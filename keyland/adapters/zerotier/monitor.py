"""
Keyland ZeroTier monitor and local membership controls.
State-changing operations are exposed separately and require API-level confirmation.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any

_NETWORK_ID_RE = re.compile(r"^[0-9a-fA-F]{16}$")


def _run(args: list[str], timeout: int = 8) -> dict[str, Any]:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout,
            check=False, errors="replace",
        )
        return {
            "success": result.returncode == 0,
            "returncode": result.returncode,
            "output": result.stdout.strip(),
            "error": result.stderr.strip() or None,
        }
    except FileNotFoundError:
        return {"success": False, "returncode": None, "output": "", "error": "Command not found"}
    except subprocess.TimeoutExpired:
        return {"success": False, "returncode": None, "output": "", "error": "Command timed out"}
    except OSError as exc:
        return {"success": False, "returncode": None, "output": "", "error": str(exc)}


def _json_command(cli: str, command: str) -> dict[str, Any]:
    result = _run([cli, "-j", command])
    if not result["success"]:
        return {"success": False, "data": None, "error": result["error"]}
    try:
        return {"success": True, "data": json.loads(result["output"]), "error": None}
    except (json.JSONDecodeError, TypeError):
        return {"success": False, "data": None, "error": "Invalid JSON response"}


def get_zerotier_status() -> dict[str, Any]:
    cli = shutil.which("zerotier-cli")
    systemctl = shutil.which("systemctl")
    service = "unknown"

    if systemctl:
        result = _run([systemctl, "is-active", "zerotier-one"])
        service = result["output"] or "unknown"

    if not cli:
        return {
            "available": False, "service": service, "cli_available": False,
            "info": None, "networks": [], "peers": [],
            "errors": ["zerotier-cli command not found"], "read_only": True,
        }

    info = _json_command(cli, "info")
    networks = _json_command(cli, "listnetworks")
    peers = _json_command(cli, "listpeers")
    errors = [
        f"{name}: {item['error']}"
        for name, item in (("info", info), ("networks", networks), ("peers", peers))
        if not item["success"]
    ]

    return {
        "available": True,
        "service": service,
        "cli_available": True,
        "info": info["data"] if isinstance(info["data"], dict) else None,
        "networks": networks["data"] if isinstance(networks["data"], list) else [],
        "peers": peers["data"] if isinstance(peers["data"], list) else [],
        "errors": errors,
        "read_only": True,
    }


def _validated_cli() -> str:
    cli = shutil.which("zerotier-cli")
    if not cli:
        raise RuntimeError("zerotier-cli command not found")
    return cli


def join_network(network_id: str) -> dict[str, Any]:
    if not _NETWORK_ID_RE.fullmatch(network_id or ""):
        return {"success": False, "error": "Invalid ZeroTier network ID"}
    result = _run([_validated_cli(), "join", network_id], timeout=20)
    return {
        "success": result["success"],
        "network_id": network_id,
        "output": result["output"],
        "error": result["error"] or (None if result["success"] else "ZeroTier join command failed"),
    }


def leave_network(network_id: str) -> dict[str, Any]:
    if not _NETWORK_ID_RE.fullmatch(network_id or ""):
        return {"success": False, "error": "Invalid ZeroTier network ID"}
    result = _run([_validated_cli(), "leave", network_id], timeout=20)
    return {
        "success": result["success"],
        "network_id": network_id,
        "output": result["output"],
        "error": result["error"] or (None if result["success"] else "ZeroTier leave command failed"),
    }


def get_zerotier_diagnostics(lines: int = 100) -> dict[str, Any]:
    systemctl = shutil.which("systemctl")
    journalctl = shutil.which("journalctl")
    service_status = _run([systemctl, "status", "zerotier-one", "--no-pager", "-l"], timeout=8) if systemctl else {
        "success": False, "output": "", "error": "systemctl command not found"
    }
    logs = _run(
        [journalctl, "-u", "zerotier-one", "--no-pager", "-n", str(lines), "-o", "short-iso"],
        timeout=12,
    ) if journalctl else {"success": False, "output": "", "error": "journalctl command not found"}
    return {
        "service_status": service_status["output"][-12000:],
        "service_status_error": service_status["error"],
        "logs": logs["output"][-30000:],
        "logs_error": logs["error"],
        "log_lines_requested": lines,
    }
