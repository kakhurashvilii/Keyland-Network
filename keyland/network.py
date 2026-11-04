
import json
import platform
import shutil
import socket
import subprocess
import threading
import time

import psutil


SYSTEM = platform.system().lower()

# Previous interface counters used to calculate real traffic rates.
_traffic_lock = threading.Lock()
_previous_counters = {}
_previous_time = None


def run_command(args, timeout=8):
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
            "error": result.stderr.strip(),
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


def get_interfaces():
    result = []
    addresses = psutil.net_if_addrs()
    statistics = psutil.net_if_stats()

    for name, addr_list in addresses.items():
        stat = statistics.get(name)

        interface = {
            "name": name,
            "is_up": stat.isup if stat else False,
            "speed_mbps": stat.speed if stat else None,
            "mtu": stat.mtu if stat else None,
            "addresses": [],
        }

        for address in addr_list:
            family = address.family

            if family == socket.AF_INET:
                family_name = "IPv4"
            elif family == socket.AF_INET6:
                family_name = "IPv6"
            else:
                family_name = "MAC"

            interface["addresses"].append({
                "family": family_name,
                "address": address.address,
                "netmask": address.netmask,
            })

        result.append(interface)

    return result


def get_traffic():
    """Return per-interface RX/TX counters and calculated Mbps rates."""
    global _previous_counters, _previous_time

    now = time.monotonic()
    counters = psutil.net_io_counters(pernic=True)

    with _traffic_lock:
        elapsed = (
            now - _previous_time
            if _previous_time is not None
            else 0
        )

        interfaces = []

        for name, current in counters.items():
            previous = _previous_counters.get(name)

            rx_rate = 0.0
            tx_rate = 0.0

            if previous is not None and elapsed > 0:
                rx_delta = max(
                    0, current.bytes_recv - previous.bytes_recv
                )
                tx_delta = max(
                    0, current.bytes_sent - previous.bytes_sent
                )

                rx_rate = (rx_delta * 8) / elapsed / 1_000_000
                tx_rate = (tx_delta * 8) / elapsed / 1_000_000

            interfaces.append({
                "name": name,
                "bytes_recv": current.bytes_recv,
                "bytes_sent": current.bytes_sent,
                "packets_recv": current.packets_recv,
                "packets_sent": current.packets_sent,
                "errors_in": current.errin,
                "errors_out": current.errout,
                "drops_in": current.dropin,
                "drops_out": current.dropout,
                "rx_mbps": round(rx_rate, 4),
                "tx_mbps": round(tx_rate, 4),
            })

        _previous_counters = {
            name: value for name, value in counters.items()
        }
        _previous_time = now

    return {
        "platform": SYSTEM,
        "sample_interval_seconds": round(elapsed, 3),
        "unit": "Mbps",
        "interfaces": interfaces,
    }


def get_system_resources():
    """Read CPU, memory, boot time and disk usage."""
    memory = psutil.virtual_memory()
    disk_path = "C:\\" if SYSTEM == "windows" else "/"

    try:
        disk = psutil.disk_usage(disk_path)
        disk_data = {
            "path": disk_path,
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
            "percent": disk.percent,
        }
    except (OSError, PermissionError):
        disk_data = None

    return {
        "platform": SYSTEM,
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "cpu_logical_cores": psutil.cpu_count(logical=True),
        "cpu_physical_cores": psutil.cpu_count(logical=False),
        "memory": {
            "total_bytes": memory.total,
            "available_bytes": memory.available,
            "used_bytes": memory.used,
            "percent": memory.percent,
        },
        "disk": disk_data,
        "boot_time": psutil.boot_time(),
    }


def get_routes():
    if SYSTEM == "windows":
        command = (
            "Get-NetRoute -AddressFamily IPv4 "
            "| Select-Object DestinationPrefix,NextHop,"
            "InterfaceIndex,RouteMetric,Protocol "
            "| ConvertTo-Json -Depth 3 -Compress"
        )

        result = run_command([
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
        ])

        if not result["success"] or not result["output"]:
            return {
                "platform": SYSTEM,
                "available": False,
                "routes": [],
                "error": result["error"],
            }

        try:
            routes = json.loads(result["output"])
            if isinstance(routes, dict):
                routes = [routes]

            return {
                "platform": SYSTEM,
                "available": True,
                "count": len(routes),
                "routes": routes,
            }
        except json.JSONDecodeError:
            return {
                "platform": SYSTEM,
                "available": False,
                "routes": [],
                "error": "Could not parse PowerShell JSON",
            }

    if SYSTEM == "linux":
        if not shutil.which("ip"):
            return {
                "platform": SYSTEM,
                "available": False,
                "routes": [],
            }

        result = run_command(["ip", "-j", "route", "show"])

        if not result["success"]:
            return {
                "platform": SYSTEM,
                "available": False,
                "routes": [],
                "error": result["error"],
            }

        try:
            routes = json.loads(result["output"])
            return {
                "platform": SYSTEM,
                "available": True,
                "count": len(routes),
                "routes": routes,
            }
        except json.JSONDecodeError:
            return {
                "platform": SYSTEM,
                "available": False,
                "routes": [],
                "error": "Could not parse route JSON",
            }

    return {
        "platform": SYSTEM,
        "available": False,
        "routes": [],
    }


