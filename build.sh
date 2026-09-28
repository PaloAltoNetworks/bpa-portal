#!/usr/bin/env bash
# Build a self-contained, ready-to-distribute BPA Portal package for the
# current host OS. Run on Linux for a tarball, on macOS for a .dmg.
#
#   ./build.sh                # full build + package
#   ./build.sh --no-package   # binary only, skip the installer step
#   ./build.sh --wheel        # universal wheel only (runs on ANY host OS)
#
# Prereqs: python3 (3.9+) and either an active venv or the system Python.
#
# NOTE: PyInstaller does not cross-compile. The native binary produced here
# runs only on this OS/arch family. For an artifact that installs everywhere
# from a single build, use --wheel (see BUILD.md).

set -euo pipefail

cd "$(dirname "$0")"

PACKAGE=1
WHEEL_ONLY=0
case "${1:-}" in
  --no-package) PACKAGE=0 ;;
  --wheel)      WHEEL_ONLY=1 ;;
  "")           ;;
  *)            echo "Unknown option: $1"; exit 2 ;;
esac

VENV_DIR="${BPA_VENV:-.venv}"
APP_NAME="BPA Portal"
VERSION="${BPA_VERSION:-1.1.0}"

uname_s="$(uname -s)"
case "$uname_s" in
  Darwin) HOST_OS="macos" ;;
  Linux)  HOST_OS="linux" ;;
  *)      echo "Unsupported OS: $uname_s (use build.ps1 on Windows)"; exit 1 ;;
esac

# Real host architecture — never hardcode this into artifact names.
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|amd64) ARCH="x86_64" ;;
  aarch64|arm64) ARCH="arm64" ;;
esac

echo "==> Host OS: $HOST_OS ($ARCH)"

# 1) Ensure venv with build deps
if [[ ! -d "$VENV_DIR" ]]; then
  echo "==> Creating virtualenv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"

echo "==> Installing dependencies"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt pyinstaller build

mkdir -p release

