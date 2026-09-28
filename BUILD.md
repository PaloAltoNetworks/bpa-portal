# BPA Portal — build & distribution

## TL;DR: what "installs universally"?

There is no single file that is both **self-contained** and **runs everywhere** —
PyInstaller does not cross-compile, so a native binary is always tied to one
OS/architecture family. Pick the trade-off that matches your audience:

| Artifact | Runs on | Needs Python? | Built from |
|---|---|---|---|
| **`bpa_portal-1.1.0-py3-none-any.whl`** | **macOS (Intel + Apple Silicon), Windows, Linux — any arch** | Yes, 3.9+ | **One build, any host** |
| `BPA-Portal-1.1.0-macos-universal2.dmg` | macOS 11+, Intel **and** Apple Silicon | No | a Mac |
| `BPA-Portal-1.1.0-windows-x64.zip` | Windows 10/11 x64 | No | a Windows box |
| `BPA-Portal-1.1.0-linux-x86_64.tar.gz` | Linux x86_64, glibc ≥ build host | No | a Linux box |

**The wheel is the universal one.** It is the only artifact you can build here,
once, and hand to any customer on any platform.

---

## The universal package (recommended)

Build it on any OS — the output is identical:

```bash
./build.sh --wheel          # macOS / Linux
.\build.ps1 -WheelOnly      # Windows
```

Output: `release/bpa_portal-1.1.0-py3-none-any.whl`. The `py3-none-any` tag is
what makes it universal: pure Python, no compiled extensions, no architecture.
Both build scripts **fail the build** if the tag ever becomes platform-specific.

### Customer install (identical on macOS, Windows, Linux)

```bash
pipx install bpa_portal-1.1.0-py3-none-any.whl
bpa-portal
```

`pipx` gives each install its own isolated venv and puts `bpa-portal` on PATH.
If they don't have pipx:

```bash
python3 -m pip install --user bpa_portal-1.1.0-py3-none-any.whl
python3 -m bpa_portal
```

The app listens on **all interfaces** (IPv4 + IPv6) on port 5057 by default
and opens the local browser to `http://127.0.0.1:5057/`. Restrict it with
`--host 127.0.0.1` (or `BPA_HOST`). There is no authentication — see the
Security section of `README.md`.

**Requirement:** Python 3.9 or newer. macOS ships an adequate Python; Windows
users may need one from python.org or the Microsoft Store.

---

## Native binaries (no Python needed)

Use these when customers cannot install Python. **Each must be built on its
target OS** — there is no way around this.

| OS      | Command       | Output                                             |
|---------|---------------|----------------------------------------------------|
| macOS   | `./build.sh`  | `release/BPA-Portal-1.1.0-macos-universal2.dmg`    |
| Linux   | `./build.sh`  | `release/BPA-Portal-1.1.0-linux-<arch>.tar.gz`     |
| Windows | `.\build.ps1` | `release\BPA-Portal-1.1.0-windows-<arch>.zip`      |

Each script creates a venv, installs deps + PyInstaller, bundles, and packages
the result. Every run also emits the universal wheel alongside the binary.

The Linux tarball ships:

| File | Purpose |
|---|---|
| `BPA Portal` | the self-contained binary |
| `install.sh` | installer — systemd service, service account, bind config |
| `README.md` | full user documentation |
| `Quickstart.txt` | short install/run/security summary |
| `bpa-portal.desktop` + `install-desktop-entry.sh` | optional desktop launcher |

`install.sh` and `README.md` are copied from the repo root at package time, so
edit them there — not in `release/`.

Override the version with `BPA_VERSION=1.2.3 ./build.sh`.

### Getting all four from one commit — CI

`.github/workflows/release.yml` builds the wheel plus all three native
installers on GitHub-hosted macOS, Windows and Linux runners and attaches them
to a GitHub Release. This is how you produce a full cross-platform release
without owning a Mac and a Windows machine:

