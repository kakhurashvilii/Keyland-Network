
"""
Read-only Wi-Fi repeater readiness inspection.

This module does not configure interfaces, routes, DHCP, NAT,
firewall rules, or NetworkManager connections.
"""

from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

SYSTEM = platform.system().lower()


def _run(args: list[str], timeout: int = 8) -> dict[str, Any]:
    """Run a fixed diagnostic command without invoking a shell."""
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


def _parse_iw_dev(output: str) -> list[dict[str, Any]]:
    """Parse `iw dev` output."""
    interfaces: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_phy: str | None = None

    for raw in output.splitlines():
        line = raw.strip()

        if line.startswith("phy#"):
            current_phy = line

        elif line.startswith("Interface "):
            if current is not None:
                interfaces.append(current)

            current = {
                "name": line.split(maxsplit=1)[1],
                "phy": current_phy,
                "type": None,
                "ssid": None,
                "channel": None,
                "frequency_mhz": None,
            }

        elif current is not None:
            if line.startswith("type "):
                current["type"] = line.split(maxsplit=1)[1]

            elif line.startswith("ssid "):
                current["ssid"] = line[5:].strip()

            else:
                match = re.search(
                    r"\bchannel\s+(\d+)\s+\((\d+)\s+MHz\)",
                    line,
                    re.IGNORECASE,
                )
                if match:
                    current["channel"] = int(match.group(1))
                    current["frequency_mhz"] = int(match.group(2))

    if current is not None:
        interfaces.append(current)

    return interfaces


def _read_ip_forward() -> int | None:
    try:
        value = Path(
            "/proc/sys/net/ipv4/ip_forward"
        ).read_text().strip()
        return int(value)
    except (OSError, ValueError):
        return None


def get_repeater_status() -> dict[str, Any]:
    """Report observed Wi-Fi roles and basic forwarding prerequisites."""

    if SYSTEM != "linux":
        return {
            "platform": SYSTEM,
            "supported": False,
            "available": False,
            "message": "Repeater inspection is currently Linux-only.",
        }

    missing = [
        tool for tool in ("iw", "ip")
        if shutil.which(tool) is None
    ]

    if missing:
        return {
            "platform": SYSTEM,
            "supported": True,
            "available": False,
            "error": "Missing diagnostic tools: " + ", ".join(missing),
        }

    iw_result = _run(["iw", "dev"])

    if not iw_result["success"]:
        return {
            "platform": SYSTEM,
            "supported": True,
            "available": False,
            "error": iw_result["error"]
            or "Could not inspect wireless interfaces.",
        }

    interfaces = _parse_iw_dev(iw_result["output"])

    ap_interfaces = [
        item for item in interfaces
        if item["type"] in ("AP", "P2P-GO") and item["ssid"]
    ]

    client_interfaces = [
        item for item in interfaces
        if item["type"] in ("managed", "P2P-client")
        and item["ssid"]
    ]

    ip_result = _run(["ip", "-j", "addr", "show"])
    addresses: dict[str, list[str]] = {}

    if ip_result["success"] and ip_result["output"]:
        try:
            for item in json.loads(ip_result["output"]):
                addresses[item.get("ifname", "")] = [
                    addr["local"]
                    for addr in item.get("addr_info", [])
                    if addr.get("local")
                ]
        except (json.JSONDecodeError, TypeError, KeyError):
            addresses = {}

    channel_pairs = [
        (ap.get("channel"), client.get("channel"))
        for ap in ap_interfaces
        for client in client_interfaces
        if ap.get("channel") is not None
        and client.get("channel") is not None
    ]

    same_channel = (
        all(ap_ch == client_ch for ap_ch, client_ch in channel_pairs)
        if channel_pairs else None
    )

    # This only confirms that both roles are currently visible.
    # It does not prove that the hardware supports stable concurrency.
    active_roles_observed = bool(
        ap_interfaces and client_interfaces
    )

    # A stronger observation: both roles are reported on the same PHY.
    shared_phy_observed = any(
        ap["phy"] is not None
        and ap["phy"] == client["phy"]
        for ap in ap_interfaces
        for client in client_interfaces
    )

    ip_forward = _read_ip_forward()

    firewall = (
        "nft" if shutil.which("nft")
        else "iptables" if shutil.which("iptables")
        else None
    )

    dnsmasq_available = shutil.which("dnsmasq") is not None

    forwarding_prerequisites = {
        "ip_forward_enabled": ip_forward == 1,
        "firewall_tool_available": firewall is not None,
        "dnsmasq_available": dnsmasq_available,
    }

    forwarding_ready = all(forwarding_prerequisites.values())

    if active_roles_observed:
        assessment = "ap_and_client_roles_currently_observed"
    elif ap_interfaces or client_interfaces:
        assessment = "only_one_wifi_role_observed"
    else:
        assessment = "no_active_ap_or_client_ssid_observed"

    return {
        "platform": SYSTEM,
        "supported": True,
        "available": True,
        "read_only": True,
        "assessment": assessment,
        "radio": {
            "concurrent_ap_client_observed": active_roles_observed,
            "shared_phy_observed": shared_phy_observed,
            "same_channel_observed": same_channel,
            "access_points": [
                {
                    **item,
                    "ipv4": addresses.get(item["name"], []),
                }
                for item in ap_interfaces
            ],
            "clients": [
                {
                    **item,
                    "ipv4": addresses.get(item["name"], []),
                }
                for item in client_interfaces
            ],
            "interfaces": interfaces,
            "note": (
                "Observed AP and client interfaces do not prove "
                "that the wireless chipset supports reliable "
                "simultaneous operation. Check iw list interface "
                "combinations for hardware/driver capability."
            ),
        },
        "forwarding": {
            "ip_forward": ip_forward,
            "firewall_tool": firewall,
            "dnsmasq_available": dnsmasq_available,
            "prerequisites": forwarding_prerequisites,
            "basic_prerequisites_ready": forwarding_ready,
            "note": (
                "Prerequisites do not prove NAT, DHCP, DNS, firewall "
                "rules, or end-to-end repeater traffic are working."
            ),
        },
        "message": (
            "Inspection only. No network configuration was changed."
        ),
    }