# --- Universal wheel: one artifact, installs on every OS and architecture ---
build_wheel() {
  echo "==> Building universal wheel (py3-none-any)"
  rm -rf build/wheel dist/*.whl dist/*.tar.gz 2>/dev/null || true
  python -m build --outdir dist >/dev/null
  local whl
  whl="$(ls dist/*.whl | head -1)"
  # Assert the wheel really is platform independent.
  case "$whl" in
    *-py3-none-any.whl) ;;
    *) echo "ERROR: wheel is not universal: $whl"; exit 1 ;;
  esac
  cp "$whl" release/
  cp dist/*.tar.gz release/ 2>/dev/null || true
  echo "    -> release/$(basename "$whl")  (installs on macOS/Windows/Linux, any arch)"
}

if [[ "$WHEEL_ONLY" -eq 1 ]]; then
  build_wheel
  echo "Done."
  exit 0
fi

# 2) Clean and build the native binary
echo "==> Running PyInstaller"
rm -rf build dist
pyinstaller --clean --noconfirm bpa.spec >/dev/null
echo "    Built $(ls dist | tr '\n' ' ')"

# 3) Package, unless skipped
if [[ "$PACKAGE" -eq 0 ]]; then
  echo "==> Skipping packaging (per --no-package). Artifacts in ./dist/"
  exit 0
fi

rm -rf release/*
build_wheel

case "$HOST_OS" in
  macos)
    APP_BUNDLE="dist/${APP_NAME}.app"
    [[ -d "$APP_BUNDLE" ]] || { echo "Missing $APP_BUNDLE"; exit 1; }

    # Verify the bundle really is universal2 — an arm64-only build will not
    # launch on Intel Macs, and that failure is silent for the customer.
    MACHO="$APP_BUNDLE/Contents/MacOS/${APP_NAME}"
    if [[ -f "$MACHO" ]]; then
      ARCHS="$(lipo -archs "$MACHO" 2>/dev/null || echo unknown)"
      echo "    Binary architectures: $ARCHS"
      if [[ "$ARCHS" != *"x86_64"* || "$ARCHS" != *"arm64"* ]]; then
        echo "    WARNING: not universal2 (got: $ARCHS)."
        echo "             Intel Macs will NOT be able to run this build."
        echo "             Install a universal2 Python (python.org installer) and rebuild."
        MAC_SUFFIX="$ARCHS"
      else
        MAC_SUFFIX="universal2"
      fi
    else
      MAC_SUFFIX="$ARCH"
    fi

    # Strip quarantine attr from the freshly-built bundle (set by some tools).
    xattr -cr "$APP_BUNDLE" 2>/dev/null || true

    DMG="release/BPA-Portal-${VERSION}-macos-${MAC_SUFFIX}.dmg"
    STAGE="$(mktemp -d)"
    mkdir -p "$STAGE/.background"
    cp -R "$APP_BUNDLE" "$STAGE/"
    ln -s /Applications "$STAGE/Applications"

    cat > "$STAGE/Quickstart.txt" <<'EOF'
BPA Portal — Install

1. Drag "BPA Portal" onto the "Applications" shortcut.
2. Eject this disk image (right-click in Finder → Eject).
3. Open "Applications" → right-click "BPA Portal" → Open.
   (First launch only: macOS asks to confirm an unsigned app.
    After confirming once, double-click works normally.)

The app opens your browser to http://127.0.0.1:5057/
If the browser does not open, the address is saved in:
  ~/Library/Application Support/BPA Portal/portal-url.txt

Data is stored at:  ~/Library/Application Support/BPA Portal/
Quit from the Dock to stop the server.
EOF

    echo "==> Creating $DMG"
    hdiutil create -volname "BPA Portal" \
      -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
    rm -rf "$STAGE"
    echo "    -> $DMG"
    ;;

  linux)
    BIN="dist/${APP_NAME}"
    [[ -f "$BIN" ]] || { echo "Missing $BIN"; exit 1; }

    # Report the glibc floor. PyInstaller onefile packs libpython and the
    # extension modules inside the executable, so the real requirement is the
    # build host's glibc, not just what the outer ELF stub imports.
    HOST_GLIBC="$(ldd --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+$' || true)"
    if [[ -n "$HOST_GLIBC" ]]; then
      echo "    Built against glibc $HOST_GLIBC"
      echo "    -> this binary will NOT start on distros older than that."
      echo "    -> for wider support build in quay.io/pypa/manylinux_2_28_x86_64 (see BUILD.md)"
    fi

    TGZ="release/BPA-Portal-${VERSION}-linux-${ARCH}.tar.gz"
    STAGE="$(mktemp -d)/BPA-Portal"
    mkdir -p "$STAGE"
    cp "$BIN" "$STAGE/"
    chmod +x "$STAGE/${APP_NAME}"

    # Ship the installer and full docs alongside the binary.
    install -m 0755 install.sh "$STAGE/install.sh"
    install -m 0644 README.md  "$STAGE/README.md"

    cat > "$STAGE/Quickstart.txt" <<EOF
BPA Portal ${VERSION} — Quickstart
==================================

INSTALL (recommended)

  sudo ./install.sh

  Installs to /opt/bpa-portal, creates a systemd service that starts on
  boot, and listens on ALL network interfaces on port 5057.

  Options:
    sudo ./install.sh --port 8080        different port
    sudo ./install.sh --host 127.0.0.1   this machine only
    sudo ./install.sh --no-service       copy files only
         ./install.sh --user             per-user install, no root
    sudo ./install.sh --uninstall        remove (keeps your reports)

RUN WITHOUT INSTALLING

  ./"BPA Portal"                     all interfaces, port 5057
  ./"BPA Portal" --host 127.0.0.1    this machine only
  ./"BPA Portal" --port 8080

  No Python required — the binary is self-contained.

ACCESS

  http://<this-server-ip>:5057/
  The URL is printed at startup and saved to portal-url.txt in the data dir.

>> SECURITY <<

  The portal has NO authentication. On all interfaces (the default), anyone
  who can reach port 5057 can upload configs, read every stored report
  (full firewall configuration detail), and delete them.

  Keep it on a trusted network and restrict the port at the firewall:
    sudo ufw allow from 10.0.0.0/8 to any port 5057 proto tcp

  Or bind to loopback and use an SSH tunnel:
    sudo ./install.sh --host 127.0.0.1
    ssh -L 5057:127.0.0.1:5057 user@this-server

DATA

  System install:  /var/lib/bpa-portal/
  Manual / --user: ~/.local/share/BPA Portal/
  Override with BPA_DATA_DIR=/some/path

REQUIREMENTS

  Linux x86_64, glibc 2.39+ (Ubuntu 24.04+, Fedora 39+, Debian 13+)

Full documentation: see README.md in this archive.
EOF

    # Desktop launcher for workstation use (separate from the systemd service
    # that install.sh sets up). The Exec= path contains a space, so it MUST be
    # quoted; an unquoted path makes the launcher silently fail to start.
    cat > "$STAGE/bpa-portal.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=BPA Portal
Comment=Palo Alto Networks BPA Portal
Exec="@@INSTALL_DIR@@/BPA Portal"
Icon=
Terminal=false
Categories=Network;Security;
EOF

    # Generate the entry against wherever the user actually extracted it,
    # rather than baking in the build machine's $HOME.
    cat > "$STAGE/install-desktop-entry.sh" <<'EOF'
#!/usr/bin/env bash
# Install a desktop launcher pointing at this directory.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$DEST"
sed "s|@@INSTALL_DIR@@|$HERE|" "$HERE/bpa-portal.desktop" > "$DEST/bpa-portal.desktop"
chmod +x "$HERE/BPA Portal"
echo "Installed $DEST/bpa-portal.desktop -> $HERE/BPA Portal"
EOF
    chmod +x "$STAGE/install-desktop-entry.sh"

    echo "==> Creating $TGZ"
    tar -C "$(dirname "$STAGE")" -czf "$TGZ" "$(basename "$STAGE")"
    rm -rf "$(dirname "$STAGE")"
    echo "    -> $TGZ"
    ;;
esac

echo "Done."
