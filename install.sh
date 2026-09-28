#!/usr/bin/env bash
# BPA Portal — Linux installer.
#
#   sudo ./install.sh                      install + run as a systemd service on 0.0.0.0:5057
#   sudo ./install.sh --port 8080          use a different port
#   sudo ./install.sh --host 127.0.0.1     restrict to this machine only
#   sudo ./install.sh --no-service         copy files only, no systemd unit
#   sudo ./install.sh --uninstall          remove everything except stored data
#        ./install.sh --user               per-user install, no root required
#
# The portal has NO authentication. Installing on 0.0.0.0 exposes the upload
# form and every stored report to anyone who can reach the port.

set -euo pipefail

APP_NAME="BPA Portal"
BIN_NAME="BPA Portal"
SERVICE="bpa-portal"
HOST="0.0.0.0"
PORT="5057"
MODE="system"
MAKE_SERVICE=1
UNINSTALL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)       HOST="$2"; shift 2 ;;
    --port)       PORT="$2"; shift 2 ;;
    --user)       MODE="user"; shift ;;
    --no-service) MAKE_SERVICE=0; shift ;;
    --uninstall)  UNINSTALL=1; shift ;;
    -h|--help)    sed -n '2,14p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *)            echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

HERE="$(cd "$(dirname "$0")" && pwd)"

if [[ "$MODE" == "system" ]]; then
  INSTALL_DIR="/opt/bpa-portal"
  DATA_DIR="/var/lib/bpa-portal"
  UNIT_DIR="/etc/systemd/system"
  SVC_USER="bpa-portal"
  SYSTEMCTL=(systemctl)
  if [[ $EUID -ne 0 ]]; then
    echo "ERROR: system install needs root. Use: sudo $0 $*" >&2
    echo "       (or run a per-user install with: $0 --user)" >&2
    exit 1
  fi
else
  INSTALL_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/bpa-portal"
  DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/BPA Portal"
  UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  SVC_USER=""
  SYSTEMCTL=(systemctl --user)
fi

have_systemd() {
  command -v systemctl >/dev/null 2>&1 || return 1
  [[ -d /run/systemd/system ]] || return 1
  # A user install also needs a running user manager (absent in containers,
  # over plain su, and on hosts without lingering enabled).
  if [[ "$MODE" == "user" ]]; then
    systemctl --user show-environment >/dev/null 2>&1 || return 1
  fi
  return 0
}

# ---------------------------------------------------------------- uninstall --
if [[ "$UNINSTALL" -eq 1 ]]; then
  echo "==> Uninstalling BPA Portal ($MODE)"
  if have_systemd; then
    "${SYSTEMCTL[@]}" disable --now "$SERVICE" 2>/dev/null || true
    "${SYSTEMCTL[@]}" daemon-reload 2>/dev/null || true
  fi
  # Remove the unit file whether or not systemd is usable here — otherwise an
  # orphaned unit is left behind on hosts without a running user manager.
  if [[ -f "$UNIT_DIR/${SERVICE}.service" ]]; then
    rm -f "$UNIT_DIR/${SERVICE}.service"
    echo "    removed $UNIT_DIR/${SERVICE}.service"
  fi
  rm -rf "$INSTALL_DIR"
  rm -f "${HOME:-/root}/.local/bin/bpa-portal" /usr/local/bin/bpa-portal 2>/dev/null || true
  rm -f "${XDG_DATA_HOME:-$HOME/.local/share}/applications/bpa-portal.desktop" 2>/dev/null || true
  echo "    removed $INSTALL_DIR"
  echo
  echo "Stored reports were KEPT at: $DATA_DIR"
  echo "Delete them yourself if you no longer need them:  rm -rf \"$DATA_DIR\""
  exit 0
fi

# ------------------------------------------------------------------ install --
[[ -f "$HERE/$BIN_NAME" ]] || { echo "ERROR: '$BIN_NAME' not found next to this script." >&2; exit 1; }

echo "==> Installing BPA Portal ($MODE install)"
echo "    binary -> $INSTALL_DIR"
echo "    data   -> $DATA_DIR"
echo "    listen -> $HOST:$PORT"

install -d "$INSTALL_DIR"
install -m 0755 "$HERE/$BIN_NAME" "$INSTALL_DIR/$BIN_NAME"
for extra in Quickstart.txt README.md; do
  [[ -f "$HERE/$extra" ]] && install -m 0644 "$HERE/$extra" "$INSTALL_DIR/$extra"
done

# Dedicated unprivileged account for the system service.
if [[ "$MODE" == "system" ]]; then
  if ! id -u "$SVC_USER" >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SVC_USER"
    echo "    created service account: $SVC_USER"
  fi
  install -d -o "$SVC_USER" -g "$SVC_USER" -m 0750 "$DATA_DIR"
  LINK=/usr/local/bin/bpa-portal
