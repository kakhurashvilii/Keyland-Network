
import platform
import re
import shutil
import subprocess
from typing import Any


def run_readonly(command: list[str], timeout: int = 15) -> dict[str, Any]:
    """Run a fixed diagnostic command without changing system state."""

    if not command or not shutil.which(command[0]):
        return {
            "success": False,
            "output": "",
            "error": f"Command not found: {command[0] if command else 'unknown'}",
        }

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            errors="replace",
        )

        return {
            "success": result.returncode == 0,
            "output": result.stdout or "",
            "error": (
                result.stderr.strip()
                or (
                    f"Command exited with code {result.returncode}"
                    if result.returncode else None
                )
            ),
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


def parse_linux_interfaces(output: str) -> list[dict[str, Any]]:
    interfaces = []
    current = None
    phy = None

    for raw in output.splitlines():
        line = raw.strip()

        if line.startswith("phy#"):
            if current:
                interfaces.append(current)
                current = None
            phy = line

        elif line.startswith("Unnamed/non-netdev interface"):
            if current:
                interfaces.append(current)
                current = None

        elif line.startswith("Interface "):
            if current:
                interfaces.append(current)

            current = {
                "name": line.split(maxsplit=1)[1],
                "phy": phy,
                "type": None,
                "ssid": None,
                "channel": None,
                "frequency_mhz": None,
            }

        elif current and line.startswith("type "):
            current["type"] = line.split(maxsplit=1)[1]

        elif current and line.startswith("wiphy "):
            current["phy"] = line.split(maxsplit=1)[1]

        elif current and line.startswith("ssid "):
            current["ssid"] = line[5:].strip()

        elif current:
            match = re.search(
                r"channel\s+(\d+)\s+\((\d+)\s+MHz\)",
                line,
                re.IGNORECASE,
            )

            if match:
                current["channel"] = int(match.group(1))
                current["frequency_mhz"] = int(match.group(2))

    if current:
        interfaces.append(current)

    return interfaces


def parse_supported_modes(output: str) -> list[str]:
    modes = []
    in_modes = False

    for raw in output.splitlines():
        line = raw.strip()

        if line == "Supported interface modes:":
            in_modes = True
            continue

        if in_modes:
            if line.startswith("*"):
                mode = line.lstrip("* ").strip()

                if mode and mode not in modes:
                    modes.append(mode)

            elif line:
                in_modes = False

    return modes


def parse_channels(output: str) -> tuple[list[dict], list[str]]:
    channels = []
    bands = set()

    for line in output.splitlines():
        match = re.search(
            r"\*\s+(\d+)\s+MHz\s+\[(\d+)\]",
            line,
        )

        if not match:
            continue

        frequency = int(match.group(1))
        channel = int(match.group(2))

        if 2400 <= frequency < 2500:
            bands.add("2.4 GHz")
        elif 4900 <= frequency < 5925:
            bands.add("5 GHz")
        elif 5925 <= frequency <= 7125:
            bands.add("6 GHz")

        channels.append({
            "frequency_mhz": frequency,
            "channel": channel,
        })

    return channels, sorted(bands)



def parse_interface_combinations(output: str) -> dict[str, Any]:
    """Parse valid wireless interface combinations reported by iw."""

    lines = output.splitlines()
    combinations = []
    in_section = False

    for raw in lines:
        line = raw.strip()

        if line.startswith("valid interface combinations:"):
            in_section = True
            continue

        if in_section:
            if not line:
                continue
            if line.endswith(":") and not line.startswith("#"):
                in_section = False
                continue
            combinations.append(line)

    groups = []
    current = ""

    for line in combinations:
        if line.startswith("*"):
            if current:
                groups.append(current)
            current = line
        elif current:
            current += " " + line

    if current:
        groups.append(current)

    compatible = []

    for group in groups:
        limits = {}

        for match in re.finditer(
            r"#\s*\{([^}]*)\}\s*<=\s*(\d+)",
            group
        ):
            modes = [item.strip() for item in match.group(1).split(",")]
            limit = int(match.group(2))

            for mode in modes:
                limits[mode] = max(limits.get(mode, 0), limit)

        if limits.get("managed", 0) >= 1 and limits.get("AP", 0) >= 1:
            compatible.append(group)

    channel_limits = []
    for group in compatible:
        match = re.search(r"#channels\s*<=\s*(\d+)", group)
        if match:
            channel_limits.append(int(match.group(1)))

    return {
        "reported": bool(combinations),
        "ap_client_combination_mentioned": bool(compatible),
        "details": combinations,
        "compatible_combinations": compatible,
        "max_channels_for_ap_client": max(channel_limits, default=None),
        "simultaneous_ap_client": (
            "driver_reports_combination" if compatible else "unknown"
        ),
        "note": (
            "Driver-reported AP + managed concurrency is not a runtime "
            "guarantee. PHY assignment, channel limits, firmware and "
            "actual testing must also be considered."
        ),
    }


def detect_linux_wifi_capabilities() -> dict[str, Any]:
    if not shutil.which("iw"):
        return {
            "available": False,
            "platform": "linux",
            "interfaces": [],
            "error": "iw is not installed",
        }

    devices = run_readonly(["iw", "dev"])
    phy_info = run_readonly(["iw", "list"], timeout=25)

    interfaces = (
        parse_linux_interfaces(devices["output"])
        if devices["success"] else []
    )

    if not phy_info["success"]:
        return {
            "available": bool(interfaces),
            "platform": "linux",
            "interfaces": interfaces,
            "capabilities": None,
            "errors": {
                "interfaces": devices["error"],
                "phy": phy_info["error"],
            },
        }

    output = phy_info["output"]

    modes = parse_supported_modes(output)
    channels, bands = parse_channels(output)
    combinations = parse_interface_combinations(output)

    return {
        "available": True,
        "platform": "linux",
        "interfaces": interfaces,
        "capabilities": {
            "access_point": "AP" in modes,
            "client": "managed" in modes,
            "monitor": "monitor" in modes,
            "supported_modes": modes,
            "bands": bands,
            "channels": channels,
            "concurrent_modes": combinations,
        },
        "errors": {
            "interfaces": devices["error"],
            "phy": None,
        },
        "note": (
            "Capabilities are reported by the wireless driver. "
            "Actual operation also depends on firmware, regulatory "
            "rules, hardware and PHY interface combinations."
        ),
    }


def detect_windows_wifi_capabilities() -> dict[str, Any]:
    result = run_readonly([
        "netsh",
        "wlan",
        "show",
        "drivers",
    ])

    if not result["success"]:
        return {
            "available": False,
            "platform": "windows",
            "interfaces": [],
            "error": result["error"],
        }

    output = result["output"]

    radio_types = None
    hosted_network = None

    for line in output.splitlines():
        normalized = line.strip().lower()

        if "radio types supported" in normalized and ":" in line:
            radio_types = line.split(":", 1)[1].strip()

        if "hosted network supported" in normalized and ":" in line:
            value = line.split(":", 1)[1].strip().lower()

            if value in ("yes", "no"):
                hosted_network = value == "yes"

    bands = []

    if radio_types:
        if re.search(r"802\.11[abgn]", radio_types, re.I):
            bands.append("2.4 GHz")

        if re.search(r"802\.11[acax]", radio_types, re.I):
            bands.append("5 GHz")

    return {
        "available": True,
        "platform": "windows",
        "interfaces": [],
        "capabilities": {
            "access_point": hosted_network,
            "legacy_hosted_network": hosted_network,
            "client": True,
            "monitor": None,
            "supported_modes": None,
            "bands": bands,
            "channels": [],
            "concurrent_modes": {
                "simultaneous_ap_client": "unknown",
                "note": (
                    "Windows driver information does not prove "
                    "simultaneous AP and Client operation."
                ),
            },
        },
        "driver_information": output,
        "note": (
            "Windows Hosted Network support does not guarantee "
            "Mobile Hotspot or simultaneous AP/client operation."
        ),
    }


def get_wifi_capabilities() -> dict[str, Any]:
    system = platform.system().lower()

    if system == "linux":
        return detect_linux_wifi_capabilities()

    if system == "windows":
        return detect_windows_wifi_capabilities()

    return {
        "available": False,
        "platform": system,
        "interfaces": [],
        "error": "Wi-Fi capability detection is not implemented for this OS",
    }
