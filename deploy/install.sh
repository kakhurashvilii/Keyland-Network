#!/usr/bin/env bash
# Keyland Network installer for Kali NetHunter (Debian-based). Run as root:
#   sudo bash deploy/install.sh
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Run as root: sudo bash deploy/install.sh"; exit 1; }

SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST=/opt/keyland
ENV_DIR=/etc/keyland
ENV_FILE=$ENV_DIR/keyland.env

echo "[1/6] Packages"
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip hostapd dnsmasq iw nftables iproute2 \
    iputils-ping network-manager modemmanager rsync nginx

echo "[2/6] Copy project to $DEST"
mkdir -p "$DEST"
rsync -a --delete --exclude '.venv' --exclude 'data' --exclude '__pycache__' \
      --exclude '.pytest_cache' "$SRC/" "$DEST/"
mkdir -p "$DEST/data"; chmod 700 "$DEST/data"
if [ ! -s "$DEST/data/keyland.db" ] && [ -s "$SRC/data/keyland.db" ]; then
    install -m 0600 "$SRC/data/keyland.db" "$DEST/data/keyland.db"
fi

# Install the narrowly scoped privileged router helper used by the API.
install -m 0750 "$DEST/deploy/keyland-routerctl" /usr/local/sbin/keyland-routerctl

echo "[3/6] Python environment"
python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install -q --upgrade pip
"$DEST/.venv/bin/pip" install -q -r "$DEST/requirements.txt"

echo "[4/6] Secrets and NetworkManager"
mkdir -p "$ENV_DIR"; chmod 700 "$ENV_DIR"
if [ ! -f "$ENV_FILE" ]; then
    umask 077
    cat > "$ENV_FILE" <<ENVEOF
KEYLAND_JWT_SECRET=$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')
KEYLAND_HOST=127.0.0.1
KEYLAND_PORT=8000
KEYLAND_COOKIE_SECURE=false
ENVEOF
fi
if grep -q '^KEYLAND_HOST=' "$ENV_FILE"; then
    sed -i 's/^KEYLAND_HOST=.*/KEYLAND_HOST=127.0.0.1/' "$ENV_FILE"
else
    printf '\nKEYLAND_HOST=127.0.0.1\n' >> "$ENV_FILE"
fi
set -a
. "$ENV_FILE"
set +a
: "${KEYLAND_JWT_SECRET:=}"
if [ "${#KEYLAND_JWT_SECRET}" -lt 32 ]; then
    echo "ERROR: KEYLAND_JWT_SECRET is missing or too short."
    exit 1
fi
KEYLAND_PORT="${KEYLAND_PORT:-8000}"
if ! grep -q '^KEYLAND_PORT=' "$ENV_FILE"; then
    printf 'KEYLAND_PORT=8000\n' >> "$ENV_FILE"
fi
# ap0 is managed by Keyland/hostapd, not by NetworkManager
mkdir -p /etc/NetworkManager/conf.d
printf '[keyfile]\nunmanaged-devices=interface-name:ap0\n' > /etc/NetworkManager/conf.d/99-keyland-ap0.conf
nmcli general reload conf 2>/dev/null || true

echo "[5/6] Administrator account"
HAS_ADMIN=$("$DEST/.venv/bin/python" - "$DEST/data/keyland.db" <<'PYEOF' 2>/dev/null || echo 0
import sqlite3, sys
try:
    print(sqlite3.connect(sys.argv[1]).execute("select count(*) from users").fetchone()[0])
except Exception:
    print(0)
PYEOF
)
if [ "${HAS_ADMIN:-0}" = "0" ]; then
    set -a; . "$ENV_FILE"; set +a
    (cd "$DEST" && "$DEST/.venv/bin/python" -m keyland.create_admin)
else
    echo "Existing database found, skipping."
fi

echo "[6/6] Nginx and systemd services"
install -m 644 "$DEST/deploy/keyland.nginx.conf" /etc/nginx/sites-available/keyland
ln -sfn /etc/nginx/sites-available/keyland /etc/nginx/sites-enabled/keyland
rm -f /etc/nginx/sites-enabled/default
nginx -t
LISTEN_PIDS="$(ss -ltnp "sport = :${KEYLAND_PORT}" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true)"
for pid in $LISTEN_PIDS; do
    command_line="$(ps -p "$pid" -o args= 2>/dev/null || true)"
    case "$command_line" in
        *uvicorn*keyland.main:app*) kill -TERM "$pid" ;;
        *) echo "ERROR: Port ${KEYLAND_PORT} is used by another process (PID ${pid}); not stopped."; exit 1 ;;
    esac
done
for attempt in $(seq 1 10); do
    if ! ss -ltn "sport = :${KEYLAND_PORT}" | grep -q LISTEN; then break; fi
    sleep 1
done
if ss -ltn "sport = :${KEYLAND_PORT}" | grep -q LISTEN; then echo "ERROR: Port ${KEYLAND_PORT} is still occupied."; exit 1; fi
install -m 644 "$DEST/deploy/keyland.service" /etc/systemd/system/keyland.service
install -m 644 "$DEST/deploy/keyland-router-restore.service" /etc/systemd/system/keyland-router-restore.service
install -m 644 "$DEST/deploy/keyland-router-restore.timer" /etc/systemd/system/keyland-router-restore.timer
systemctl daemon-reload
systemctl enable --now keyland.service
systemctl enable --now keyland-router-restore.timer
systemctl enable --now nginx
sleep 2
systemctl --no-pager --lines=5 status keyland.service || true
systemctl --no-pager --lines=5 status nginx.service || true

echo
echo "Done. Panel: http://<phone-ip>  (also via Tailscale/ZeroTier address)"
echo "Open the panel, configure Router Wi-Fi and press Start once."
echo "Router Mode is saved securely and restored automatically after reboot."
echo "Stop Router Wi-Fi from the GUI to disable automatic startup."