def get_wifi():
    if SYSTEM == "windows":
        result = run_command([
            "netsh", "wlan", "show", "interfaces"
        ])

        if not result["success"]:
            return {
                "platform": SYSTEM,
                "available": False,
                "interfaces": [],
                "error": result["error"],
            }

        interfaces = []
        current = {}

        for line in result["output"].splitlines():
            line = line.strip()

            if not line:
                continue

            if line.startswith("Name"):
                if current:
                    interfaces.append(current)
                current = {}

            if ":" in line:
                key, value = line.split(":", 1)
                current[key.strip()] = value.strip()

        if current:
            interfaces.append(current)

        # Ignore the introductory "There is N interface..." line.
        interfaces = [
            item for item in interfaces
            if "Name" in item
        ]

        return {
            "platform": SYSTEM,
            "available": True,
            "count": len(interfaces),
            "interfaces": interfaces,
        }

    if SYSTEM == "linux":
        if not shutil.which("iw"):
            return {
                "platform": SYSTEM,
                "available": False,
                "interfaces": [],
                "error": "iw command not found",
            }

        result = run_command(["iw", "dev"])

        return {
            "platform": SYSTEM,
            "available": result["success"],
            "details": result["output"],
            "error": result["error"],
        }

    return {
        "platform": SYSTEM,
        "available": False,
        "interfaces": [],
    }


def get_wifi_scan():
    """Scan nearby Wi-Fi networks where the OS and driver permit it."""
    if SYSTEM == "windows":
        result = run_command([
            "netsh", "wlan", "show", "networks", "mode=bssid"
        ], timeout=20)

        if not result["success"]:
            return {
                "platform": SYSTEM,
                "available": False,
                "networks": [],
                "error": result["error"],
            }

        networks = []
        current = None
        current_bssid = None

        for raw_line in result["output"].splitlines():
            line = raw_line.strip()

            if line.startswith("SSID ") and ":" in line:
                if current:
                    networks.append(current)

                key, value = line.split(":", 1)
                current = {
                    "ssid": value.strip(),
                    "authentication": None,
                    "encryption": None,
                    "bssids": [],
                }
                current_bssid = None

            elif current and line.startswith("Authentication") and ":" in line:
                current["authentication"] = line.split(":", 1)[1].strip()

            elif current and line.startswith("Encryption") and ":" in line:
                current["encryption"] = line.split(":", 1)[1].strip()

            elif current and line.startswith("BSSID ") and ":" in line:
                current_bssid = {
                    "bssid": line.split(":", 1)[1].strip()
                }
                current["bssids"].append(current_bssid)

            elif current_bssid and ":" in line:
                key, value = line.split(":", 1)
                key = key.strip().lower()

                if key in ("signal", "radio type", "channel"):
                    current_bssid[key.replace(" ", "_")] = value.strip()

        if current:
            networks.append(current)

        return {
            "platform": SYSTEM,
            "available": True,
            "count": len(networks),
            "networks": networks,
        }

    if SYSTEM == "linux":
        if not shutil.which("iw"):
            return {
                "platform": SYSTEM,
                "available": False,
                "networks": [],
                "error": "iw command not found",
            }

        devices = run_command(["iw", "dev"])

        if not devices["success"]:
            return {
                "platform": SYSTEM,
                "available": False,
                "networks": [],
                "error": devices["error"],
            }

        names = []
        for line in devices["output"].splitlines():
            line = line.strip()
            if line.startswith("Interface "):
                names.append(line.split(maxsplit=1)[1])

        if not names:
            return {
                "platform": SYSTEM,
                "available": False,
                "networks": [],
                "error": "No wireless interfaces found",
            }

        scans = []
        for name in names:
            result = run_command(
                ["iw", "dev", name, "scan"],
                timeout=25,
            )
            scans.append({
                "interface": name,
                "success": result["success"],
                "details": result["output"],
                "error": result["error"],
            })

        return {
            "platform": SYSTEM,
            "available": any(item["success"] for item in scans),
            "networks": scans,
            "note": "Linux Wi-Fi scanning may require root privileges.",
        }

    return {
        "platform": SYSTEM,
        "available": False,
        "networks": [],
        "error": "Wi-Fi scanning is not supported on this platform",
    }


def get_services():
    tools = {
        "linux": [
            "ip", "iw", "nmcli", "nft",
            "iptables", "dnsmasq", "mmcli",
            "ubus", "uci", "hostapd",
        ],
        "windows": [
            "netsh", "route", "ipconfig",
            "powershell", "sc",
        ],
        "darwin": [
            "networksetup", "ifconfig",
            "netstat", "route",
        ],
    }

    commands = tools.get(SYSTEM, [])

    return {
        "platform": SYSTEM,
        "available_tools": {
            command: shutil.which(command) is not None
            for command in commands
        },
    }
