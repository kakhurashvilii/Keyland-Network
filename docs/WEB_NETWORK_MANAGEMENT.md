# Web Network Management

This branch records the staged implementation scope for Keyland-Network.

## Management areas
- DNS: inspect resolver configuration, save per-interface IPv4/IPv6 DNS in NetworkManager profiles, use runtime systemd-resolved fallback, and revert to automatic mode.
- Wi-Fi: inspect AP/client/repeater capability; configure a dedicated NetworkManager WPA2 hotspot profile with SSID, password, IPv4 DNS and shared DHCP/NAT when a safe AP interface is available. Concurrent AP + Client is gated by driver-reported PHY combinations.
- Chipsets: enumerate adapters, PHYs, drivers, bands, channels and supported modes. Hardware chipsets cannot be replaced in software; driver changes are potentially disruptive.
- WAN/LAN: IP addressing, gateway, DHCP and MTU.
- Routing: inspect, add and remove routes with management-path protection.
- Firewall: rules, NAT and ports with backend detection and rollback.
- SIM/LTE/5G: modem, APN, operator and network mode through supported modem APIs.
- Devices: connected clients, IP/MAC, and explicit block/unblock.
- System: services, logs, backups, restore and diagnostics.

## Mandatory safeguards
- Existing single-admin authentication and CSRF protection for every write endpoint.
- No public registration and no arbitrary shell command endpoint.
- Strict input validation and command argument allowlists; never use shell=True.
- Preview/diff and explicit confirmation before applying changes.
- Backup before persistent changes and timed rollback for connectivity-affecting changes.
- Detect the actual network manager and firewall backend; do not assume systemd-resolved, NetworkManager, nftables or iptables.
- Preserve active Wi-Fi, SIM data, Tailscale, ZeroTier and the management route unless the administrator explicitly confirms a safe transition.
- Verify each OS-level change by reading the setting back; never report success based only on submitted form values.
- Clearly label runtime-only versus persistent changes.
- Hide or mark unsupported controls instead of simulating success.

## Delivery sequence
1. DNS inspection and per-interface DNS controls with manager-aware persistence and read-back verification.
2. Wi-Fi and chipset capability detection, then guarded hotspot activation on a separate AP interface.
3. WAN/LAN, routes and DHCP with connectivity rollback.
4. Firewall/NAT and client access control.
5. Modem configuration and system backup/restore.
6. Automated tests on supported Linux backends and documentation.

This document tracks staged implementation. Hotspot activation requires NetworkManager and a dedicated eligible AP interface; it is not a claim that all router functions are complete. See ONEPLUS6T_ROUTER_MODE.md for device-specific limits.
