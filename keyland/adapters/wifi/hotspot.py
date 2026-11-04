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
            phy = line
        elif line.startswith("Interface "):
            if current:
                found.append(current)
            current = {"name": line.split(maxsplit=1)[1], "phy": phy, "type": None, "ssid": None}
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
            "message": "Install the missing tools and ensure NetworkManager manages the Wi-Fi device.",
        }

    phy_result = _run(["iw", "list"], timeout=20)
    modes = parse_supported_modes(phy_result["output"]) if phy_result["success"] else []
    combinations = parse_interface_combinations(phy_result["output"]) if phy_result["success"] else {}
    wifi = _wifi_interfaces()
    devices = _nm_devices()
    defaults = _default_interfaces()
    nm_by_name = {item["name"]: item for item in devices}
    candidates = []
    upstream_wifi = [
        item for item in wifi
        if item["name"] in defaults and item.get("type") == "managed"
    ]

    for item in wifi:
        nm = nm_by_name.get(item["name"], {})
        is_wifi = nm.get("type") == "wifi"
        is_disconnected = nm.get("state", "").lower() in {"disconnected", "unavailable"}
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
        "upstream_wifi_interfaces": [item["name"] for item in upstream_wifi],
        "interfaces": wifi,
        "ap_candidates": candidates,
        "hotspot_profile": _PROFILE,
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


def _validate(payload: dict[str, Any]) -> tuple[str, str, list[str], str | None]:
    interface = payload.get("interface")
    ssid = payload.get("ssid")
    password = payload.get("password")
    security = payload.get("security", "wpa2-psk")
    dns = payload.get("dns_servers", [])

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
        addresses = [str(ipaddress.ip_address(item)) for item in dns]
    except (ValueError, TypeError):
        raise ValueError("One or more DNS server addresses are invalid.")
    return interface, ssid, addresses, password


def start_hotspot(payload: dict[str, Any]) -> dict[str, Any]:
    """Create/update and activate the dedicated NetworkManager AP profile."""
    try:
        interface, ssid, dns, password = _validate(payload)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    if payload.get("confirmed") is not True:
        return {"success": False, "error": "Explicit administrator confirmation is required."}

    preflight = get_hotspot_capabilities()
    if not preflight.get("available") or not preflight.get("safe_to_apply"):
        return {"success": False, "error": "No safe, separate AP interface is ready. Review hotspot preflight.", "preflight": preflight}

    candidate = next((item for item in preflight["ap_candidates"] if item["name"] == interface and item["eligible"]), None)
    if not candidate:
        return {"success": False, "error": "Selected interface is not an eligible, separate AP interface."}
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
    settings = [
        "802-11-wireless.ssid", ssid,
        "802-11-wireless.mode", "ap",
        "wifi-sec.key-mgmt", "wpa-psk",
        "wifi-sec.psk", password,
        "ipv4.method", "shared",
        "ipv6.method", "disabled",
        "connection.autoconnect", "no",
    ]
    if dns_value:
        settings.extend(["ipv4.dns", dns_value, "ipv4.ignore-auto-dns", "yes"])

    if profile_exists:
        result = _run(["nmcli", "connection", "modify", _PROFILE, "connection.interface-name", interface, *settings])
    else:
        result = _run([
            "nmcli", "connection", "add", "type", "wifi",
            "ifname", interface, "con-name", _PROFILE,
            "ssid", ssid, *settings,
        ])

    if not result["success"]:
        return {"success": False, "error": "NetworkManager could not prepare the hotspot profile."}

    activated = _run(["nmcli", "connection", "up", _PROFILE, "ifname", interface], timeout=25)
    if not activated["success"]:
        if not profile_exists:
            _run(["nmcli", "connection", "delete", _PROFILE])
        return {"success": False, "error": "Hotspot activation failed. The newly created profile was rolled back."}

    return {
        "success": True, "active": True, "interface": interface,
        "ssid": ssid, "security": "WPA2-PSK",
        "dns_servers": dns, "addressing": "NetworkManager shared IPv4 (DHCP/NAT)",
        "persistent": True, "profile": _PROFILE,
        "message": "Hotspot profile activated. Verify client connectivity before relying on it.",
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
