"""
Keyland ZeroTier local-agent adapter.

This module only reads local state or issues an explicitly requested
join/leave command. Central administration lives in central.py.
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
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            errors="replace",
        )
        output = result.stdout.strip()
        stderr = result.stderr.strip()
        return {
            "success": result.returncode == 0,
            "returncode": result.returncode,
            "output": output,
            "error": stderr or (
                None if result.returncode == 0
                else f"Command exited with code {result.returncode}"
            ),
        }
    except FileNotFoundError:
        return {
            "success": False, "returncode": None, "output": "",
            "error": f"Command not found: {args[0] if args else 'unknown'}",
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False, "returncode": None, "output": "",
            "error": f"Command timed out after {timeout}s",
        }
    except OSError as exc:
        return {
            "success": False, "returncode": None, "output": "",
            "error": str(exc),
        }


def _json_command(cli: str, command: str) -> dict[str, Any]:
    result = _run([cli, "-j", command])
    if not result["success"]:
        return {
            "success": False, "data": None,
            "error": result["error"] or "ZeroTier command failed",
            "returncode": result["returncode"],
        }
    try:
        return {
            "success": True, "data": json.loads(result["output"]),
            "error": None, "returncode": 0,
        }
    except (json.JSONDecodeError, TypeError):
        return {
            "success": False, "data": None,
            "error": "ZeroTier CLI returned invalid JSON",
            "returncode": result["returncode"],
        }


def _service_state() -> str:
    systemctl = shutil.which("systemctl")
    if systemctl:
        result = _run([systemctl, "is-active", "zerotier-one"])
        state = result["output"].strip().lower()
        if state in {"active", "inactive", "failed", "activating", "deactivating"}:
            return state

    # NetHunter/mobile Linux installations may not use systemd.
    pgrep = shutil.which("pgrep")
    if pgrep:
        result = _run([pgrep, "-x", "zerotier-one"])
        return "active" if result["success"] else "inactive"
    return "unknown"


def get_zerotier_status() -> dict[str, Any]:
    cli = shutil.which("zerotier-cli")
    service = _service_state()

    if not cli:
        return {
            "available": False,
            "state": "unavailable",
            "online": False,
            "service": service,
            "cli_available": False,
            "info": None,
            "networks": [],
            "peers": [],
            "errors": ["zerotier-cli command not found"],
            "read_only": True,
        }

    info = _json_command(cli, "info")
    networks = _json_command(cli, "listnetworks")
    peers = _json_command(cli, "listpeers")

    errors = [
        f"{name}: {item['error']}"
        for name, item in (
            ("info", info), ("networks", networks), ("peers", peers)
        )
        if not item["success"]
    ]
    info_data = info["data"] if isinstance(info["data"], dict) else {}
    online = info["success"] and info_data.get("online") is True

    # 'available' means the local daemon answered the CLI, not that it has
    # Internet connectivity or that a managed network is authorized.
    available = info["success"]
    if not available:
        state = "degraded" if service == "active" else "unavailable"
    else:
        state = "online" if online else "offline"

    return {
        "available": available,
        "state": state,
        "online": bool(online),
        "service": service,
        "cli_available": True,
        "info": info_data or None,
        "networks": networks["data"] if networks["success"] and isinstance(networks["data"], list) else [],
        "peers": peers["data"] if peers["success"] and isinstance(peers["data"], list) else [],
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
        "error": result["error"],
    }


def leave_network(network_id: str) -> dict[str, Any]:
    if not _NETWORK_ID_RE.fullmatch(network_id or ""):
        return {"success": False, "error": "Invalid ZeroTier network ID"}
    result = _run([_validated_cli(), "leave", network_id], timeout=20)
    return {
        "success": result["success"],
        "network_id": network_id,
        "output": result["output"],
        "error": result["error"],
    }


def get_zerotier_diagnostics(lines: int = 100) -> dict[str, Any]:
    # The API validates this too; keep the adapter safe when called directly.
    lines = max(1, min(int(lines), 200))
    systemctl = shutil.which("systemctl")
    journalctl = shutil.which("journalctl")

    service_status = (
        _run([systemctl, "status", "zerotier-one", "--no-pager", "-l"], timeout=8)
        if systemctl else {
            "success": False, "output": "",
            "error": "systemctl command not found; this system may not use systemd",
        }
    )
    logs = (
        _run([
            journalctl, "-u", "zerotier-one", "--no-pager",
            "-n", str(lines), "-o", "short-iso",
        ], timeout=12)
        if journalctl else {
            "success": False, "output": "", "error": "journalctl command not found",
        }
    )
    return {
        "service": _service_state(),
        "service_status": service_status["output"][-12000:],
        "service_status_error": service_status["error"],
        "logs": logs["output"][-30000:],
        "logs_error": logs["error"],
        "log_lines_requested": lines,
    }
