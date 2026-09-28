import json
import os
import sqlite3
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
from flask import (
    Flask, abort, g, jsonify, render_template,
    request, send_file, url_for, redirect,
)
from werkzeug.utils import secure_filename

from .report_summary import summarize


TOKEN_URL = "https://auth.apps.paloaltonetworks.com/oauth2/access_token"
BASE_URL = "https://api.strata.paloaltonetworks.com"
INITIATE_UPLOAD_URL = f"{BASE_URL}/posture/checks/v1/reports/config-file-upload"

APP_NAME = "BPA Portal"
APP_AUTHOR = "PaloAltoNetworks"


def resource_path(rel: str) -> Path:
    """Resolve a bundled resource (templates/static) — works under PyInstaller too."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / rel
    return Path(__file__).resolve().parent / rel


def user_data_dir() -> Path:
    """Per-user writable directory for the SQLite DB and reports."""
    override = os.environ.get("BPA_DATA_DIR")
    if override:
        return Path(override)
    try:
        from platformdirs import user_data_dir as _udd
        return Path(_udd(APP_NAME, APP_AUTHOR))
    except ImportError:
        # Mirror platformdirs' layout exactly. Any divergence here would point a
        # platformdirs-less run at a different directory and "lose" the database.
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Application Support" / APP_NAME
        if os.name == "nt":
            base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
            return Path(base) / APP_AUTHOR / APP_NAME
        xdg = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg) if xdg else Path.home() / ".local" / "share"
        return base / APP_NAME


DATA_DIR = user_data_dir()
REPORTS_DIR = DATA_DIR / "reports"
DB_PATH = DATA_DIR / "bpa.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(
    __name__,
    template_folder=str(resource_path("templates")),
    static_folder=str(resource_path("static")),
)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024
app.secret_key = os.environ.get("BPA_SECRET_KEY") or os.urandom(32).hex()

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def get_db():
    db = getattr(g, "_db", None)
    if db is None:
        db = g._db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
    return db


@app.teardown_appcontext
def close_db(_exc):
    db = getattr(g, "_db", None)
    if db is not None:
        db.close()


def init_db():
    with sqlite3.connect(DB_PATH) as db:
        existing = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='reports'"
        ).fetchone()
        if existing:
            cols = {row[1] for row in db.execute("PRAGMA table_info(reports)")}
            if "user_id" in cols:
                # Migrate from the old auth-based schema → single-user.
                has_customer = "customer_name" in cols
                cust_sel = "customer_name" if has_customer else "NULL"
                db.executescript(
                    """
                    CREATE TABLE reports_new (
                        id TEXT PRIMARY KEY,
                        customer_name TEXT,
                        tsg_id TEXT,
                        filename TEXT,
                        status TEXT NOT NULL,
                        error TEXT,
                        report_path TEXT,
                        created_at TEXT NOT NULL,
                        completed_at TEXT
                    );
                    """
                )
                db.execute(
                    f"INSERT INTO reports_new "
                    f"(id, customer_name, tsg_id, filename, status, error, report_path, created_at, completed_at) "
                    f"SELECT id, {cust_sel}, tsg_id, filename, status, error, report_path, created_at, completed_at FROM reports"
                )
                db.executescript(
                    """
                    DROP TABLE reports;
                    ALTER TABLE reports_new RENAME TO reports;
                    DROP TABLE IF EXISTS users;
                    """
                )
            elif "customer_name" not in cols:
                db.execute("ALTER TABLE reports ADD COLUMN customer_name TEXT")
        else:
            db.executescript(
                """
                CREATE TABLE reports (
                    id TEXT PRIMARY KEY,
                    customer_name TEXT,
                    tsg_id TEXT,
                    filename TEXT,
                    status TEXT NOT NULL,
                    error TEXT,
                    report_path TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
                """
            )


init_db()


@app.route("/")
def index():
    rows = get_db().execute(
        "SELECT id, customer_name, tsg_id, filename, status, error, created_at, completed_at "
        "FROM reports ORDER BY created_at DESC"
    ).fetchall()
    return render_template("index.html", reports=rows)


@app.route("/run", methods=["POST"])
def run_assessment():
    customer_name = request.form.get("customer_name", "").strip() or None
    client_id = request.form.get("client_id", "").strip()
    client_secret = request.form.get("client_secret", "").strip()
    file = request.files.get("config_file")

    if not client_id or not client_secret or not file or not file.filename:
        return jsonify({"error": "Client ID, Client Secret, and config file are required."}), 400

    try:
        tsg_id = client_id.split("@")[1].split(".")[0]
    except IndexError:
        return jsonify({"error": "Client ID format is invalid (expected user@<tsg>.iam.panserviceaccount.com)."}), 400

    job_id = uuid.uuid4().hex
    filename = secure_filename(file.filename) or "config.xml"
    xml_bytes = file.read()

    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            "INSERT INTO reports (id, customer_name, tsg_id, filename, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, customer_name, tsg_id, filename, "PENDING",
             datetime.now(timezone.utc).isoformat()),
        )

    with JOBS_LOCK:
        JOBS[job_id] = {"steps": [], "status": "PENDING"}

    t = threading.Thread(
        target=run_job,
        args=(job_id, client_id, client_secret, tsg_id, xml_bytes, filename),
        daemon=True,
    )
    t.start()
    return jsonify({"job_id": job_id})


@app.route("/jobs/<job_id>")
def job_status(job_id):
    row = get_db().execute(
        "SELECT id, status, error, report_path FROM reports WHERE id = ?",
        (job_id,),
    ).fetchone()
    if not row:
        abort(404)
    with JOBS_LOCK:
        live = JOBS.get(job_id, {})
        steps = list(live.get("steps", []))
    return jsonify({
        "id": row["id"],
        "status": row["status"],
        "error": row["error"],
        "has_report": bool(row["report_path"]),
        "steps": steps,
    })


@app.route("/reports/<report_id>/download")
def download_report(report_id):
    row = get_db().execute(
        "SELECT report_path, filename FROM reports WHERE id = ?",
        (report_id,),
    ).fetchone()
    if not row or not row["report_path"]:
        abort(404)
    path = Path(row["report_path"])
    if not path.exists():
        abort(404)
    download_name = f"bpa_report_{report_id}.json"
    return send_file(path, as_attachment=True, download_name=download_name, mimetype="application/json")


@app.route("/reports/<report_id>/view")
def view_report(report_id):
    row = get_db().execute(
        "SELECT id, customer_name, tsg_id, filename, created_at, completed_at, report_path "
        "FROM reports WHERE id = ?",
        (report_id,),
    ).fetchone()
    if not row or not row["report_path"]:
        abort(404)
    path = Path(row["report_path"])
    if not path.exists():
        abort(404)
    with open(path, "rb") as f:
        data = json.loads(f.read())
    summary = summarize(data)
    meta = {
        "id": row["id"],
        "customer_name": row["customer_name"],
        "tsg_id": row["tsg_id"],
        "filename": row["filename"],
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
    }
    return render_template("report.html", summary=summary, meta=meta)


@app.route("/reports/<report_id>/executive")
def executive_report(report_id):
    row = get_db().execute(
        "SELECT id, customer_name, tsg_id, filename, created_at, completed_at, report_path "
        "FROM reports WHERE id = ?",
        (report_id,),
    ).fetchone()
    if not row or not row["report_path"]:
        abort(404)
    path = Path(row["report_path"])
    if not path.exists():
        abort(404)
    with open(path, "rb") as f:
        data = json.loads(f.read())
    summary = summarize(data)
    meta = {
        "id": row["id"],
        "customer_name": row["customer_name"],
        "tsg_id": row["tsg_id"],
        "filename": row["filename"],
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
    }
    return render_template("executive.html", summary=summary, meta=meta)


@app.route("/reports/<report_id>/raw")
def raw_report(report_id):
    row = get_db().execute(
        "SELECT report_path FROM reports WHERE id = ?",
        (report_id,),
    ).fetchone()
    if not row or not row["report_path"]:
        abort(404)
    path = Path(row["report_path"])
    if not path.exists():
        abort(404)
    with open(path, "rb") as f:
        return app.response_class(f.read(), mimetype="application/json")


@app.route("/reports/<report_id>/delete", methods=["POST"])
def delete_report(report_id):
    row = get_db().execute(
        "SELECT report_path FROM reports WHERE id = ?",
        (report_id,),
    ).fetchone()
    if not row:
        abort(404)
    if row["report_path"]:
        try:
            Path(row["report_path"]).unlink(missing_ok=True)
        except OSError:
            pass
    with sqlite3.connect(DB_PATH) as db:
        db.execute("DELETE FROM reports WHERE id = ?", (report_id,))
    return redirect(url_for("index"))


def log_step(job_id, message):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id]["steps"].append(f"[{ts}] {message}")


def update_status(job_id, status, error=None, report_path=None):
    with sqlite3.connect(DB_PATH) as db:
        if status in ("COMPLETED", "FAILED"):
            db.execute(
                "UPDATE reports SET status = ?, error = ?, report_path = ?, completed_at = ? WHERE id = ?",
                (status, error, report_path, datetime.now(timezone.utc).isoformat(), job_id),
            )
        else:
            db.execute("UPDATE reports SET status = ? WHERE id = ?", (status, job_id))
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id]["status"] = status


def run_job(job_id, client_id, client_secret, tsg_id, xml_bytes, filename):
    try:
        log_step(job_id, "[1/5] Requesting access token…")
        token_resp = requests.post(
            TOKEN_URL,
            auth=(client_id, client_secret),
            data={"grant_type": "client_credentials", "scope": f"tsg_id:{tsg_id}"},
            timeout=30,
        )
        if token_resp.status_code != 200:
            raise RuntimeError(f"Token request failed: HTTP {token_resp.status_code} {token_resp.text[:200]}")
        token = token_resp.json().get("access_token")
        if not token:
            raise RuntimeError("Token response missing access_token.")
        log_step(job_id, "Token acquired.")

        update_status(job_id, "INITIATING")
        log_step(job_id, "[2/5] Initiating upload session…")
        init_resp = requests.post(
            INITIATE_UPLOAD_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={},
            timeout=30,
        )
        if init_resp.status_code not in (200, 201):
            raise RuntimeError(f"Initiate failed: HTTP {init_resp.status_code} {init_resp.text[:200]}")
        upload_url = init_resp.json().get("upload_url")
        tracking_uri = init_resp.headers.get("Location")
        if not upload_url or not tracking_uri:
            raise RuntimeError("Initiate response missing upload_url or Location header.")
        log_step(job_id, "Upload URL secured.")

        update_status(job_id, "UPLOADING")
        log_step(job_id, f"[3/5] Uploading {filename} ({len(xml_bytes)} bytes)…")
        # NOTE: the body is sent UNCOMPRESSED while still declaring
        # Content-Encoding: gzip. That looks wrong but is deliberate — the header
        # is part of the presigned URL's signature, so it must be present
        # verbatim or the PUT is rejected; the storage backend records it as
        # metadata without validating the body. Do not "fix" this by re-adding
        # gzip.compress() — that regresses the upload.
        put_resp = requests.put(
            upload_url,
            headers={"Content-Type": "text/plain", "Content-Encoding": "gzip"},
            data=xml_bytes,
            timeout=120,
        )
        if put_resp.status_code not in (200, 201, 202):
            raise RuntimeError(f"Upload failed: HTTP {put_resp.status_code} {put_resp.text[:1000]}")
        log_step(job_id, "File uploaded.")

        update_status(job_id, "PROCESSING")
        log_step(job_id, "[4/5] Polling for assessment results…")

        if "/v1/" not in tracking_uri:
            tracking_uri = tracking_uri.replace("/reports/", "/v1/reports/")
        if not tracking_uri.endswith("/bpa-result"):
            tracking_uri = tracking_uri.rstrip("/") + "/bpa-result"
        poll_url = f"{BASE_URL}{tracking_uri}" if tracking_uri.startswith("/") else tracking_uri

        attempt = 1
        report_url = None
        while True:
            log_step(job_id, f"  Attempt {attempt}: checking status…")
            poll_resp = requests.get(
                poll_url,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                timeout=30,
            )
            if poll_resp.status_code == 202:
                log_step(job_id, "    Server accepted; waiting 20s…")
                time.sleep(20)
                attempt += 1
                continue
            if poll_resp.status_code != 200:
                log_step(job_id, f"    Unexpected HTTP {poll_resp.status_code}; retrying in 20s…")
                time.sleep(20)
                attempt += 1
                continue

            data = poll_resp.json()
            job_state = (data.get("status") or "").upper()
            if job_state in ("PENDING", "RUNNING", "PROCESSING", "IN_PROGRESS", "UPLOAD_COMPLETE", "QUEUED"):
                log_step(job_id, f"    {job_state} — {data.get('message', 'working')}; waiting 20s…")
                time.sleep(20)
                attempt += 1
                continue
            if job_state in ("FAILED", "ERROR"):
                raise RuntimeError(f"Server reported job failure: {json.dumps(data)[:300]}")
            if job_state in ("COMPLETED", "SUCCESS"):
                result = data.get("result", {})
                report_url = result.get("report_url") or result.get("custom_check_url")
                if not report_url:
                    raise RuntimeError(f"Job completed but no download URL found. Full response: {json.dumps(data)}")
                break
            log_step(job_id, f"    Unknown state '{job_state}'; waiting 20s…")
            time.sleep(20)
            attempt += 1

        log_step(job_id, "[5/5] Downloading final report…")
        report_resp = requests.get(report_url, timeout=120)
        if report_resp.status_code != 200:
            raise RuntimeError(f"Download failed: HTTP {report_resp.status_code}")

        report_path = REPORTS_DIR / f"{job_id}.json"
        with open(report_path, "wb") as f:
            f.write(report_resp.content)
        log_step(job_id, f"Saved report ({len(report_resp.content)} bytes).")
        update_status(job_id, "COMPLETED", report_path=str(report_path))
    except Exception as e:
        log_step(job_id, f"ERROR: {e}")
        update_status(job_id, "FAILED", error=str(e))
