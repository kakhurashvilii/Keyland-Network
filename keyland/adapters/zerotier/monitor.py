"""
Keyland ZeroTier Monitor v1.0.0

Read-only service, network and peer diagnostics.
No network membership, route, DNS or service state is changed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any


def _run(args: list[str], timeout: int = 8) -> dict[str, Any]:
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
    except FileNotFoundError:
        return {"success": False, "output": "", "error": "Command not found"}
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "", "error": "Command timed out"}
    except OSError as exc:
        return {"success": False, "output": "", "error": str(exc)}


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
