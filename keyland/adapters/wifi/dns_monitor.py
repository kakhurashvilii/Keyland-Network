"""Privacy-conscious parser for dnsmasq query metadata.

This module parses only resolver query log lines. It does not inspect packets,
TLS, HTTP contents, credentials, or application payloads.
"""
from __future__ import annotations

import ipaddress
import re
from typing import Any

_QUERY = re.compile(
    r"^(?P<prefix>.*?)dnsmasq\[\d+\]:\s+"
    r"query\[(?P<qtype>[A-Z0-9-]+)\]\s+"
    r"(?P<domain>\S+)\s+from\s+(?P<ip>[^\s#]+)(?:#\d+)?\s*$",
    re.IGNORECASE,
)


def _normalize_domain(value: str) -> str | None:
    value = value.rstrip(".").strip().lower()
    if not value or len(value) > 253 or any(ord(ch) < 33 for ch in value):
        return None
    try:
        value = value.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    labels = value.split(".")
    if any(
        not label
        or len(label) > 63
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
        for label in labels
    ):
        return None
    return value


def parse_dhcp_leases(text: str) -> dict[str, dict[str, str]]:
    """Map a dnsmasq lease file's IPv4 address to its MAC and hostname."""
    result: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        mac = fields[1].lower()
        address = fields[2]
        hostname = fields[3] if fields[3] != "*" else ""
        if not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", mac):
            continue
        try:
            if ipaddress.ip_address(address).version != 4:
                continue
        except ValueError:
            continue
        result[address] = {"mac": mac, "hostname": hostname}
    return result


def parse_dns_query_log(
    text: str,
    leases: dict[str, dict[str, str]] | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Return the newest bounded DNS query events, enriched with DHCP identity."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 2000:
        raise ValueError("DNS query limit must be between 1 and 2000.")
    lease_map = leases or {}
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = _QUERY.match(line.strip())
        if not match:
            continue
        domain = _normalize_domain(match.group("domain"))
        if not domain:
            continue
        address = match.group("ip")
        try:
            ipaddress.ip_address(address)
        except ValueError:
            continue
        lease = lease_map.get(address, {})
        prefix = match.group("prefix").strip()
        # dnsmasq's standard syslog prefix is retained as a display timestamp.
        timestamp = prefix
        events.append({
            "timestamp": timestamp,
            "query_type": match.group("qtype").upper(),
            "domain": domain,
            "ip": address,
            "mac": lease.get("mac"),
            "hostname": lease.get("hostname") or None,
        })
    return events[-limit:]
