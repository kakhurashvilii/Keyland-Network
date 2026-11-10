# Keyland ZeroTier Network Manager

Keyland exposes an administrator-only ZeroTier manager under `/api/v1/zerotier`.

## Current capabilities

- Local node status, networks, peers, service state.
- Join and leave a network on this device using `zerotier-cli`.
- Central network and member inventory.
- Authorize and deauthorize a member through Central.
- Update network name, description, private/public access mode, managed routes and DNS.
- Read the ZeroTier service status and a bounded tail of its system journal.

All state-changing HTTP actions require the Keyland administrator session, CSRF protection for cookie sessions, an explicit confirmation flag, and an exact confirmation phrase. Network leave may disconnect an SSH session that is using ZeroTier.

## Central API token

This implementation currently targets the Legacy Central v1 API at `https://api.zerotier.com/api/v1`, using the personal API token format:

```ini
KEYLAND_ZEROTIER_CENTRAL_TOKEN=YOUR_PERSONAL_CENTRAL_API_TOKEN
```

Store this line in `/etc/keyland/keyland.env` on the Keyland device. Do not put the token in the browser, source code, GitHub, screenshots, or chat. Keep the environment file readable only by root:

```bash
sudo chown root:root /etc/keyland/keyland.env
sudo chmod 600 /etc/keyland/keyland.env
sudo systemctl restart keyland
```

The token is read server-side and is never returned by the API. A missing or rejected token is reported without exposing the token value.

New Central uses service-account tokens and the v2 API with Bearer authentication and organization/network-group scoping. That v2 organization workflow is not yet implemented in this adapter; do not assume a v2-only token will work with the current v1 integration.

## API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/status` | Local node, networks and peers |
| GET | `/diagnostics?lines=100` | Service status and bounded journal tail |
| POST | `/local/join` | Join this node to a network |
| POST | `/local/leave` | Leave a network from this node |
| GET | `/central/networks` | List Central networks |
| GET | `/central/networks/{network_id}/members` | List Central network members |
| POST | `/central/networks/{network_id}/members/{member_id}/authorize` | Authorize a member |
| POST | `/central/networks/{network_id}/members/{member_id}/deauthorize` | Deauthorize a member |
| POST | `/central/networks/{network_id}/settings` | Update network name, privacy, routes and DNS |

All endpoints require an administrator.

## Validation

On the OnePlus/Kali device, after pulling the update:

```bash
cd /opt/keyland
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m py_compile \
  keyland/adapters/zerotier/monitor.py \
  keyland/adapters/zerotier/central.py \
  keyland/api/zerotier.py \
  keyland/main.py

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  -q -p no:cacheprovider --import-mode=importlib tests/test_api.py
```

Only restart Keyland after compilation and tests pass.