```bash
git tag v1.1.0 && git push --tags
```

The workflow **fails the build** if the macOS bundle is not universal2, so an
Intel-incompatible release cannot ship by accident.

### macOS: universal2 is mandatory

`bpa.spec` sets `target_arch="universal2"` so one `.app` runs on both Apple
Silicon and Intel. An arm64-only build **fails silently on Intel Macs** — the
previous 1.0.0 build had exactly this defect.

This requires the build Python to itself be universal2 (the python.org
installer and `actions/setup-python` both are; Homebrew's is not). `build.sh`
runs `lipo -archs` after building and warns loudly if the result is thin.
Escape hatch for a local single-arch build: `BPA_MAC_ARCH=native ./build.sh`.

### Linux: mind the glibc floor

A PyInstaller binary requires **at least** the glibc of the machine that built
it — compatibility runs forward only. Building on Ubuntu 24.04 produces a
binary that will not start on RHEL 8 or Ubuntu 20.04.

Build inside an old-glibc image to widen support (this is what CI does):

```bash
docker run --rm -v "$PWD:/src" -w /src quay.io/pypa/manylinux_2_28_x86_64 \
  bash -c '/opt/python/cp311-cp311/bin/python -m venv .venv && BPA_VENV=.venv ./build.sh'
```

`build.sh` prints the resulting glibc floor so the support matrix is a fact,
not a guess.

Architecture is detected with `uname -m` and written into the artifact name —
building on ARM yields `...-linux-arm64.tar.gz`, not a mislabeled `x86_64` one.

---

## Customer install notes (native builds)

- **macOS** — open the `.dmg`, drag **BPA Portal** to **Applications**, eject.
  First launch: right-click in `/Applications` → **Open** (Gatekeeper, unsigned).
- **Windows** — extract the ZIP, run `BPA Portal.exe`. SmartScreen warns once:
  **More info → Run anyway**.
- **Linux** — extract the tarball, run `./"BPA Portal"`. For a desktop launcher
  run `./install-desktop-entry.sh`, which generates the `.desktop` file against
  the actual install path.

In all cases the app listens on all interfaces on port 5057 and opens the
local browser to `http://127.0.0.1:5057/`. If
the browser does not open — headless Linux, SSH, WSL, or a Mac `.app` with no
console — the URL is also written to `portal-url.txt` in the data directory, and
macOS shows it in a dialog.

## Where data is stored

| OS      | Location                                          |
|---------|---------------------------------------------------|
| macOS   | `~/Library/Application Support/BPA Portal/`       |
| Linux   | `~/.local/share/BPA Portal/`                      |
| Windows | `%LOCALAPPDATA%\PaloAltoNetworks\BPA Portal\`     |

Override with `BPA_DATA_DIR=/some/path`. SQLite (`bpa.db`), report JSONs
(`reports/<id>.json`) and `portal-url.txt` live there. These paths come from
`platformdirs`; the no-platformdirs fallback in `app.py` mirrors them exactly so
a database can never be orphaned.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
bpa-portal            # or: python -m bpa_portal
```

Layout: the app is the `bpa_portal/` package (`app.py`, `launcher.py`,
`report_summary.py`, `templates/`, `static/`). `run_portal.py` is the
PyInstaller entry point; `launcher.py` at the root is a back-compat shim.
`bpa.py` is the standalone pre-web CLI and is not part of the package.

## Notes

- Builds are **unsigned**. SmartScreen / Gatekeeper warn on first launch.
  Removing those warnings needs an Apple Developer ID (~$99/yr + notarization)
  and an Authenticode cert for Windows. The wheel has no such warning.
- `static/` contains **only** web assets. Source files were previously copied
  there and served publicly at `/static/app.py`; that is fixed and must not
  regress — Flask serves that directory verbatim.
- **Never commit `bpa.csv`** — it holds service-account credentials. It is in
  `.gitignore`.
