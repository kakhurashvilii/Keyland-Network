
"""
Keyland-Network Cellular Monitor v0.4.0

Read-only SIM / LTE / 4G / 5G monitoring.
No modem, connection, route, or firewall configuration is changed.
"""

from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
from typing import Any

try:
    import psutil
except ImportError:
    psutil = None


SYSTEM = platform.system().lower()

CELLULAR_PREFIXES = (
    "wwan",
    "rmnet",
    "qmap",
    "ppp",
    "cellular",
    "usb",
)


def _run(args: list[str], timeout: int = 10) -> dict[str, Any]:
    """Run a fixed diagnostic command without a shell."""
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            errors="replace",
        )
        return {
            "success": result.returncode == 0,
            "output": result.stdout.strip(),
            "error": result.stderr.strip() or None,
        }
    except FileNotFoundError:
        return {
            "success": False,
            "output": "",
            "error": "Command not found",
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "output": "",
            "error": "Command timed out",
        }
    except OSError as exc:
        return {
            "success": False,
            "output": "",
            "error": str(exc),
        }


def _load_json(output: str) -> dict[str, Any]:
    try:
        value = json.loads(output)
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _find_value(data: Any, names: tuple[str, ...]) -> Any:
    """Find a value recursively using normalized JSON keys."""
    wanted = {
        re.sub(r"[^a-z0-9]", "", name.lower())
        for name in names
    }

    if isinstance(data, dict):
        for key, value in data.items():
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            if normalized in wanted:
                return value

        for value in data.values():
            found = _find_value(value, names)
            if found is not None:
                return found

    elif isinstance(data, list):
        for value in data:
            found = _find_value(value, names)
            if found is not None:
                return found

    return None


def _as_text(value: Any) -> str | None:
    if value is None:
        return None

    if isinstance(value, (str, int, float)):
        return str(value)

    if isinstance(value, list):
        return ", ".join(str(item) for item in value)

    return None


def _signal_value(value: Any) -> int | None:
    if isinstance(value, dict):
        value = value.get("value")

    try:
        result = int(value)
        return max(0, min(100, result))
    except (TypeError, ValueError):
        return None


def _network_type(access: Any) -> str:
    if isinstance(access, list):
        text = " ".join(str(item) for item in access).lower()
    else:
        text = str(access or "").lower()

    if any(item in text for item in ("5gnr", "5g", "nr5g")):
        return "5G"
    if "lte" in text or "4g" in text:
        return "4G LTE"
    if "hspa" in text or "hsdpa" in text or "hsupa" in text:
        return "3G HSPA"
    if "umts" in text or "wcdma" in text:
        return "3G"
    if "edge" in text or "gprs" in text:
        return "2G"
    if "gsm" in text:
        return "2G GSM"

    return "Unknown"


def _get_modem_indices() -> list[str]:
    result = _run(["mmcli", "-L"])

    if not result["success"]:
        return []

    return sorted(
        set(
            re.findall(
                r"/Modem/(\d+)",
                result["output"],
            )
        ),
        key=int,
    )


def _get_modem_data(index: str) -> dict[str, Any]:
    result = _run([
        "mmcli",
        "--output-json",
        "-m",
        index,
    ])

    if not result["success"]:
        return {
            "available": False,
            "error": result["error"] or "Could not read modem.",
        }

    data = _load_json(result["output"])

    if not data:
        return {
            "available": False,
            "error": "ModemManager returned invalid JSON.",
        }

    manufacturer = _find_value(data, ("manufacturer",))
    model = _find_value(data, ("model",))
    state = _find_value(data, ("state",))
    signal = _find_value(data, ("signal-quality", "signal_quality"))
    access = _find_value(data, ("access-technologies",))
    operator = _find_value(data, ("operator-name",))
    operator_code = _find_value(data, ("operator-code",))
    registration = _find_value(data, ("registration-state",))
    packet_state = _find_value(data, ("packet-service-state",))
    sim_path = _find_value(data, ("sim",))

    sim_id = None
    if isinstance(sim_path, str):
        match = re.search(r"/SIM/(\d+)", sim_path)
        if match:
            sim_id = match.group(1)

    sim_data: dict[str, Any] = {}

    if sim_id is not None:
        sim_result = _run([
            "mmcli",
            "--output-json",
            "-i",
            sim_id,
        ])

        if sim_result["success"]:
            sim_data = _load_json(sim_result["output"])

    sim_state = _find_value(sim_data, ("state",))
    sim_operator = _find_value(sim_data, ("operator-name",))

    if sim_operator:
        operator = sim_operator

    return {
        "available": True,
        "modem_id": index,
        "manufacturer": _as_text(manufacturer),
        "model": _as_text(model),
        "state": _as_text(state),
        "sim": {
            "detected": sim_id is not None,
            "state": _as_text(sim_state),
        },
        "operator": {
            "name": _as_text(operator),
            "code": _as_text(operator_code),
        },
        "network": {
            "technology": _network_type(access),
            "access_technologies": access,
            "registration_state": _as_text(registration),
            "packet_service_state": _as_text(packet_state),
        },
        "signal": {
            "quality_percent": _signal_value(signal),
        },
    }


