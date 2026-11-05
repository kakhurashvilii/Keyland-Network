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
    iputils-ping network-manager modemmanager rsync

echo "[2/6] Copy project to $DEST"
mkdir -p "$DEST"
rsync -a --delete --exclude '.venv' --exclude 'data' --exclude '__pycache__' \
      --exclude '.pytest_cache' "$SRC/" "$DEST/"
mkdir -p "$DEST/data"; chmod 700 "$DEST/data"

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
KEYLAND_HOST=0.0.0.0
KEYLAND_PORT=8000
KEYLAND_COOKIE_SECURE=false
ENVEOF
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

echo "[6/6] systemd service"
install -m 644 "$DEST/deploy/keyland.service" /etc/systemd/system/keyland.service
systemctl daemon-reload
systemctl enable --now keyland.service
sleep 2
systemctl --no-pager --lines=5 status keyland.service || true

echo
echo "Done. Panel: http://<phone-ip>:8000  (also via Tailscale/ZeroTier address)"
echo "Open the panel, set hotspot password, press Start."
