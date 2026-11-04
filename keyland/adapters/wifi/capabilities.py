import platform
import re
import shutil
import subprocess


def run_readonly(command: list[str], timeout: int = 15) -> dict:
    """Run a fixed, read-only system inspection command."""
    if not shutil.which(command[0]):
        return {
            "success": False,
            "output": "",
            "error": f"Required command not found: {command[0]}",
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

        output = result.stdout or ""
        error = result.stderr or ""

        return {
            "success": result.returncode == 0,
            "output": output,
            "error": error.strip() or (
                f"Command exited with code {result.returncode}"
                if result.returncode else None
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


def detect_linux_wifi_capabilities() -> dict:
    if not shutil.which("iw"):
        return {
            "available": False,
            "platform": "linux",
            "interfaces": [],
            "error": "iw is not installed",
            "note": "Install iw to inspect wireless capabilities.",
        }

    devices = run_readonly(["iw", "dev"])
    phy_info = run_readonly(["iw", "list"], timeout=25)

    interfaces = []
    current = None

    if devices["success"]:
        for raw in devices["output"].splitlines():
            line = raw.strip()

            if line.startswith("Interface "):
                if current:
                    interfaces.append(current)

                current = {
                    "name": line.split(maxsplit=1)[1],
                    "type": None,
                    "phy": None,
                }

            elif current and line.startswith("type "):
                current["type"] = line.split(maxsplit=1)[1]

            elif current and line.startswith("wiphy "):
                current["phy"] = line.split(maxsplit=1)[1]

        if current:
            interfaces.append(current)

    modes = []
    bands = set()
    channels = []

    if phy_info["success"]:
        in_modes = False

        for raw in phy_info["output"].splitlines():
            line = raw.strip()

            if line == "Supported interface modes:":
                in_modes = True
                continue

            if in_modes:
                if line.startswith("*"):
                    mode = line.lstrip("* ").strip()
                    if mode and mode not in modes:
                        modes.append(mode)
                elif line and not line.startswith("*"):
                    in_modes = False

            match = re.search(r"\*\s+(\d+)\s+MHz\s+\[(\d+)\]", line)
            if match:
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

    return {
        "available": bool(interfaces) or phy_info["success"],
        "platform": "linux",
        "interfaces": interfaces,
        "capabilities": {
            "access_point": "AP" in modes,
            "client": "managed" in modes,
            "monitor": "monitor" in modes,
            "supported_modes": modes,
            "bands": sorted(bands),
            "channels": channels,
        },
        "errors": {
            "interfaces": devices["error"],
            "phy": phy_info["error"],
        },
        "note": (
            "Capabilities are driver-reported. Actual operation may "
            "also depend on regulatory rules, firmware and hardware."
        ),
    }


def detect_windows_wifi_capabilities() -> dict:
    result = run_readonly([
        "netsh", "wlan", "show", "drivers"
    ])

    if not result["success"]:
        return {
            "available": False,
            "platform": "windows",
            "interfaces": [],
            "error": result["error"],
        }

    output = result["output"]

    # Windows output may be localized. If a field cannot be identified,
    # report unknown instead of guessing.
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
            "access_point": True if hosted_network else None,
            "legacy_hosted_network": hosted_network,
            "client": True,
            "monitor": None,
            "supported_modes": None,
            "bands": bands,
            "channels": [],
        },
        "driver_information": output,
        "note": (
            "Windows driver output is used for a preliminary check. "
            "Hosted Network support does not guarantee Mobile Hotspot "
            "or simultaneous AP/client operation."
        ),
    }


def get_wifi_capabilities() -> dict:
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
