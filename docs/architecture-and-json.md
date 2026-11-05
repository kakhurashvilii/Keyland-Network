# Keyland Network — architecture and JSON contracts

## Runtime layout

- \`keyland/main.py\` — FastAPI application, admin session, CSRF middleware and router registration.
- \`keyland/api/\` — authenticated HTTP endpoints and Pydantic request validation.
- \`keyland/adapters/wifi/router.py\` — Linux Wi-Fi AP controller, hostapd/dnsmasq, policy routing, nftables, client station counters and per-client controls.
- \`keyland/adapters/cellular/\` — ModemManager and cellular status/usage readers.
- \`keyland/network.py\` — system interfaces, routes, traffic and resource telemetry.
- \`keyland/router/\` — router configuration and orchestration modules; keep platform-neutral policy here.
- \`web/index.html\` — current single-page responsive console. CSS and browser-side API rendering are currently inline; a later refactor can split them into \`web/assets/css/\` and \`web/assets/js/\`.
- \`deploy/keyland-routerctl\` — narrow JSON stdin/stdout bridge for privileged network operations.
- \`deploy/install.sh\` — NetHunter/Kali installation.
- \`tests/\` — API, adapter and safety regression tests.

## JSON boundaries

Do not use one giant JSON dump as the UI. Keep each API response focused and versioned.

### Hotspot state

\`GET /api/v1/wifi/hotspot/router/status\`

\`\`\`json
{
  "configured": true,
  "active": true,
  "configuration": {
    "mode": "repeater",
    "interface": "keyland-ap",
    "upstream_interface": "wlan0",
    "ssid": "Keyland-Hotspot",
    "ipv4_cidr": "192.168.88.1/24",
    "channel": 1,
    "firewall_table": "keyland_router"
  },
  "clients": [
    {
      "mac": "64:32:a8:b5:3e:ed",
      "signal": "-32 dBm",
      "rx_bytes": 6489980,
      "tx_bytes": 78773140,
      "connected_time": 1200
    }
  ]
}
\`\`\`

### Connected hotspot clients

\`GET /api/v1/wifi/hotspot/router/clients\`

\`\`\`json
{
  "active": true,
  "interface": "keyland-ap",
  "clients": [
    {
      "mac": "64:32:a8:b5:3e:ed",
      "ip": "192.168.88.10",
      "hostname": "phone",
      "signal": "-32 dBm",
      "rx_bytes": 6489980,
      "tx_bytes": 78773140,
      "connected_time": 1200,
      "blocked": false,
      "speed_limit": {
        "download_mbps": 10,
        "upload_mbps": 5
      }
    }
  ]
}
\`\`\`

**Counter direction:** Linux \`iw station dump\` RX bytes are bytes received by the AP from the station (client upload). TX bytes are bytes sent by the AP to the station (client download). Current counters are per association and may reset after reconnect/AP restart. The UI calculates instantaneous Mbps from successive samples; it must not label these counters as monthly totals.

### Block and unblock one client

\`POST /api/v1/wifi/hotspot/router/clients/block\` or \`/unblock\`

\`\`\`json
{
  "mac": "64:32:a8:b5:3e:ed",
  "confirmed": true
}
\`\`\`

Blocking is enforced on the isolated Keyland hotspot forwarding path. It must not add rules to the phone's upstream Wi-Fi or cellular interface.

### Per-client speed cap

\`POST /api/v1/wifi/hotspot/router/clients/limit\`

\`\`\`json
{
  "mac": "64:32:a8:b5:3e:ed",
  "download_mbps": 10,
  "upload_mbps": 5,
  "confirmed": true
}
\`\`\`

Limits apply to the dedicated AP interface only. The controller refuses to replace unowned qdiscs and records its own shaping state under \`/run/keyland-router/client-traffic.json\`.

## Mobile UI rules

- Under 760px use a fixed bottom navigation with safe-area padding.
- Keep cards single-column, compact, with short labels and no raw JSON in the primary view.
- Put raw diagnostic JSON in expandable diagnostic details only.
- Keep touch targets at least 35–40px and avoid horizontal page overflow.
- Keep desktop sidebar navigation on wider screens.
- Device controls require an explicit confirmation and admin session.

## Current scope and next engineering steps

Implemented on the feature branch:
- Mobile bottom navigation and compact card density.
- Hotspot client cards with MAC/IP/hostname, signal, connection time, cumulative association counters and sampled rates.
- Authenticated block/unblock and per-client upload/download rate-cap endpoints.
- Narrow router helper actions and installation of the helper script.

Still required before describing this as production-complete:
1. Run the complete pytest suite on Kali/NetHunter and test the exact kernel's \`tc flower\` support.
2. Verify nftables set/chain creation and blocking/unblocking with a real connected station.
3. Add persistent usage accounting (daily/monthly byte totals) in SQLite, with reset windows and configurable quotas. Current \`iw\` counters are not durable monthly accounting.
4. Add a safe "remove speed limit" action and restore shaping state after AP service restart.
5. Add client naming/reservation support and a clear audit log for administrator actions.
6. Split the monolithic HTML into separate CSS/JS assets after behavior stabilizes.
7. Ensure the service remains LAN-only by default; remote access must be explicitly enabled through a trusted VPN.

