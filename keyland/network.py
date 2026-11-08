
import json
import platform
import shutil
import socket
import subprocess
import threading
import time
#network.py
import psutil
from collections import deque
from datetime import datetime, timezone
import re
import ipaddress
import urllib.request
from pathlib import Path

SYSTEM = platform.system().lower()

# Previous interface counters used to calculate real traffic rates.
_traffic_lock = threading.Lock()
_previous_counters = {}
_previous_time = None
_traffic_history = deque(maxlen=60)

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

def get_interfaces(active_only=False):
    result = []
    addresses = psutil.net_if_addrs()
    statistics = psutil.net_if_stats()

    for name, addr_list in addresses.items():
        stat = statistics.get(name)
        is_up = stat.isup if stat else False

        if active_only and not is_up:
            continue

        interface = {
            "name": name,
            "is_up": is_up,
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
    """Return interface traffic rates and store the last 60 samples."""
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
        total_rx = 0.0
        total_tx = 0.0

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

            rx_rate = round(rx_rate, 4)
            tx_rate = round(tx_rate, 4)

            total_rx += rx_rate
            total_tx += tx_rate

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
                "rx_mbps": rx_rate,
                "tx_mbps": tx_rate,
            })

        _previous_counters = dict(counters)
        _previous_time = now

        sample = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "rx_mbps": round(total_rx, 4),
            "tx_mbps": round(total_tx, 4),
        }

        _traffic_history.append(sample)

    return {
        "platform": SYSTEM,
        "sample_interval_seconds": round(elapsed, 3),
        "unit": "Mbps",
        "total_rx_mbps": round(total_rx, 4),
        "total_tx_mbps": round(total_tx, 4),
        "interfaces": interfaces,
    }


def get_traffic_history():
    """Return the last 60 traffic samples."""

    with _traffic_lock:
        samples = list(_traffic_history)

    return {
        "platform": SYSTEM,
        "unit": "Mbps",
        "count": len(samples),
        "samples": samples,
    }


def _public_ip(url: str, version: int) -> str | None:
    """Read the public IP from a small HTTPS response; never change routing."""
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Keyland-Network/1.0"},
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            raw = response.read(128).decode("ascii", errors="ignore").strip()
        # ipify JSON endpoint returns {"ip":"..."}; plain endpoints are also accepted.
        try:
            value = json.loads(raw).get("ip")
        except (ValueError, AttributeError):
            value = raw
        address = ipaddress.ip_address(str(value).strip())
        return str(address) if address.version == version else None
    except (OSError, ValueError, TypeError, TimeoutError):
        return None


