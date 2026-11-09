
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


def _frequency_to_channel(frequency: int) -> int | None:
    if frequency == 2484:
        return 14
    if 2412 <= frequency <= 2472:
        return (frequency - 2407) // 5
    if 5000 <= frequency <= 5895:
        return (frequency - 5000) // 5
    if 5955 <= frequency <= 7115:
        return (frequency - 5950) // 5
    return None


def _read_ip_forward() -> int | None:
    try:
        value = Path(
            "/proc/sys/net/ipv4/ip_forward"
        ).read_text().strip()
        return int(value)
    except (OSError, ValueError):
        return None



_IFACE_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,15}$")
_BSSID = re.compile(r"^(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")


def _split_nmcli_fields(line: str) -> list[str]:
    fields: list[str] = []
    value: list[str] = []
    escaped = False
    for char in line:
        if escaped:
            value.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(value))
            value = []
        else:
            value.append(char)
    fields.append("".join(value))
    return fields


def _managed_interface(interface: str) -> dict[str, Any] | None:
    if not isinstance(interface, str) or not _IFACE_NAME.fullmatch(interface):
        return None
    result = _run(["iw", "dev"])
    if not result["success"]:
        return None
    return next(
        (
            item for item in _parse_iw_dev(result["output"])
            if item.get("name") == interface and item.get("type") == "managed"
        ),
        None,
    )


def _active_ap_on_phy(phy: str | None) -> bool:
    if not phy:
        return False
    result = _run(["iw", "dev"])
    if not result["success"]:
        return True
    return any(
        item.get("phy") == phy
        and item.get("type") in ("AP", "P2P-GO")
        and item.get("ssid")
        for item in _parse_iw_dev(result["output"])
    )


def connect_wifi(
    interface: str,
    ssid: str,
    bssid: str,
    password: str = "",
    confirmed: bool = False,
) -> dict[str, Any]:
    """Connect a managed Wi-Fi interface to one network selected by scan."""
    if confirmed is not True:
        return {"success": False, "error": "Explicit administrator confirmation is required."}
    if not shutil.which("nmcli"):
        return {"success": False, "error": "NetworkManager (nmcli) is required to connect to Wi-Fi."}
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32:
        return {"success": False, "error": "Choose a valid Wi-Fi network name."}
    if not isinstance(bssid, str) or not _BSSID.fullmatch(bssid):
        return {"success": False, "error": "Choose a valid BSSID from the scan results."}
    if not isinstance(password, str) or len(password) > 63:
        return {"success": False, "error": "Wi-Fi password is too long."}

    station = _managed_interface(interface)
    if not station:
        return {"success": False, "error": "Choose an existing managed Wi-Fi interface; AP interfaces cannot be used as the upstream."}
    if _active_ap_on_phy(station.get("phy")):
        return {
            "success": False,
            "error": "Stop the active Hotspot before changing the Wi-Fi upstream. The AP and client share one radio.",
        }

    scan = _run([
        "nmcli", "--terse", "--escape", "yes",
        "--fields", "SSID,BSSID,SECURITY",
        "device", "wifi", "list", "ifname", interface,
    ], timeout=20)
    if not scan["success"]:
        return {"success": False, "error": "Could not verify the selected network. Scan Wi-Fi networks again."}

    selected = None
    for row in scan["output"].splitlines():
        fields = _split_nmcli_fields(row)
        if len(fields) >= 3 and fields[1].lower() == bssid.lower():
            selected = fields
            break
    if not selected or selected[0] != ssid:
        return {"success": False, "error": "The selected network is no longer in the scan results. Scan again."}

    security = (selected[2] or "").strip().lower()
    is_open = not security or security in ("--", "open")
    if not is_open and not 8 <= len(password) <= 63:
        return {"success": False, "error": "Enter the Wi-Fi password (8–63 characters)."}
    if is_open and password:
        return {"success": False, "error": "This network is open; no password is required."}

    command = [
        "nmcli", "--wait", "40", "device", "wifi", "connect",
        bssid, "ifname", interface,
    ]
    if not is_open:
        command.extend(["password", password])

    result = _run(command, timeout=45)
    if not result["success"]:
        return {
            "success": False,
            "error": "NetworkManager could not connect. Check the password, signal and network security, then try again.",
        }

    link = _run(["iw", "dev", interface, "link"])
    connected_ssid = None
    connected_bssid = None
    if link["success"]:
        for line in link["output"].splitlines():
            clean = line.strip()
            if clean.startswith("SSID:"):
                connected_ssid = clean.split(":", 1)[1].strip()
            elif clean.startswith("Connected to "):
                connected_bssid = clean.split()[-1].lower()

    if connected_ssid != ssid:
        return {
            "success": False,
            "error": "NetworkManager accepted the request, but the Wi-Fi connection could not be verified.",
        }
    return {
        "success": True,
        "connected": True,
        "interface": interface,
        "ssid": connected_ssid,
        "bssid": connected_bssid or bssid.lower(),
        "message": "Wi-Fi connected. You can now share this connection through the Keyland Hotspot.",
    }


def disconnect_wifi(interface: str, confirmed: bool = False) -> dict[str, Any]:
    """Disconnect only the selected managed Wi-Fi client interface."""
    if confirmed is not True:
        return {"success": False, "error": "Explicit administrator confirmation is required."}
    if not shutil.which("nmcli"):
        return {"success": False, "error": "NetworkManager (nmcli) is required to disconnect Wi-Fi."}

    station = _managed_interface(interface)
    if not station:
        return {"success": False, "error": "Choose an existing managed Wi-Fi interface."}
    if _active_ap_on_phy(station.get("phy")):
        return {
            "success": False,
            "error": "Stop the active Hotspot before disconnecting the upstream Wi-Fi. The AP and client share one radio.",
        }

    link = _run(["iw", "dev", interface, "link"])
    if link["success"] and "Not connected." in link["output"]:
        return {"success": True, "connected": False, "interface": interface, "already_disconnected": True}

    result = _run(["nmcli", "--wait", "20", "device", "disconnect", interface], timeout=25)
    if not result["success"]:
        return {"success": False, "error": "NetworkManager could not disconnect the selected Wi-Fi interface."}
    return {"success": True, "connected": False, "interface": interface, "message": "Wi-Fi disconnected."}


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

    # "iw dev" often omits the SSID for managed client interfaces.
    # Read the live association from "iw dev <interface> link" so
    # the repeater UI reflects the actual upstream connection.
    for item in interfaces:
        if item.get("type") not in ("managed", "P2P-client"):
            continue

        link = _run(["iw", "dev", item["name"], "link"])
        if not link.get("success"):
            continue

        link_output = link.get("output", "")
        if link_output.lower().startswith("not connected"):
            item["ssid"] = None
            continue

        for raw_line in link_output.splitlines():
            line = raw_line.strip()
            if line.startswith("Connected to "):
                match = re.search(
                    r"Connected to ([0-9a-fA-F:]{17})",
                    line,
                )
                if match:
                    item["bssid"] = match.group(1).lower()
            elif line.startswith("SSID:"):
                item["ssid"] = line.split(":", 1)[1].strip() or None
            elif line.startswith("freq:"):
                match = re.search(r"(\\d{4,5})", line)
                if match:
                    item["frequency_mhz"] = int(match.group(1))
                    item["channel"] = _frequency_to_channel(item["frequency_mhz"])
            elif line.startswith("signal:"):
                match = re.search(r"-?\\d+(?:\\.\\d+)?", line)
                if match:
                    item["signal_dbm"] = float(match.group())

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
