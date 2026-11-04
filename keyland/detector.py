
import platform
import shutil
import os
from pathlib import Path


def detect_device():
    system = platform.system().lower()

    if Path("/etc/openwrt_release").exists():
        device_type = "openwrt"
    elif "ANDROID_ROOT" in os.environ:
        device_type = "android"
    elif system == "linux":
        device_type = "linux"
    elif system == "windows":
        device_type = "windows"
    elif system == "darwin":
        device_type = "macos"
    else:
        device_type = "unknown"

    return {
        "platform": device_type,
        "system": platform.system(),
        "release": platform.release(),
        "architecture": platform.machine(),
        "hostname": platform.node(),
        "python": platform.python_version()
    }


def detect_capabilities():
    system = platform.system().lower()

    if system == "windows":
        commands = [
            "netsh",
            "route",
            "ipconfig",
            "powershell",
            "sc"
        ]

        return {
            "platform": "windows",
            "interfaces": True,
            "routes": shutil.which("route") is not None,
            "wifi": shutil.which("netsh") is not None,
            "network_manager": False,
            "lte": False,
            "commands": {
                name: shutil.which(name) is not None
                for name in commands
            }
        }

    commands = [
        "ip", "iw", "nmcli", "nft",
        "iptables", "dnsmasq", "mmcli",
        "ubus", "uci", "hostapd"
    ]

    available = {
        name: shutil.which(name) is not None
        for name in commands
    }

    return {
        "platform": system,
        "interfaces": True,
        "routes": available.get("ip", False),
        "wifi": available.get("iw", False),
        "network_manager": available.get("nmcli", False),
        "lte": available.get("mmcli", False),
        "openwrt": available.get("ubus", False),
        "commands": available
    }