def get_internet_status():
    """Check connectivity and discover the public IP through the active WAN."""
    targets = [("1.1.1.1", 443), ("8.8.8.8", 53)]
    connected = False
    target = None
    for host, port in targets:
        try:
            with socket.create_connection((host, port), timeout=2):
                connected = True
                target = f"{host}:{port}"
                break
        except OSError:
            continue

    routes = get_routes().get("routes", [])
    default_route = next(
        (route for route in routes
         if route.get("dst", "default") in ("default", "0.0.0.0/0")
         and route.get("dev")),
        {},
    )
    wan_interface = default_route.get("dev")
    if not wan_interface:
        gateway = get_default_gateway()
        wan_interface = None

    if wan_interface and wan_interface.startswith(("rmnet", "qmap", "wwan", "ppp", "cellular")):
        wan_type = "cellular"
    elif wan_interface and wan_interface.startswith(("wl", "wifi")):
        wan_type = "wifi"
    elif wan_interface:
        wan_type = "ethernet_or_other"
    else:
        wan_type = None

    local_address = None
    if wan_interface:
        for interface in get_interfaces():
            if interface.get("name") == wan_interface:
                local_address = next(
                    (item.get("address") for item in interface.get("addresses", [])
                     if item.get("family") == "IPv4"),
                    None,
                )
                break

    public_ipv4 = _public_ip("https://api.ipify.org?format=json", 4) if connected else None
    public_ipv6 = _public_ip("https://api6.ipify.org?format=json", 6) if connected else None
    return {
        "platform": SYSTEM,
        "connected": connected,
        "target": target,
        "wan_interface": wan_interface,
        "wan_type": wan_type,
        "gateway": default_route.get("gateway"),
        "local_ipv4": local_address,
        "public_ipv4": public_ipv4,
        "public_ipv6": public_ipv6,
        "public_ip_provider": "api.ipify.org",
        "public_ip_note": None if public_ipv4 or public_ipv6 else (
            "Public IP lookup unavailable; connectivity may be restricted or the lookup service may be unreachable."
        ),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

def get_default_gateway():
    """Detect the default IPv4 gateway without changing routes."""

    result = get_routes()

    if not result.get("available"):
        return {
            "available": False,
            "gateway": None,
            "error": result.get("error", "Routing information unavailable"),
        }

    for route in result.get("routes", []):
        if SYSTEM == "windows":
            destination = route.get("DestinationPrefix")
            gateway = route.get("NextHop")
        else:
            destination = route.get("dst", "default")
            gateway = route.get("gateway")

        if destination in ("0.0.0.0/0", "default") and gateway:
            if gateway not in ("0.0.0.0", "::"):
                return {
                    "available": True,
                    "gateway": gateway,
                    "error": None,
                }

    return {
        "available": False,
        "gateway": None,
        "error": "Default gateway not found",
    }


def ping_host(host, count=4):
    """Measure latency and packet loss using the system ping command."""

    if not shutil.which("ping"):
        return {
            "available": False,
            "success": False,
            "latency_ms": None,
            "packet_loss_percent": None,
            "error": "ping command not found",
        }

    if SYSTEM == "windows":
        command = [
            "ping", "-n", str(count), "-w", "1500", host
        ]
    else:
        command = [
            "ping", "-c", str(count), "-W", "2", host
        ]

    result = run_command(command, timeout=count * 3 + 3)
    output = result["output"]

    loss = None
    latency = None

    # Windows: (0% loss)
    # Linux: 0% packet loss
    loss_match = re.search(
        r"\(([\d.]+)%\s*loss\)|([\d.]+)%\s*packet loss",
        output,
        re.IGNORECASE,
    )

    if loss_match:
        loss = float(
            loss_match.group(1) or loss_match.group(2)
        )

    # Windows: Average = 12ms
    windows_latency = re.search(
        r"Average\s*=\s*([\d.]+)\s*ms",
        output,
        re.IGNORECASE,
    )

    # Linux: min/avg/max/mdev = 1.0/2.0/3.0/0.5 ms
    linux_latency = re.search(
        r"=\s*[\d.]+/([\d.]+)/[\d.]+",
        output,
    )

    if windows_latency:
        latency = float(windows_latency.group(1))
    elif linux_latency:
        latency = float(linux_latency.group(1))

    return {
        "available": True,
        "success": result["success"],
        "latency_ms": latency,
        "packet_loss_percent": loss,
        "error": result["error"] or None,
    }


def test_dns():
    """Check DNS resolution using the operating system resolver."""

    hostname = "example.com"
    started = time.monotonic()

    try:
        addresses = socket.getaddrinfo(
            hostname,
            443,
            type=socket.SOCK_STREAM,
        )

        elapsed = (time.monotonic() - started) * 1000

        resolved = sorted({
            item[4][0] for item in addresses
        })

        return {
            "available": True,
            "success": True,
            "hostname": hostname,
            "latency_ms": round(elapsed, 2),
            "addresses": resolved,
            "error": None,
        }

    except socket.gaierror as exc:
        elapsed = (time.monotonic() - started) * 1000

        return {
            "available": True,
            "success": False,
            "hostname": hostname,
            "latency_ms": round(elapsed, 2),
            "addresses": [],
            "error": str(exc),
        }


def get_network_diagnostics():
    """Run read-only network diagnostics."""

    gateway = get_default_gateway()

    gateway_ping = {
        "available": False,
        "success": False,
        "latency_ms": None,
        "packet_loss_percent": None,
        "error": "Gateway unavailable",
    }

    if gateway["available"]:
        gateway_ping = ping_host(
            gateway["gateway"],
            count=4,
        )

    dns = test_dns()

    return {
        "platform": SYSTEM,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "gateway": gateway,
        "gateway_ping": gateway_ping,
        "dns": dns,
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
        connections = []
        if result["success"]:
            interface_names = [
                line.strip().split(maxsplit=1)[1]
                for line in result["output"].splitlines()
                if line.strip().startswith("Interface ")
            ]
            for interface_name in interface_names:
                link = run_command(["iw", "dev", interface_name, "link"])
                item = {"interface": interface_name, "connected": False}
                link_output = link.get("output", "")
                if link["success"] and not link_output.lower().startswith("not connected"):
                    item["connected"] = True
                    for line in link_output.splitlines():
                        value = line.strip()
                        if value.startswith("SSID:"):
                            item["ssid"] = value.split(":", 1)[1].strip()
                        elif value.startswith("freq:"):
                            try:
                                item["frequency_mhz"] = int(value.split(":", 1)[1].strip())
                            except ValueError:
                                pass
                        elif value.startswith("signal:"):
                            match = re.search(r"-?\d+", value)
                            if match:
                                item["signal_dbm"] = int(match.group())
                        elif value.startswith("tx bitrate:"):
                            item["tx_bitrate"] = value.split(":", 1)[1].strip()
                # AP interfaces are not associated as Wi-Fi clients; their
                # active channel is reported by "iw dev <iface> info".
                info = run_command(["iw", "dev", interface_name, "info"])
                info_output = info.get("output", "")
                for line in info_output.splitlines():
                    value = line.strip()
                    if value.startswith("type "):
                        item["type"] = value.split(maxsplit=1)[1]
                    match = re.search(r"channel\s+(\d+)\s+\((\d+)\s+MHz\)", value)
                    if match and not item.get("frequency_mhz"):
                        item["channel"] = int(match.group(1))
                        item["frequency_mhz"] = int(match.group(2))
                frequency = item.get("frequency_mhz")
                if frequency:
                    item["channel"] = item.get("channel") or _wifi_channel(frequency)
                    item["connected"] = item["connected"] or item.get("type") == "AP"
                    item["band"] = (
                        "6 GHz" if frequency and frequency >= 5955
                        else "5 GHz" if frequency and frequency >= 4900
                        else "2.4 GHz" if frequency else None
                    )
                connections.append(item)

        return {
            "platform": SYSTEM,
            "available": result["success"],
            "details": result["output"],
            "connections": connections,
            "error": result["error"],
        }

    return {
        "platform": SYSTEM,
        "available": False,
        "interfaces": [],
    }


def _wifi_channel(frequency: int | None) -> int | None:
    if frequency is None:
        return None
    if frequency == 2484:
        return 14
    if 2412 <= frequency <= 2472:
        return (frequency - 2407) // 5
    if 5000 <= frequency <= 5895:
        return (frequency - 5000) // 5
    if 5955 <= frequency <= 7115:
        return (frequency - 5950) // 5
    return None


def _parse_iw_scan(output: str, interface: str) -> list[dict]:
    networks = []
    current = None

    def finish():
        if current and current.get("ssid"):
            current["interface"] = interface
            current["channel"] = _wifi_channel(current.get("frequency_mhz"))
            current["signal_source"] = "iw measured RSSI" if current.get("signal_dbm") is not None else None
            current["distance_m_estimate"] = _wifi_distance_estimate(current.get("signal_dbm"))
            current["security"] = (
                "WPA3" if current.pop("_wpa3", False)
                else "WPA2/WPA" if current.pop("_secured", False)
                else "Open"
            )
            networks.append(current)

    for raw in output.splitlines():
        line = raw.strip()
        if line.startswith("BSS "):
            finish()
            current = {
                "ssid": "",
                "bssid": line.split()[1].split("(")[0],
                "frequency_mhz": None,
                "signal_dbm": None,
                "_secured": False,
                "_wpa3": False,
            }
        elif current is not None:
            if line.startswith("freq:"):
                try:
                    current["frequency_mhz"] = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
            elif line.startswith("signal:"):
                match = re.search(r"-?\d+(?:\.\d+)?", line.split(":", 1)[1])
                if match:
                    current["signal_dbm"] = float(match.group())
            elif line.startswith("SSID:"):
                current["ssid"] = line.split(":", 1)[1].strip()
            elif line.startswith(("RSN:", "WPA:")):
                current["_secured"] = True
                if "SAE" in line:
                    current["_wpa3"] = True
    finish()
    return networks


def _split_nmcli_row(line: str) -> list[str]:
    fields, value, escaped = [], [], False
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


# KEYLAND_WIFI_DISTANCE_ESTIMATE
def _wifi_distance_estimate(signal_dbm: float | int | None) -> float | None:
    """Rough indoor range estimate; assumes -40 dBm at 1 m and path-loss n=2.7."""
    if signal_dbm is None:
        return None
    try:
        value = float(signal_dbm)
    except (TypeError, ValueError):
        return None
    if not -110 <= value <= -1:
        return None
    distance = 10 ** ((-40.0 - value) / 27.0)
    return round(max(0.5, min(distance, 100.0)), 1)


def get_wifi_scan():
    """Scan Wi-Fi networks using NetworkManager first, then the nl80211 iw fallback."""
    if SYSTEM == "windows":
        result = run_command(["netsh", "wlan", "show", "networks", "mode=bssid"], timeout=20)
        if not result["success"]:
            return {"platform": SYSTEM, "available": False, "networks": [], "error": result["error"]}
        networks, current, current_bssid = [], None, None
        for raw_line in result["output"].splitlines():
            line = raw_line.strip()
            if line.startswith("SSID ") and ":" in line:
                if current:
                    networks.append(current)
                current = {"ssid": line.split(":", 1)[1].strip(), "bssids": []}
                current_bssid = None
            elif current and line.startswith("BSSID ") and ":" in line:
                current_bssid = {"bssid": line.split(":", 1)[1].strip()}
                current["bssids"].append(current_bssid)
            elif current_bssid and ":" in line:
                key, value = line.split(":", 1)
                key = key.strip().lower()
                if key in ("signal", "channel", "radio type"):
                    current_bssid[key.replace(" ", "_")] = value.strip()
        if current:
            networks.append(current)
        return {"platform": SYSTEM, "available": True, "count": len(networks), "networks": networks}

    if SYSTEM != "linux":
        return {"platform": SYSTEM, "available": False, "networks": [], "error": "Wi-Fi scanning is not supported on this platform"}

    devices = run_command(["iw", "dev"])
    if not devices["success"]:
        return {"platform": SYSTEM, "available": False, "networks": [], "error": devices.get("error") or "Could not list Wi-Fi interfaces"}

    names = [
        line.strip().split(maxsplit=1)[1]
        for line in devices["output"].splitlines()
        if line.strip().startswith("Interface ")
    ]
    if not names:
        return {"platform": SYSTEM, "available": False, "networks": [], "error": "No wireless interfaces found"}

    networks, scans = [], []
    nmcli_available = shutil.which("nmcli") is not None
    for name in names:
        found, scan_error, scan_method = [], None, None
        if nmcli_available:
            listed = run_command([
                "nmcli", "--wait", "20", "--terse", "--escape", "yes", "--fields",
                "SSID,BSSID,CHAN,FREQ,RATE,SIGNAL,SECURITY",
                "device", "wifi", "list", "--rescan", "yes", "ifname", name,
            ], timeout=25)
            if listed["success"]:
                for row in listed["output"].splitlines():
                    parts = _split_nmcli_row(row)
                    if len(parts) < 7 or not parts[1]:
                        continue
                    try:
                        frequency = int(parts[3]) if parts[3] else None
                        channel = int(parts[2]) if parts[2] else _wifi_channel(frequency)
                        signal = int(parts[5]) if parts[5] else None
                    except ValueError:
                        frequency, channel, signal = None, None, None
                    # NetworkManager exposes signal quality as percent, not RSSI.
                    # Convert only to a clearly marked estimate; iw scan below can
                    # provide the actual dBm value when the driver permits it.
                    estimated_dbm = round(-100 + (signal * 0.6), 1) if signal is not None else None
                    found.append({
                        "interface": name, "ssid": parts[0], "bssid": parts[1],
                        "channel": channel, "frequency_mhz": frequency,
                        "rate": parts[4], "signal_percent": signal,
                        "signal_dbm_estimate": estimated_dbm,
                        "signal_source": "NetworkManager quality estimate" if signal is not None else None,
                        "distance_m_estimate": _wifi_distance_estimate(estimated_dbm),
                        "security": parts[6] or "Open",
                    })
                if found:
                    scan_method = "NetworkManager"
            if not found:
                scan_error = listed.get("error")

        if not found:
            raw = run_command(["iw", "dev", name, "scan"], timeout=30)
            if raw["success"]:
                found = _parse_iw_scan(raw["output"], name)
                scan_method = "iw"
            else:
                scan_error = raw.get("error") or scan_error or "Scan failed"

        networks.extend(found)
        scans.append({
            "interface": name, "success": bool(found),
            "count": len(found), "method": scan_method, "error": scan_error,
        })

    # De-duplicate BSS entries returned by multiple interfaces.
    unique = {}
    for item in networks:
        key = (item.get("bssid"), item.get("ssid"))
        unique[key] = item

    result_networks = sorted(
        unique.values(),
        key=lambda item: (
            -(item.get("signal_percent") if item.get("signal_percent") is not None
              else item.get("signal_dbm") if item.get("signal_dbm") is not None else -999)
        ),
    )
    return {
        "platform": SYSTEM, "available": bool(result_networks),
        "count": len(result_networks), "networks": result_networks,
        "interfaces": scans,
        "error": None if result_networks else "No networks returned. Check interface state, rfkill, permissions and driver scan support.",
        "note": "Scanning uses NetworkManager when possible and falls back to iw. It does not change the active connection.",
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


def get_power_status():
    """Read battery and charger telemetry from Linux power_supply sysfs."""
    if SYSTEM != "linux":
        return {
            "platform": SYSTEM,
            "available": False,
            "battery": None,
            "chargers": [],
            "error": "Power-supply monitoring is supported on Linux only",
        }

    root = Path("/sys/class/power_supply")
    if not root.is_dir():
        return {
            "platform": SYSTEM,
            "available": False,
            "battery": None,
            "chargers": [],
            "error": "Power-supply sysfs is unavailable",
        }

    def read_text(directory, name):
        try:
            value = (directory / name).read_text(encoding="utf-8").strip()
            return value if value else None
        except (OSError, UnicodeError):
            return None

    def read_number(directory, name):
        value = read_text(directory, name)
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    battery = None
    chargers = []

    try:
        devices = sorted(item for item in root.iterdir() if item.is_dir())
    except OSError:
        devices = []

    for device in devices:
        supply_type = read_text(device, "type")
        if not supply_type:
            continue

        name = device.name
        status = read_text(device, "status")
        voltage_uv = read_number(device, "voltage_now")
        current_ua = read_number(device, "current_now")
        voltage_v = round(voltage_uv / 1_000_000, 3) if voltage_uv is not None else None
        current_ma = round(current_ua / 1_000, 1) if current_ua is not None else None

        if supply_type.lower() == "battery":
            capacity = read_number(device, "capacity")
            temp_raw = read_number(device, "temp")
            charge_now = read_number(device, "charge_now")
            charge_full = read_number(device, "charge_full")
            charge_design = read_number(device, "charge_full_design")
            energy_now = read_number(device, "energy_now")
            energy_full = read_number(device, "energy_full")
            energy_design = read_number(device, "energy_full_design")
            min_design_uv = read_number(device, "voltage_min_design")
            max_design_uv = read_number(device, "voltage_max_design")

            battery = {
                "name": name,
                "present": read_number(device, "present") != 0,
                "status": status,
                "capacity_percent": capacity,
                "capacity_level": read_text(device, "capacity_level"),
                "health": read_text(device, "health"),
                "voltage_v": voltage_v,
                "current_ma": current_ma,
                "temperature_c": round(temp_raw / 10, 1) if temp_raw is not None else None,
                "charge_now_mah": round(charge_now / 1000, 1) if charge_now is not None else None,
                "charge_full_mah": round(charge_full / 1000, 1) if charge_full is not None else None,
                "charge_full_design_mah": round(charge_design / 1000, 1) if charge_design is not None else None,
                "energy_now_wh": round(energy_now / 1_000_000, 3) if energy_now is not None else None,
                "energy_full_wh": round(energy_full / 1_000_000, 3) if energy_full is not None else None,
                "energy_full_design_wh": round(energy_design / 1_000_000, 3) if energy_design is not None else None,
                "voltage_min_design_v": round(min_design_uv / 1_000_000, 3) if min_design_uv is not None else None,
                "voltage_max_design_v": round(max_design_uv / 1_000_000, 3) if max_design_uv is not None else None,
                "technology": read_text(device, "technology"),
                "manufacturer": read_text(device, "manufacturer"),
                "model_name": read_text(device, "model_name"),
                "cycle_count": read_number(device, "cycle_count"),
            }
        else:
            online_value = read_number(device, "online")
            charger = {
                "name": name,
                "type": supply_type,
                "online": bool(online_value) if online_value is not None else None,
                "status": status,
                "voltage_v": voltage_v,
                "current_ma": current_ma,
                "power_mw": round((voltage_uv * current_ua) / 1_000_000_000, 1)
                    if voltage_uv is not None and current_ua is not None else None,
                "health": read_text(device, "health"),
                "usb_type": read_text(device, "usb_type"),
                "manufacturer": read_text(device, "manufacturer"),
                "model_name": read_text(device, "model_name"),
            }
            chargers.append(charger)

    return {
        "platform": SYSTEM,
        "available": battery is not None,
        "battery": battery,
        "chargers": chargers,
        "source": str(root),
        "error": None if battery is not None else "No battery device was found in power_supply",
    }