else
  install -d "$DATA_DIR"
  LINK="$HOME/.local/bin/bpa-portal"
  install -d "$(dirname "$LINK")"
fi

# Convenience wrapper so the name has no space in it.
cat > "$LINK" <<EOF
#!/usr/bin/env bash
exec "$INSTALL_DIR/$BIN_NAME" "\$@"
EOF
chmod 0755 "$LINK"
echo "    command -> $LINK"

# -------------------------------------------------------------------- service --
if [[ "$MAKE_SERVICE" -eq 1 ]]; then
  if ! have_systemd; then
    echo "    systemd not available — skipping service (run '$LINK' manually)"
    MAKE_SERVICE=0
  else
    install -d "$UNIT_DIR"
    {
      echo "[Unit]"
      echo "Description=BPA Portal — Palo Alto Networks Best Practice Assessment portal"
      echo "After=network-online.target"
      echo "Wants=network-online.target"
      echo
      echo "[Service]"
      echo "Type=simple"
      echo "ExecStart=\"$INSTALL_DIR/$BIN_NAME\" --host $HOST --port $PORT --no-browser"
      # Quoted: the user-mode data dir contains a space and systemd would
      # otherwise split the assignment at it.
      echo "Environment=\"BPA_DATA_DIR=$DATA_DIR\""
      echo "Restart=on-failure"
      echo "RestartSec=5"
      [[ -n "$SVC_USER" ]] && { echo "User=$SVC_USER"; echo "Group=$SVC_USER"; }
      echo "NoNewPrivileges=true"
      echo "PrivateTmp=true"
      echo "ProtectSystem=strict"
      echo "ProtectHome=true"
      echo "ReadWritePaths=\"$DATA_DIR\""
      echo
      echo "[Install]"
      if [[ "$MODE" == "system" ]]; then echo "WantedBy=multi-user.target"; else echo "WantedBy=default.target"; fi
    } > "$UNIT_DIR/${SERVICE}.service"

    # `enable --now` does NOT restart a service that is already running, so a
    # reinstall with different --host/--port would leave the OLD bind in place
    # while reporting success — dangerous when the point was to restrict access.
    # Always restart explicitly after rewriting the unit.
    if "${SYSTEMCTL[@]}" daemon-reload 2>/dev/null \
       && "${SYSTEMCTL[@]}" enable "$SERVICE" 2>/dev/null \
       && "${SYSTEMCTL[@]}" restart "$SERVICE" 2>/dev/null; then
      echo "    service -> ${SERVICE}.service (enabled, restarted on $HOST:$PORT)"
    else
      echo "    NOTE: could not register the systemd service (no usable systemd here)."
      echo "          Unit written to $UNIT_DIR/${SERVICE}.service"
      echo "          Start manually instead:  $LINK --host $HOST --port $PORT"
      MAKE_SERVICE=0
    fi
  fi
fi

# ------------------------------------------------------------------ summary --
sleep 1
echo
echo "===================================================================="
echo " BPA Portal installed"
echo "===================================================================="
if [[ "$HOST" == "0.0.0.0" || "$HOST" == "*" ]]; then
  echo " Reachable at:"
  echo "   http://127.0.0.1:$PORT/"
  # List the addresses other machines would actually use.
  if command -v hostname >/dev/null 2>&1; then
    for ip in $(hostname -I 2>/dev/null); do
      case "$ip" in *:*) echo "   http://[$ip]:$PORT/" ;; *) echo "   http://$ip:$PORT/" ;; esac
    done
  fi
  echo
  echo " WARNING: no authentication. Anyone who can reach port $PORT can"
  echo "          upload configs, read every report, and delete them."
  echo "          Restrict with: sudo $0 --host 127.0.0.1"
  if command -v ufw >/dev/null 2>&1; then
    echo "          Allow through ufw:  sudo ufw allow $PORT/tcp"
  elif command -v firewall-cmd >/dev/null 2>&1; then
    echo "          Allow through firewalld:"
    echo "            sudo firewall-cmd --add-port=$PORT/tcp --permanent && sudo firewall-cmd --reload"
  fi
else
  echo " Reachable at: http://$HOST:$PORT/   (this machine only)"
fi
echo
echo " Data:  $DATA_DIR"
if [[ "$MAKE_SERVICE" -eq 1 ]]; then
  echo
  echo " Service control:"
  echo "   ${SYSTEMCTL[*]} status $SERVICE"
  echo "   ${SYSTEMCTL[*]} restart $SERVICE"
  echo "   journalctl -u $SERVICE -f"
else
  echo
  echo " Start it with:  $LINK"
fi
echo " Uninstall:  sudo $0 --uninstall"
echo "===================================================================="
