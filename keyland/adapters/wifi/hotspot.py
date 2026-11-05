"""Guarded Wi-Fi hotspot management through NetworkManager.

Only a dedicated, disconnected AP-capable interface can be configured.
The currently active upstream Wi-Fi interface is never repurposed.
"""
from __future__ import annotations

import ipaddress
import json
import re
import shutil
import subprocess
from typing import Any

from keyland.adapters.wifi.capabilities import (
    parse_interface_combinations,
    parse_supported_modes,
)

_PROFILE = "Keyland-Hotspot"
_IFACE = re.compile(r"^[A-Za-z0-9_.:-]{1,15}$")


def _run(args: list[str], timeout: int = 12) -> dict[str, Any]:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout,
            check=False, errors="replace",
        )
        return {
            "success": result.returncode == 0,
            "output": result.stdout.strip(),
            "error": result.stderr.strip() or None,
        }
    except (OSError, subprocess.TimeoutExpired):
        return {"success": False, "output": "", "error": "Required command failed or timed out"}


def _wifi_interfaces() -> list[dict[str, Any]]:
    if not shutil.which("iw"):
        return []

    result = _run(["iw", "dev"])
    if not result["success"]:
        return []

    found: list[dict[str, Any]] = []
    phy = None
    current = None

    for raw in result["output"].splitlines():
        line = raw.strip()

        if line.startswith("phy#"):
            if current:
                found.append(current)
                current = None
            phy = line

        elif line.startswith("Unnamed/non-netdev interface"):
            if current:
                found.append(current)
                current = None

        elif line.startswith("Interface "):
            if current:
                found.append(current)

            current = {
                "name": line.split(maxsplit=1)[1],
                "phy": phy,
                "type": None,
                "ssid": None,
            }

        elif current and line.startswith("type "):
            current["type"] = line.split(maxsplit=1)[1]

        elif current and line.startswith("ssid "):
            current["ssid"] = line[5:].strip()

    if current:
        found.append(current)

    return found

