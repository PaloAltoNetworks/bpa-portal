# BPA Portal

A self-hosted web portal for running Palo Alto Networks **Best Practice
Assessments**. Upload a firewall or Panorama configuration XML; the portal
submits it to the Strata posture API, tracks the assessment to completion, and
renders the result as a browsable report plus a print-ready executive summary.

---

## Contents

- [How it works](#how-it-works)
- [Install](#install)
- [Using the portal](#using-the-portal)
- [Configuration](#configuration)
- [Security](#security)
- [Where data is stored](#where-data-is-stored)
- [Service management](#service-management)
- [Troubleshooting](#troubleshooting)
- [Uninstall](#uninstall)

---

## How it works

The portal is a small Flask application served by [waitress](https://docs.pylonsproject.org/projects/waitress/),
bundled with everything it needs into a single executable. There is no external
database and no separate web server.

### The assessment pipeline

When you submit a config file, the app runs a five-stage job on a background
thread and streams progress back to the browser:

```
   Browser                  BPA Portal                 Strata Cloud
      │                          │                            │
      │ ── POST /run ──────────► │                            │
      │    (creds + config.xml)  │                            │
      │                          │ ─[1] POST oauth2/token ──► │
      │                          │ ◄──── access_token ─────── │
      │                          │                            │
      │                          │ ─[2] POST config-file-upload
      │                          │ ◄──── upload_url + Location
      │                          │                            │
      │                          │ ─[3] PUT config XML ─────► │
      │                          │                            │
      │ ◄─ GET /jobs/<id> ─────► │ ─[4] GET .../bpa-result ─► │  poll
      │    (live progress log)   │ ◄──── 202 / PENDING ────── │  every
      │                          │      … repeat …            │  20s
      │                          │ ◄──── COMPLETED + url ──── │
      │                          │                            │
      │                          │ ─[5] GET report_url ─────► │
      │                          │ ◄──── report JSON ──────── │
      │ ◄─ status COMPLETED ──── │  saved to reports/<id>.json
```

1. **Authenticate** — exchanges your service-account client ID and secret for an
   OAuth2 access token, scoped to the TSG ID parsed out of the client ID.
2. **Initiate** — asks the API for a presigned upload URL and a tracking URI.
3. **Upload** — PUTs the config XML to the presigned URL.
4. **Poll** — checks the tracking URI every 20 seconds until the assessment
   reports `COMPLETED` (or fails).
5. **Download** — fetches the finished report JSON and stores it locally.

Job state lives in a small SQLite database; the report JSON is written to disk.
The live progress log is kept in memory and is served to the browser by
`GET /jobs/<id>`, which the page polls while a job runs.

### Routes

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Report list and the upload form |
| `/run` | POST | Start an assessment; returns a `job_id` |
| `/jobs/<id>` | GET | Live status and progress log (JSON) |
| `/reports/<id>/view` | GET | Full rendered report |
| `/reports/<id>/executive` | GET | Executive summary, formatted for printing to PDF |
| `/reports/<id>/download` | GET | Original report JSON as an attachment |
| `/reports/<id>/raw` | GET | Report JSON inline |
| `/reports/<id>/delete` | POST | Delete the report and its JSON |

### Layout

```
bpa_portal/
  app.py             Flask routes, SQLite schema, the 5-stage job runner
  launcher.py        CLI parsing, bind address, waitress startup
  report_summary.py  Turns raw BPA JSON into the view model for the templates
  templates/         Jinja2 pages (index, report, executive)
  static/            CSS, Chart.js, logo
```

---

## Install

Extract the tarball, then run the installer:

```bash
tar -xzf BPA-Portal-1.1.0-linux-x86_64.tar.gz
cd BPA-Portal
sudo ./install.sh
```

That installs the binary to `/opt/bpa-portal`, creates an unprivileged
`bpa-portal` service account, registers a systemd service that starts on boot,
and listens on **all interfaces** on port 5057.

### Installer options

| Command | Effect |
|---|---|
| `sudo ./install.sh` | All interfaces, port 5057, systemd service |
| `sudo ./install.sh --port 8080` | Different port |
| `sudo ./install.sh --host 127.0.0.1` | This machine only |
| `sudo ./install.sh --no-service` | Copy files only, no systemd unit |
| `./install.sh --user` | Per-user install, no root needed |
| `sudo ./install.sh --uninstall` | Remove the app (keeps your reports) |

### Without installing

The binary is self-contained and needs no Python:

```bash
./"BPA Portal"                        # all interfaces, port 5057
./"BPA Portal" --host 127.0.0.1       # this machine only
./"BPA Portal" --port 8080
```

---

## Using the portal

Open the URL the installer prints (e.g. `http://<server-ip>:5057/`). You need a
Strata service account:

- **Client ID** — `name@<tsg-id>.iam.panserviceaccount.com`
- **Client Secret**
- **Config file** — the firewall or Panorama XML export (50 MB limit)
- **Customer name** — optional label for the report list

The TSG ID is parsed from the client ID automatically. Progress streams live as
the job runs; a typical assessment takes a few minutes. When it finishes you can
open the full report, open the executive summary (use your browser's Print → Save
as PDF), download the raw JSON, or delete it.

**Credentials are never written to disk.** They are used for the single token
exchange and held only in memory for the life of the job.

---

## Configuration

Every option has a flag and an environment variable. Flags win.

| Flag | Env var | Default | Meaning |
|---|---|---|---|
| `--host` | `BPA_HOST` | `0.0.0.0` | Interface to bind. `0.0.0.0` = all (IPv4 + IPv6) |
| `--port` | `BPA_PORT` | `5057` | Listening port |
| `--threads` | `BPA_THREADS` | `8` | Worker threads |
| `--no-browser` | `BPA_NO_BROWSER` | off | Don't open a browser on startup |
| — | `BPA_DATA_DIR` | per-OS | Override where reports and the database live |

`--host 0.0.0.0` binds both IPv4 and IPv6 on every interface. If the port is
already in use the app exits with an error rather than silently moving to a
different port, so the address you configured is the address you get.

---

## Security

**The portal has no authentication.** There is no login, no API key, no access
control of any kind. Anyone who can reach the port can:

- upload configurations and spend your Strata API quota
- read every stored report — these contain full firewall configuration detail
- delete reports

By default it listens on **all interfaces**, so that means anyone who can route
to the machine on that port.

Given that, treat the listening port as the entire security boundary:

- **Keep it on a trusted management network.** Do not expose it to the internet.
- **Restrict with the firewall** — allow only the subnets that need it:
  ```bash
  sudo ufw allow from 10.0.0.0/8 to any port 5057 proto tcp     # Debian/Ubuntu
  sudo firewall-cmd --permanent \
       --add-rich-rule='rule family=ipv4 source address=10.0.0.0/8 port port=5057 protocol=tcp accept'
  sudo firewall-cmd --reload                                     # RHEL/Fedora
  ```
- **Or bind to loopback** and reach it over an SSH tunnel:
  ```bash
  sudo ./install.sh --host 127.0.0.1
  ssh -L 5057:127.0.0.1:5057 user@server     # then browse to localhost:5057
  ```
- **Put a reverse proxy in front** (nginx/Caddy) if you need TLS and real
  authentication. Traffic to the portal is plain HTTP — credentials typed into
  the form cross the network unencrypted.

The systemd service runs as an unprivileged `bpa-portal` account with
`ProtectSystem=strict`, `ProtectHome=true`, and `NoNewPrivileges=true`; it can
write only to its data directory.

---

## Where data is stored

| OS | Location |
|---|---|
| Linux (system install) | `/var/lib/bpa-portal/` |
| Linux (manual/user) | `~/.local/share/BPA Portal/` |
| macOS | `~/Library/Application Support/BPA Portal/` |
| Windows | `%LOCALAPPDATA%\PaloAltoNetworks\BPA Portal\` |

Contents:

- `bpa.db` — SQLite job/report index
- `reports/<id>.json` — the raw report from the API (these can be large)
- `portal-url.txt` — the URL of the running instance

Override with `BPA_DATA_DIR`. Back up this directory to preserve reports.

---

## Service management

```bash
sudo systemctl status bpa-portal
sudo systemctl restart bpa-portal
sudo systemctl stop bpa-portal
sudo journalctl -u bpa-portal -f      # live logs
```

To change the bind address or port after install, re-run the installer with the
new values — it rewrites the unit and restarts the service:

```bash
sudo ./install.sh --host 127.0.0.1 --port 8443
```

---

## Troubleshooting

**"port 5057 is already in use"** — something else holds the port. Find it with
`sudo ss -ltnp | grep 5057`, then stop it or use `--port`.

**Reachable locally but not from another machine** — the bind is correct
(`sudo ss -ltnp | grep 5057` should show `0.0.0.0:5057`, not `127.0.0.1:5057`);
the block is almost certainly the firewall. See [Security](#security).

**Browser doesn't open** — expected on a headless server. The URL is printed at
startup and saved to `portal-url.txt` in the data directory.

**"Client ID format is invalid"** — the client ID must look like
`name@<tsg-id>.iam.panserviceaccount.com`; the TSG ID is parsed from it.

**Job fails at "Requesting access token"** — bad credentials, or the service
account lacks posture-assessment scope for that TSG.

**Job polls forever** — large configs take a while. The log line updates each
attempt; check `journalctl -u bpa-portal -f` for the API's reported state.

**Binary won't start: `GLIBC_2.39 not found`** — this build requires glibc 2.39
(Ubuntu 24.04+, Fedora 39+, Debian 13+). See `BUILD.md` for building against an
older glibc.

---

## Uninstall

```bash
sudo ./install.sh --uninstall
```

Stops and removes the service, the binary, and the launcher. **Your reports are
deliberately kept** — the command prints their location. Remove them yourself
when you're ready.

---

## Building from source

See [BUILD.md](BUILD.md) — covers the universal Python wheel, native builds for
macOS/Windows/Linux, the macOS universal2 requirement, and the Linux glibc floor.
