"""Runtime DNS controls for Linux systems using systemd-resolved.

These operations are intentionally runtime-only. Persistent DNS configuration
must be implemented through the detected network manager in a later change.
"""
from __future__ import annotations

import ipaddress
import re
import shutil
import subprocess
from typing import Any


_INTERFACE = re.compile(r"^[A-Za-z0-9_.:-]{1,15}$")


def _command(args: list[str]) -> dict[str, Any]:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            errors="replace",
        )
        return {
            "success": result.returncode == 0,
            "output": result.stdout.strip(),
            "error": result.stderr.strip() or None,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"success": False, "output": "", "error": str(exc)}


def get_dns_status() -> dict[str, Any]:
    if not shutil.which("resolvectl"):
        return {
            "available": False,
            "manager": None,
            "persistent": False,
            "error": "systemd-resolved is not available",
        }
    result = _command(["resolvectl", "status"])
    dns = _command(["resolvectl", "dns"])
    return {
        "available": result["success"],
        "manager": "systemd-resolved",
        "persistent": False,
        "status": result["output"],
        "dns_by_link": dns["output"].splitlines(),
        "error": result["error"],
    }


def set_dns(interface: str, servers: list[str], confirmed: bool) -> dict[str, Any]:
    if confirmed is not True:
        return {"success": False, "error": "Explicit confirmation is required"}
    if not isinstance(interface, str) or not _INTERFACE.fullmatch(interface) or interface == "lo":
        return {"success": False, "error": "Invalid interface"}
    if not shutil.which("resolvectl"):
        return {"success": False, "error": "systemd-resolved is not available"}
    if not isinstance(servers, list) or not 1 <= len(servers) <= 4:
        return {"success": False, "error": "Provide 1 to 4 DNS server addresses"}
    try:
        addresses = [str(ipaddress.ip_address(value)) for value in servers]
    except (ValueError, TypeError):
        return {"success": False, "error": "Invalid DNS server address"}
    if not shutil.which("ip"):
        return {"success": False, "error": "iproute2 is not available"}
    exists = _command(["ip", "link", "show", "dev", interface])
    if not exists["success"]:
        return {"success": False, "error": "Interface not found"}
    result = _command(["resolvectl", "dns", interface, *addresses])
    return {
        "success": result["success"],
        "interface": interface,
        "servers": addresses,
        "persistent": False,
        "error": result["error"],
    }


def reset_dns(interface: str, confirmed: bool) -> dict[str, Any]:
    if confirmed is not True:
        return {"success": False, "error": "Explicit confirmation is required"}
    if not isinstance(interface, str) or not _INTERFACE.fullmatch(interface) or interface == "lo":
        return {"success": False, "error": "Invalid interface"}
    if not shutil.which("resolvectl") or not shutil.which("ip"):
        return {"success": False, "error": "Required Linux network tools are unavailable"}
    exists = _command(["ip", "link", "show", "dev", interface])
    if not exists["success"]:
        return {"success": False, "error": "Interface not found"}
    result = _command(["resolvectl", "revert", interface])
    return {
        "success": result["success"],
        "interface": interface,
        "persistent": False,
        "error": result["error"],
    }