def _nm_devices() -> list[dict[str, str]]:
    result = _run(["nmcli", "--terse", "--escape", "yes", "--fields", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"])
    if not result["success"]:
        return []
    rows = []
    for line in result["output"].splitlines():
        parts = line.split(":")
        if len(parts) < 4:
            continue
        rows.append({"name": parts[0], "type": parts[1], "state": parts[2], "connection": ":".join(parts[3:])})
    return rows


def _default_interfaces() -> set[str]:
    if not shutil.which("ip"):
        return set()
    result = _run(["ip", "-j", "route", "show", "default"])
    if not result["success"]:
        return set()
    try:
        return {item["dev"] for item in json.loads(result["output"]) if item.get("dev")}
    except (ValueError, TypeError, KeyError):
        return set()


def get_hotspot_capabilities() -> dict[str, Any]:
    """Read-only preflight. It does not create interfaces or alter routes."""
    missing = [tool for tool in ("iw", "ip", "nmcli") if not shutil.which(tool)]
    if missing:
        return {
            "available": False, "manager": "NetworkManager",
            "missing_tools": missing, "interfaces": [],
            "ap_candidates": [], "ap_mode_supported": False,
            "supported_modes": [], "interface_combinations": {},
            "default_route_interfaces": [], "upstream_wifi_interfaces": [],
            "upstream_sources": [], "safe_to_apply": False,
            "hotspot_profile": _PROFILE, "hotspot_active": False,
            "hotspot_active_interface": None,
            "message": "Install the missing tools and ensure NetworkManager manages the Wi-Fi device.",
        }

    phy_result = _run(["iw", "list"], timeout=20)
    modes = parse_supported_modes(phy_result["output"]) if phy_result["success"] else []
    combinations = parse_interface_combinations(phy_result["output"]) if phy_result["success"] else {}
    wifi = _wifi_interfaces()
    devices = _nm_devices()
    defaults = _default_interfaces()
    nm_by_name = {item["name"]: item for item in devices}
    wifi_names = {item["name"] for item in wifi}
    upstream_sources = []
    for name in sorted(defaults):
        if name in wifi_names:
            source_type = "wifi"
        elif name.startswith(("rmnet", "qmap", "wwan", "ppp", "cellular")):
            source_type = "cellular"
        else:
            source_type = "wired_or_other"
        upstream_sources.append({"interface": name, "type": source_type})
    active_profiles = _run(["nmcli", "--terse", "--fields", "NAME,DEVICE", "connection", "show", "--active"])
    active_rows = [line.split(":", 1) for line in active_profiles["output"].splitlines()] if active_profiles["success"] else []
    hotspot_active_row = next((row for row in active_rows if len(row) == 2 and row[0] == _PROFILE), None)
    candidates = []
    upstream_wifi = [
        item for item in wifi
        if item.get("type") == "managed"
        and (
            item["name"] in defaults
            or nm_by_name.get(item["name"], {}).get("state", "").lower() == "connected"
        )
    ]

    for item in wifi:
        nm = nm_by_name.get(item["name"], {})
        is_wifi = nm.get("type") == "wifi"
        is_disconnected = nm.get("state", "").lower() == "disconnected"
        is_ap = item.get("type") == "AP"
        is_default = item["name"] in defaults
        same_phy_upstream = any(
            upstream.get("phy") and upstream.get("phy") == item.get("phy")
            for upstream in upstream_wifi
        )
        concurrency_ok = (
            not same_phy_upstream
            or combinations.get("ap_client_combination_mentioned", False)
        )
        eligible = bool(
            is_wifi and not is_default and (is_ap or is_disconnected)
            and "AP" in modes and concurrency_ok
        )
        reason = (
            "Driver does not report AP mode" if "AP" not in modes
            else "Wi-Fi upstream uses the same PHY; simultaneous AP + Client support is not confirmed"
            if not concurrency_ok
            else None
        )
        if is_wifi and not is_default and (is_ap or is_disconnected):
            candidates.append({
                **item,
                "state": nm.get("state"),
                "connection": nm.get("connection"),
                "shares_phy_with_upstream": same_phy_upstream,
                "eligible": eligible,
                "reason": reason,
            })

    return {
        "available": True,
        "manager": "NetworkManager",
        "ap_mode_supported": "AP" in modes,
        "supported_modes": modes,
        "interface_combinations": combinations,
        "default_route_interfaces": sorted(defaults),
        "upstream_sources": upstream_sources,
        "upstream_wifi_interfaces": [item["name"] for item in upstream_wifi],
        "interfaces": wifi,
        "ap_candidates": candidates,
        "hotspot_profile": _PROFILE,
        "hotspot_active": hotspot_active_row is not None,
        "hotspot_active_interface": hotspot_active_row[1] if hotspot_active_row else None,
        "security_modes": ["WPA2-PSK"],
        "dns_supported": True,
        "safe_to_apply": any(item["eligible"] for item in candidates),
        "limitations": [
            "Only a separate AP interface is eligible; an active upstream Wi-Fi interface is never repurposed.",
            "Wi-Fi repeater mode requires the chipset/driver to support simultaneous managed + AP operation on the same PHY.",
            "Mobile-data sharing depends on the phone exposing a working cellular default route.",
            "WPA2-PSK is used; WPA3 is not enabled unless separately verified.",
        ],
    }


def _validate(payload: dict[str, Any]) -> tuple[str, str, list[str], str, str]:
    interface = payload.get("interface")
    ssid = payload.get("ssid")
    password = payload.get("password")
    security = payload.get("security", "wpa2-psk")
    dns = payload.get("dns_servers", [])
    ipv4_cidr = payload.get("ipv4_cidr", "192.168.88.1/24")

    if not isinstance(interface, str) or not _IFACE.fullmatch(interface) or interface == "lo":
        raise ValueError("Choose a valid wireless interface.")
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32:
        raise ValueError("SSID must contain 1–32 UTF-8 bytes.")
    if not isinstance(password, str) or not 8 <= len(password) <= 63:
        raise ValueError("WPA2 password must contain 8–63 characters.")
    if security != "wpa2-psk":
        raise ValueError("Only verified WPA2-PSK security is currently supported.")
    if not isinstance(dns, list) or len(dns) > 4:
        raise ValueError("Provide up to four DNS server addresses.")
    try:
        parsed_dns = [ipaddress.ip_address(item) for item in dns]
    except (ValueError, TypeError):
        raise ValueError("One or more DNS server addresses are invalid.")
    if any(address.version != 4 for address in parsed_dns):
        raise ValueError("Hotspot profile DNS currently accepts IPv4 addresses only.")
    addresses = [str(address) for address in parsed_dns]
    try:
        ap_address = ipaddress.ip_interface(ipv4_cidr)
    except (ValueError, TypeError):
        raise ValueError("Hotspot IPv4 must be a valid address with CIDR prefix, for example 192.168.88.1/24.")
    if ap_address.version != 4 or not 8 <= ap_address.network.prefixlen <= 30:
        raise ValueError("Hotspot IPv4 must be IPv4 with a subnet prefix between /8 and /30.")
    if ap_address.ip in (ap_address.network.network_address, ap_address.network.broadcast_address):
        raise ValueError("Hotspot IPv4 cannot be the network or broadcast address.")

    # Refuse overlapping subnets so the AP network cannot collide with upstream links.
    addr_result = _run(["ip", "-j", "-4", "addr", "show"])
    if addr_result["success"]:
        try:
            for link in json.loads(addr_result["output"]):
                if link.get("ifname") in {interface, "lo"}:
                    continue
                for info in link.get("addr_info", []):
                    if info.get("family") != "inet":
                        continue
                    existing_address = ipaddress.ip_interface(f'{info["local"]}/{info["prefixlen"]}')
                    if ap_address.network.overlaps(existing_address.network):
                        raise ValueError(
                            f"Hotspot subnet {ap_address.network} overlaps with "
                            f"{link.get('ifname')} ({existing_address}). Choose a different Hotspot IP/subnet."
                        )
        except (ValueError, KeyError, TypeError) as exc:
            if isinstance(exc, ValueError) and str(exc).startswith("Hotspot subnet"):
                raise
            # A malformed read-only address listing must not prevent setup.

    return interface, ssid, addresses, password, str(ap_address)


def start_hotspot(payload: dict[str, Any]) -> dict[str, Any]:
    """Create/update and activate the dedicated NetworkManager AP profile."""
    try:
        interface, ssid, dns, password, ipv4_cidr = _validate(payload)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    if payload.get("confirmed") is not True:
        return {"success": False, "error": "Explicit administrator confirmation is required."}

    preflight = get_hotspot_capabilities()
    if not preflight.get("available") or not preflight.get("safe_to_apply"):
        detected = ", ".join(
            f'{item.get("name")} ({item.get("type") or "unknown"})'
            for item in preflight.get("interfaces", [])
        ) or "no Wi-Fi interfaces detected"
        reasons = "; ".join(
            f'{item.get("name")}: {item.get("reason") or item.get("state") or "not eligible"}'
            for item in preflight.get("ap_candidates", [])
        )
        detail = f"Detected: {detected}."
        if reasons:
            detail += f" Candidate status: {reasons}."
        return {
            "success": False,
            "error": "No safe, separate AP interface is ready. " + detail,
            "preflight": preflight,
        }

    candidate = next((item for item in preflight["ap_candidates"] if item["name"] == interface and item["eligible"]), None)
    if not candidate:
        return {"success": False, "error": f"Selected interface {interface} is not an eligible, separate AP interface. Refresh the capability check and choose a dedicated AP interface."}
    if str(candidate.get("state") or "").lower() == "unmanaged":
        managed = _run(["nmcli", "device", "set", interface, "managed", "yes"])
        if not managed["success"]:
            reason = managed.get("error") or managed.get("output") or "NetworkManager refused the request"
            return {"success": False, "error": f"NetworkManager cannot manage the dedicated AP interface {interface}: {reason}"}
    if interface in _default_interfaces():
        return {"success": False, "error": "Refusing to modify the active default-route interface."}
    if not shutil.which("nmcli"):
        return {"success": False, "error": "NetworkManager CLI is unavailable."}

    existing = _run(["nmcli", "--terse", "--fields", "NAME", "connection", "show"])
    profile_exists = existing["success"] and _PROFILE in existing["output"].splitlines()
    active = _run(["nmcli", "--terse", "--fields", "GENERAL.CONNECTION", "device", "show", interface])
    active_name = active["output"].split(":", 1)[-1].strip() if active["success"] else ""
    if active_name and active_name not in {"--", _PROFILE}:
        return {"success": False, "error": "The selected AP interface is already used by another connection. It was not changed."}

    dns_value = ",".join(dns)
    # Do not pass an empty ipv4.dns value to nmcli: on some NetworkManager
    # versions this is rejected as an invalid setting and surfaced as HTTP 400.
    settings = [
        "connection.interface-name", interface,
        "802-11-wireless.ssid", ssid,
        "802-11-wireless.mode", "ap",
        "wifi-sec.key-mgmt", "wpa-psk",
        "wifi-sec.psk", password,
        "ipv4.method", "shared",
        "ipv4.addresses", ipv4_cidr,
        "ipv4.never-default", "yes",
        "ipv6.method", "disabled",
        "connection.autoconnect", "no",
    ]
    if dns_value:
        settings.extend(["ipv4.dns", dns_value, "ipv4.ignore-auto-dns", "yes"])
    else:
        # Explicitly clear a previous custom DNS list when reusing the profile.
        if profile_exists:
            settings.extend(["ipv4.dns", "", "ipv4.ignore-auto-dns", "no"])

    if profile_exists:
        result = _run(["nmcli", "connection", "modify", _PROFILE, *settings])
    else:
        result = _run([
            "nmcli", "connection", "add", "type", "wifi",
            "ifname", interface, "con-name", _PROFILE,
            *settings,
        ])

    if not result["success"]:
        reason = result.get("error") or result.get("output") or "unknown NetworkManager error"
        return {"success": False, "error": f"Could not prepare NetworkManager hotspot profile: {reason}"}

    activated = _run(["nmcli", "--wait", "30", "connection", "up", _PROFILE, "ifname", interface], timeout=38)
    if not activated["success"]:
        if not profile_exists:
            _run(["nmcli", "connection", "delete", _PROFILE])
        reason = activated.get("error") or activated.get("output") or "unknown activation error"
        device_state = _run([
            "nmcli", "--fields",
            "GENERAL.STATE,GENERAL.REASON,GENERAL.CONNECTION,GENERAL.MANAGED",
            "device", "show", interface,
        ])
        device_detail = device_state.get("output") or device_state.get("error") or "device diagnostics unavailable"
        return {
            "success": False,
            "error": f"Hotspot activation failed on {interface}: {reason}. Device status: {device_detail}",
            "profile_rolled_back": not profile_exists,
        }

    return {
        "success": True, "active": True, "interface": interface,
        "ssid": ssid, "security": "WPA2-PSK",
        "dns_servers": dns, "ipv4_cidr": ipv4_cidr,
        "addressing": "NetworkManager shared IPv4 (DHCP/NAT)",
        "persistent": True, "profile": _PROFILE,
        "message": "Hotspot profile activated. Verify client connectivity before relying on it.",
    }



def create_virtual_ap(interface: str, confirmed: bool) -> dict[str, Any]:
    """Create and verify a dedicated AP interface without touching upstream Wi-Fi."""
    if confirmed is not True:
        return {"success": False, "error": "Explicit administrator confirmation is required."}

    if not isinstance(interface, str) or not _IFACE.fullmatch(interface) or interface == "lo":
        return {"success": False, "error": "Invalid source Wi-Fi interface."}

    if not shutil.which("iw") or not shutil.which("ip"):
        return {"success": False, "error": "Required iw or ip command is missing."}

    preflight = get_hotspot_capabilities()
    if not preflight.get("available") or not preflight.get("ap_mode_supported"):
        return {"success": False, "error": "AP mode is not supported or Wi-Fi tools are unavailable."}

    source = next(
        (item for item in preflight["interfaces"] if item["name"] == interface),
        None,
    )
    if not source or source.get("type") != "managed":
        return {"success": False, "error": "Select an existing managed Wi-Fi interface."}

    active_wifi = (
        source.get("ssid") is not None
        or interface in preflight.get("upstream_wifi_interfaces", [])
        or interface in preflight.get("default_route_interfaces", [])
    )

    if active_wifi:
        combinations = preflight.get("interface_combinations") or {}
        if not combinations.get("ap_client_combination_mentioned"):
            return {
                "success": False,
                "error": "Simultaneous AP + Client support is not confirmed. No changes made.",
            }

    interfaces = _wifi_interfaces()
    existing = next(
        (item for item in interfaces if item["name"] == "keyland-ap"),
        None,
    )

    created_now = False

    if existing is None:
        result = _run([
            "iw", "dev", interface, "interface",
            "add", "keyland-ap", "type", "__ap",
        ])
        if not result["success"]:
            return {
                "success": False,
                "error": f"Could not create AP interface: {result.get('error') or result.get('output') or 'unknown error'}",
            }
        created_now = True

    # Some drivers create the virtual interface as "managed" even when
    # "__ap" was requested. Always inspect the newly-created interface and
    # explicitly switch it to AP mode when necessary.
    current = next(
        (item for item in _wifi_interfaces() if item["name"] == "keyland-ap"),
        None,
    )
    if not current:
        if created_now:
            _run(["iw", "dev", "keyland-ap", "del"])
        return {
            "success": False,
            "error": "The AP interface was not visible after creation.",
        }

    if current.get("type") != "AP":
        down = _run(["ip", "link", "set", "keyland-ap", "down"])
        if not down["success"]:
            if created_now:
                _run(["iw", "dev", "keyland-ap", "del"])
            return {
                "success": False,
                "error": f"Could not safely prepare AP interface: {down.get('error')}",
            }

        mode = _run(["iw", "dev", "keyland-ap", "set", "type", "__ap"])
        if not mode["success"]:
            if created_now:
                _run(["iw", "dev", "keyland-ap", "del"])
            return {
                "success": False,
                "error": f"Could not switch interface to AP mode: {mode.get('error')}",
            }

    verified = next(
        (item for item in _wifi_interfaces() if item["name"] == "keyland-ap"),
        None,
    )

    if not verified or verified.get("type") != "AP":
        if created_now:
            _run(["iw", "dev", "keyland-ap", "del"])
        return {
            "success": False,
            "error": "AP interface verification failed. Expected type AP.",
        }

    return {
        "success": True,
        "created": created_now,
        "interface": "keyland-ap",
        "type": "AP",
        "phy": verified.get("phy"),
        "message": "Dedicated AP interface created and verified.",
    }



def stop_hotspot(confirmed: bool) -> dict[str, Any]:
    if confirmed is not True:
        return {"success": False, "error": "Explicit administrator confirmation is required."}
    if not shutil.which("nmcli"):
        return {"success": False, "error": "NetworkManager CLI is unavailable."}
    result = _run(["nmcli", "connection", "down", _PROFILE], timeout=20)
    if not result["success"]:
        return {"success": False, "error": "Hotspot is not active or NetworkManager could not stop it."}
    return {"success": True, "active": False, "profile": _PROFILE}
