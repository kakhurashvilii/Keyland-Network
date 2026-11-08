# Keyland v0.9.0 — Hotspot Device Control & Network Visibility

## Status

Planning and implementation branch: `feature/hotspot-device-control-v0.9.0`

Base release: v0.8.0

This document defines the v0.9.0 scope. It does not claim that the features below are already implemented.

## Existing functionality to preserve

The current Hotspot stack already provides:

- Associated-station discovery through `iw dev <ap> station dump`.
- DHCP lease enrichment with client IPv4 address and hostname.
- Per-station byte/packet counters and radio signal/bitrate details.
- Administrator-only client block/unblock endpoints.
- Per-client download/upload shaping through `tc flower`.
- A narrow privileged `keyland-routerctl` action bridge.

The implementation must extend this existing `keyland.adapters.wifi.router` path. It must not introduce a competing router controller or change the phone's upstream Wi-Fi, SIM, default routes, Tailscale, or ZeroTier paths.

## v0.9.0 goals

### 1. Per-device DNS query history

- Enable query logging only in the Keyland-managed dnsmasq instance for the dedicated AP.
- Record DNS metadata (query timestamp, client IP, queried domain, query type and result when available).
- Map client IP to the current DHCP lease/MAC and display domains per connected device.
- Provide a recent-query view, search/filter, and clear-history action behind administrator authentication.
- Keep logs local to the phone, with restrictive file permissions and a documented retention/size cap.
- Do not capture packet payloads, TLS traffic, HTTP contents, credentials, messages, or browsing page contents.

### 2. Domain filtering

- Add administrator-managed allow/block rules for exact domains and optionally their subdomains.
- Validate and normalize domain names; reject wildcards, URL paths, ports, control characters, and malformed labels.
- Apply filtering only to DNS requests that pass through the Keyland-managed resolver.
- Clearly state that DNS filtering can be bypassed by VPNs, encrypted DNS (DoH/DoT), hard-coded resolvers, or cached records. Do not claim that HTTPS content is visible.

### 3. Device controls and visibility

- Preserve current block/unblock and speed-limit controls.
- Show device identity, IP, MAC, connected time, signal, current rates, cumulative station counters, and DNS visibility status.
- Explain that DNS activity is incomplete when a client uses encrypted DNS or a tunnel.
- Any VPN/tunnel indication must be labelled heuristic/unknown unless a reliable signal exists; do not claim reliable VPN detection from DNS logs alone.

### 4. Privacy and security

- All read/write APIs require the existing administrator authentication and CSRF protections.
- No public registration, no arbitrary shell command endpoint, no TLS interception, no certificate installation, and no attempts to decrypt HTTPS.
- Query history is metadata, not message or page content.
- Use bounded retention and an explicit clear-history operation.
- Do not expose DNS history on public interfaces or to hotspot clients.
- Use safe file writes, strict input validation, and command argument arrays (never `shell=True`).

## Implementation sequence

1. Add a dedicated dnsmasq query log and a parser that maps query source IPs to DHCP lease MAC addresses.
2. Add unit tests for log parsing, IP-to-client mapping, malformed log lines, retention limits, and inactive hotspot behavior.
3. Add authenticated router-helper actions and FastAPI endpoints for per-client domain history and history clearing.
4. Add validated domain allow/block rules, persist them with mode 0600, apply them only to the Keyland-managed resolver, and verify read-back.
5. Extend the existing Hotspot device cards with a domain-history panel and visibility/limitations notice.
6. Run the complete test suite and JavaScript syntax validation; then validate on the OnePlus 6T without disrupting the active WAN.

## Acceptance criteria

- Existing hotspot start/stop, DHCP, NAT, repeater, SIM/WAN, device block, and speed-limit behavior remains unchanged.
- No non-Keyland dnsmasq process or system resolver is reconfigured.
- A test DNS query from a connected test client appears against the correct DHCP lease/MAC.
- Queries from encrypted DNS/VPN clients are not falsely represented as complete history.
- All APIs reject unauthenticated access.
- Log storage is bounded, private, and clearable.
- The dashboard clearly states that HTTPS content and private communications are not visible.
