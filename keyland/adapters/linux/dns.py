"""DNS inspection and guarded per-interface DNS configuration.

NetworkManager changes are saved to the active connection profile.
systemd-resolved changes are runtime-only. No arbitrary shell is used.
"""
from __future__ import annotations

import ipaddress
import re
import shutil
import subprocess
from typing import Any

_INTERFACE = re.compile(r"^[A-Za-z0-9_.:-]{1,15}$")


def _command(args: list[str], timeout: int = 10) -> dict[str, Any]:
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
        return {"success": False, "output": "", "error": "Required network command failed or timed out"}


def _valid_interface(interface: str) -> bool:
    return bool(isinstance(interface, str) and _INTERFACE.fullmatch(interface) and interface != "lo")


def _nm_connection(interface: str) -> str | None:
    if not shutil.which("nmcli"):
        return None
    result = _command(["nmcli", "-g", "GENERAL.CONNECTION", "device", "show", interface])
    if not result["success"]:
        return None
    value = result["output"].strip()
    if not value or value in {"--", "unknown"}:
        return None
    return value


def get_dns_status() -> dict[str, Any]:
    if shutil.which("nmcli"):
        result = _command(["nmcli", "--fields", "DEVICE,IP4.DNS,IP6.DNS", "device", "show"])
        if result["success"]:
            return {
                "available": True, "manager": "NetworkManager",
                "persistent": True, "status": result["output"],
                "dns_by_link": [line.strip() for line in result["output"].splitlines() if "DNS" in line],
                "error": None,
                "note": "DNS changes are saved in the active NetworkManager connection profile.",
            }

    if shutil.which("resolvectl"):
        result = _command(["resolvectl", "status"])
        dns = _command(["resolvectl", "dns"])
        return {
            "available": result["success"], "manager": "systemd-resolved",
            "persistent": False, "status": result["output"],
            "dns_by_link": dns["output"].splitlines(),
            "error": result["error"],
            "note": "Changes are runtime-only and may be reset after reconnect or reboot.",
        }

    return {
        "available": False, "manager": None, "persistent": False,
        "dns_by_link": [], "status": "",
        "error": "Neither NetworkManager nor systemd-resolved is available.",
    }


def set_dns(interface: str, servers: list[str], confirmed: bool) -> dict[str, Any]:
    if confirmed is not True:
        return {"success": False, "error": "Explicit administrator confirmation is required."}
    if not _valid_interface(interface):
        return {"success": False, "error": "Invalid network interface."}
    if not isinstance(servers, list) or not 1 <= len(servers) <= 4:
        return {"success": False, "error": "Provide one to four DNS server addresses."}
    try:
        addresses = [str(ipaddress.ip_address(value)) for value in servers]
    except (ValueError, TypeError):
        return {"success": False, "error": "One or more DNS server addresses are invalid."}

    if not shutil.which("ip") or not _command(["ip", "link", "show", "dev", interface])["success"]:
        return {"success": False, "error": "Network interface was not found."}

    connection = _nm_connection(interface)
    if connection:
        previous = _command(["nmcli", "-g", "ipv4.dns,ipv4.ignore-auto-dns", "connection", "show", connection])
        old_lines = previous["output"].splitlines() if previous["success"] else []
        old_dns = old_lines[0] if old_lines else ""
        old_ignore = old_lines[1] if len(old_lines) > 1 else "no"
        changed = _command([
            "nmcli", "connection", "modify", connection,
            "ipv4.dns", ",".join(addresses),
            "ipv4.ignore-auto-dns", "yes",
        ])
        if not changed["success"]:
            return {"success": False, "error": "NetworkManager rejected the DNS profile change."}
        applied = _command(["nmcli", "device", "reapply", interface])
        if not applied["success"]:
            _command([
                "nmcli", "connection", "modify", connection,
                "ipv4.dns", old_dns,
                "ipv4.ignore-auto-dns", old_ignore,
            ])
            return {"success": False, "error": "DNS could not be applied live; the previous profile values were restored."}
        return {
            "success": True, "interface": interface, "servers": addresses,
            "manager": "NetworkManager", "persistent": True,
            "message": "DNS profile updated and applied.",
        }

    if shutil.which("resolvectl"):
        result = _command(["resolvectl", "dns", interface, *addresses])
        return {
            "success": result["success"], "interface": interface,
            "servers": addresses, "manager": "systemd-resolved",
            "persistent": False, "error": result["error"],
        }
    return {"success": False, "error": "No supported DNS manager controls this interface."}


def reset_dns(interface: str, confirmed: bool) -> dict[str, Any]:
    if confirmed is not True:
        return {"success": False, "error": "Explicit administrator confirmation is required."}
    if not _valid_interface(interface):
        return {"success": False, "error": "Invalid network interface."}
    if not shutil.which("ip") or not _command(["ip", "link", "show", "dev", interface])["success"]:
        return {"success": False, "error": "Network interface was not found."}

    connection = _nm_connection(interface)
    if connection:
        previous = _command(["nmcli", "-g", "ipv4.dns,ipv4.ignore-auto-dns", "connection", "show", connection])
        old_lines = previous["output"].splitlines() if previous["success"] else []
        old_dns = old_lines[0] if old_lines else ""
        old_ignore = old_lines[1] if len(old_lines) > 1 else "no"
        changed = _command([
            "nmcli", "connection", "modify", connection,
            "ipv4.dns", "", "ipv4.ignore-auto-dns", "no",
        ])
        if not changed["success"]:
            return {"success": False, "error": "NetworkManager could not restore automatic DNS."}
        applied = _command(["nmcli", "device", "reapply", interface])
        if not applied["success"]:
            _command([
                "nmcli", "connection", "modify", connection,
                "ipv4.dns", old_dns, "ipv4.ignore-auto-dns", old_ignore,
            ])
            return {"success": False, "error": "Automatic DNS could not be applied; previous profile values were restored."}
        return {
            "success": True, "interface": interface,
            "manager": "NetworkManager", "persistent": True,
            "message": "Automatic DNS restored in the active profile.",
        }

    if shutil.which("resolvectl"):
        result = _command(["resolvectl", "revert", interface])
        return {
            "success": result["success"], "interface": interface,
            "manager": "systemd-resolved", "persistent": False,
            "error": result["error"],
        }
    return {"success": False, "error": "No supported DNS manager controls this interface."}
