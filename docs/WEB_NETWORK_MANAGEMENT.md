# Web Network Management

This branch records the staged implementation scope for Keyland-Network.

## Management areas
- DNS: inspect resolver configuration, set per-interface DNS and revert to automatic mode.
- Wi-Fi: AP, client and repeater modes, SSID, password and channel, gated by detected adapter capabilities.
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
1. DNS inspection and reversible per-interface runtime DNS controls.
2. Wi-Fi and chipset capability detection, then supported AP/client configuration.
3. WAN/LAN, routes and DHCP with connectivity rollback.
4. Firewall/NAT and client access control.
5. Modem configuration and system backup/restore.
6. Automated tests on supported Linux backends and documentation.

This is a scope and safety specification, not a claim that all controls have been implemented.
