# OnePlus 6T Router Mode

Keyland Network v0.5.0 adds guarded router controls intended for Kali NetHunter Pro on the OnePlus 6T (fajita, sdm845).

## WAN options

- **Mobile-data WAN:** the phone must expose a working cellular interface (for example `rmnet_ipa0` or `qmapmux0.0`) and a usable default route. The hotspot uses NetworkManager's shared IPv4 mode for DHCP and NAT.
- **Wi-Fi repeater WAN:** the phone must maintain an upstream Wi-Fi client connection and expose a separate AP interface. If both roles share one PHY, `iw list` must report a simultaneous managed + AP interface combination. A single Wi-Fi radio without that capability cannot act as a repeater at the same time.

## Hotspot controls

The dashboard can inspect Wi-Fi PHYs and NetworkManager devices, choose an eligible AP interface, and configure:
- SSID (1–32 UTF-8 bytes)
- WPA2-PSK password (8–63 characters)
- IPv4 DNS servers (optional)
- NetworkManager shared IPv4 mode (DHCP/NAT)
- Start and stop of the dedicated `Keyland-Hotspot` profile

The controller refuses to repurpose a default-route interface and refuses an AP interface already used by another connection. It does not create virtual interfaces, change drivers, or silently disconnect the active Wi-Fi link. If no separate AP interface exists, the UI reports that a hardware/driver preflight is required.

## DNS

DNS management detects NetworkManager first and saves IPv4/IPv6 DNS values in the active connection profile with a live reapply and read-back verification. If the interface is not managed by NetworkManager, it can use `systemd-resolved` when available; that path is runtime-only. Automatic DNS reset is available.

## Access and safety

- One local administrator account; no public registration.
- All write endpoints require administrator authentication and explicit confirmation.
- Cookie-authenticated writes remain protected by CSRF validation.
- No arbitrary shell command endpoint; subprocess calls use fixed argument arrays and no shell.
- Management UI should remain on the trusted LAN. Do not expose it directly to the public Internet.
- Changes that could interrupt the only management path are intentionally not offered by this hotspot controller.

## Not yet implemented

This release is not a complete OpenWrt replacement. Persistent WAN/LAN addressing, DHCP server administration outside the hotspot profile, full firewall rule editor, client blocking, APN/operator controls, route editing, and timed rollback for all network changes still require separate modules and device testing.

## Device validation checklist

Run these commands on the OnePlus 6T before enabling hotspot or repeater mode:

```bash
uname -a
ip -br link
ip route
iw dev
iw list
nmcli device status
nmcli connection show --active
systemctl status NetworkManager --no-pager
```

Confirm the cellular default route or upstream Wi-Fi is stable, that a dedicated AP interface is available, and that the driver's interface-combination section explicitly permits AP + managed operation when using Wi-Fi repeater mode. Do not test by disabling the current `wlan0`, cellular interface, Tailscale, or ZeroTier connection.