def _get_cellular_interfaces() -> list[dict[str, Any]]:
    if psutil is None:
        return []

    try:
        counters = psutil.net_io_counters(pernic=True)
        addresses = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
    except Exception:
        return []

    result = []

    for name, counter in counters.items():
        if not name.lower().startswith(CELLULAR_PREFIXES):
            continue

        ipv4 = []
        ipv6 = []

        for address in addresses.get(name, []):
            if address.family.name == "AF_INET":
                ipv4.append(address.address)
            elif address.family.name == "AF_INET6":
                ipv6.append(address.address)

        interface_stats = stats.get(name)

        result.append({
            "name": name,
            "is_up": (
                interface_stats.isup
                if interface_stats else None
            ),
            "ipv4": ipv4,
            "ipv6": ipv6,
            "rx_bytes": counter.bytes_recv,
            "tx_bytes": counter.bytes_sent,
            "rx_packets": counter.packets_recv,
            "tx_packets": counter.packets_sent,
        })

    return result


def get_cellular_status() -> dict[str, Any]:
    """Return read-only cellular modem, SIM and interface information."""

    if SYSTEM != "linux":
        return {
            "version": "0.4.0",
            "platform": SYSTEM,
            "supported": False,
            "available": False,
            "message": (
                "Cellular modem monitoring requires Linux "
                "with ModemManager."
            ),
        }

    if shutil.which("mmcli") is None:
        return {
            "version": "0.4.0",
            "platform": SYSTEM,
            "supported": True,
            "available": False,
            "modemmanager": False,
            "error": "mmcli not found. Install ModemManager.",
        }

    modem_indices = _get_modem_indices()

    if not modem_indices:
        return {
            "version": "0.4.0",
            "platform": SYSTEM,
            "supported": True,
            "available": True,
            "modemmanager": True,
            "modems": [],
            "interfaces": _get_cellular_interfaces(),
            "message": "No modem detected by ModemManager.",
        }

    modems = [
        _get_modem_data(index)
        for index in modem_indices
    ]

    interfaces = _get_cellular_interfaces()

    connected = any(
        item.get("is_up") is True
        and any(item.get("ipv4", []))
        for item in interfaces
    )

    return {
        "version": "0.4.0",
        "platform": SYSTEM,
        "supported": True,
        "available": True,
        "modemmanager": True,
        "connection_observed": connected,
        "modems": modems,
        "interfaces": interfaces,
        "read_only": True,
        "message": (
            "Read-only inspection. No modem or network "
            "configuration was changed."
        ),
    }


def get_cellular_signal() -> dict[str, Any]:
    """Return signal information from detected modems."""
    status = get_cellular_status()

    return {
        "available": status.get("available", False),
        "modems": [
            {
                "modem_id": modem.get("modem_id"),
                "signal": modem.get("signal"),
                "network": modem.get("network"),
            }
            for modem in status.get("modems", [])
        ],
    }


def get_cellular_usage() -> dict[str, Any]:
    """Return interface byte counters; these are cumulative counters."""
    status = get_cellular_status()

    return {
        "available": status.get("available", False),
        "interfaces": [
            {
                "name": item["name"],
                "rx_bytes": item["rx_bytes"],
                "tx_bytes": item["tx_bytes"],
                "rx_packets": item["rx_packets"],
                "tx_packets": item["tx_packets"],
            }
            for item in status.get("interfaces", [])
        ],
        "note": (
            "Counters are cumulative since interface initialization "
            "or the last system counter reset."
        ),
    }
