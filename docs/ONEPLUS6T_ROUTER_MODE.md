# OnePlus 6T Router Wi-Fi

Keyland Network includes an experimental Linux router controller for Kali NetHunter Pro on OnePlus 6T (fajita / sdm845).

## Router modes

- **Hotspot:** share a selected active WAN interface, including cellular interfaces such as qmapmux0.0/rmnet or Wi-Fi.
- **Wi-Fi repeater:** use an already-connected Wi-Fi client interface as WAN and a separate AP interface as LAN. The phone's single radio must support concurrent managed + AP operation. Both roles must use the same channel.
- The dashboard exposes the AP interface, SSID, WPA2 passphrase, LAN gateway/CIDR, DNS servers, operating mode, and upstream WAN selector.

## Linux services used

- hostapd: WPA2 access point on a dedicated AP interface (normally keyland-ap).
- dnsmasq: DHCP leases and DNS forwarding on the AP interface only.
- nftables: an isolated table named keyland_router for forwarding and NAT.
- iproute2: a dedicated policy-routing table (42002) routes AP-client traffic through the selected WAN without changing the phone's existing default routes.
- NetworkManager: only the dedicated AP interface is marked unmanaged while hostapd owns it. The selected upstream interface is not reconfigured.

Install required packages on Kali:

```bash
sudo apt update
sudo apt install hostapd dnsmasq nftables iproute2 iw network-manager
```

The application must have the required root privileges to manage AP interface addressing, sysctl forwarding, nftables and policy routing.

## Start/stop API

- GET /api/v1/wifi/hotspot/router/status
- POST /api/v1/wifi/hotspot/router/start
- POST /api/v1/wifi/hotspot/router/stop

All endpoints require administrator authentication. Start/stop require explicit confirmation. Start validates that the selected WAN currently has a default route, that the AP interface is already a separate AP-mode interface, and that the AP subnet does not overlap an existing IPv4 network.

Example request:

```json
{
  "mode": "hotspot",
  "interface": "keyland-ap",
  "upstream_interface": "qmapmux0.0",
  "ssid": "Keyland-Hotspot",
  "password": "replace-with-your-own-ASCII-passphrase",
  "ipv4_cidr": "192.168.88.1/24",
  "dns_servers": ["1.1.1.1", "8.8.8.8"],
  "confirmed": true
}
```

## Safety behavior

- The active WAN interface, its address, connection profile and default routes are not changed.
- The active wlan0 connection is not intentionally disconnected or switched to AP mode.
- Only the selected dedicated AP interface is detached from NetworkManager during operation.
- The AP's IPv4 forwarding, nftables table and policy-routing table are removed on stop; the previous global IPv4-forwarding value is restored.
- The AP password is kept in a root-only runtime hostapd configuration and is not written to the status file.
- The controller refuses to overwrite an existing nftables table or policy-routing table/priority.
- The dashboard remains administrator-only; do not expose it directly to the public Internet.

## Important limitations / validation

This is not yet a complete OpenWrt replacement. Repeater operation on the OnePlus 6T is experimental and must be validated on the actual ath10k_snoc firmware. The driver's reported AP+managed combination does not guarantee stable concurrent operation. If hostapd fails to start, inspect /run/keyland-router/hostapd.log and kernel logs.

After starting, test:
1. A client can see the SSID and complete WPA2 association.
2. The client receives an address in the configured LAN subnet.
3. The client can resolve DNS and reach the Internet through the selected WAN.
4. The phone's own Wi-Fi, SIM, Tailscale and ZeroTier connections remain available.

Do not treat a successful API response as proof of end-to-end forwarding. Do not enable the feature on a remote-only management path until tested locally.


## Persistent Router Mode (boot restore)

Starting Router Wi-Fi now stores the selected router configuration for automatic restoration after reboot. The saved file is:

- `/etc/keyland/router-autostart.json`
- Root-owned directory and file permissions are restricted; the file is written atomically with mode `0600`.
- The Wi-Fi passphrase is stored only in this root-only configuration file. It is never returned by the status API.
- The existing runtime state remains under `/run/keyland-router`; it is recreated during boot restoration.

The installer enables:

- `keyland-router-restore.timer`: first attempt 45 seconds after boot, then retries every 30 seconds.
- `keyland-router-restore.service`: prepares the dedicated AP interface and restores the saved configuration only after the selected WAN has a default route.

If the selected WAN or Wi-Fi device is not ready, the restore attempt waits for the next timer run. The controller does not replace the phone's default route or reconfigure the selected WAN. Router Wi-Fi Stop removes the saved auto-start configuration after a successful stop.

Authenticated API:
- GET `/api/v1/wifi/hotspot/router/autostart`
- POST `/api/v1/wifi/hotspot/router/autostart/disable` with `{"confirmed": true}`

The feature does not enable public Internet exposure. Continue to access the GUI through the AP LAN or a separately secured ZeroTier/Tailscale path.
