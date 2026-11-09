"""Hostapd + dnsmasq + nftables controller for a dedicated Keyland AP."""
from __future__ import annotations
import ipaddress, json, os, re, shutil, signal, socket, subprocess, sys, time
from pathlib import Path
from typing import Any
from keyland.adapters.wifi.capabilities import parse_interface_combinations
from keyland.adapters.wifi.dns_monitor import parse_dhcp_leases, parse_dns_query_log

ROOT = Path("/run/keyland-router")
STATE = ROOT / "state.json"
AUTOSTART_FILE = Path("/etc/keyland/router-autostart.json")

def _read_autostart() -> dict[str, Any] | None:
    try:
        data = json.loads(AUTOSTART_FILE.read_text())
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None

def _write_autostart(config: dict[str, Any]) -> None:
    AUTOSTART_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp_config = AUTOSTART_FILE.with_suffix(".tmp")
    temp_config.write_text(json.dumps(config))
    os.chmod(temp_config, 0o600)
    temp_config.replace(AUTOSTART_FILE)


def autostart_status() -> dict[str, Any]:
    config = _read_autostart()
    return {
        "enabled": bool(config),
        "active": bool(status().get("active")),
        "ssid": config.get("ssid") if config else None,
        "interface": config.get("interface") if config else None,
        "upstream_interface": config.get("upstream_interface") if config else None,
        "message": "Router mode will be restored automatically when the device and selected WAN are ready." if config else "Automatic router startup is disabled.",
    }

def disable_autostart() -> dict[str, Any]:
    try:
        AUTOSTART_FILE.unlink(missing_ok=True)
        return {"success": True, "enabled": False}
    except OSError as exc:
        return {"success": False, "error": f"Could not disable automatic router startup: {exc}"}

def enable_autostart() -> dict[str, Any]:
    state = read_state()
    if not state or not status().get("active"):
        return {"success": False, "error": "Start Router Wi-Fi before enabling automatic startup."}
    try:
        hostapd_config = (ROOT / "hostapd.conf").read_text()
        password_line = next(
            (line for line in hostapd_config.splitlines() if line.startswith("wpa_passphrase=")),
            None,
        )
        if not password_line:
            return {"success": False, "error": "Could not read the active WPA2 configuration."}
        config = {
            key: state[key]
            for key in ("mode", "interface", "upstream_interface", "ssid", "ipv4_cidr",
                        "dns_servers", "band", "channel")
            if key in state
        }
        config["password"] = password_line.split("=", 1)[1]
        config["source_interface"] = state.get("source_interface", "wlan0")
        _write_autostart(config)
        return {"success": True, "enabled": True}
    except (OSError, KeyError, StopIteration) as exc:
        return {"success": False, "error": f"Could not enable automatic startup: {exc}"}

def restore_autostart() -> dict[str, Any]:
    """Restore saved router mode after boot without changing the phone WAN."""
    config = _read_autostart()
    if not config:
        return {"success": True, "skipped": True, "reason": "No saved router configuration."}
    if status().get("active"):
        return {"success": True, "active": True, "already_running": True}
    if read_state():
        return {"success": False, "waiting": False, "error": "Stale router state detected. Automatic recovery is disabled until existing routes and firewall ownership are verified; no network changes made."}
    source = config.get("source_interface", "wlan0")
    ap = config.get("interface", "keyland-ap")
    wan = config.get("upstream_interface")
    if not isinstance(wan, str) or wan not in {x.get("dev") for x in defaults()}:
        return {"success": False, "waiting": True, "error": "Waiting for the saved upstream interface to become available."}
    prepared = prepare_ap({"interface": source, "ap_interface": ap, "confirmed": True})
    if not prepared.get("success"):
        live = next((x for x in interfaces() if x.get("name") == ap and x.get("type") == "AP"), None)
        if not live:
            return {"success": False, "waiting": True, "error": prepared.get("error", "AP interface is not ready.")}
    payload = {k: v for k, v in config.items() if k != "source_interface"}

    # In repeater mode, AP and upstream station share one PHY.
    # Always follow the channel currently used by the connected Wi-Fi.
    if config.get("mode") == "repeater":
        station = next(
            (x for x in interfaces()
             if x.get("name") == source
             and x.get("type") == "managed"
             and x.get("ssid")),
            None,
        )
        channel = (station or {}).get("channel")
        if not isinstance(channel, int):
            return {
                "success": False,
                "waiting": True,
                "error": "Waiting for the upstream Wi-Fi channel to become available.",
            }

        payload["channel"] = channel
        payload["band"] = "2.4" if channel <= 14 else "5"

    payload["confirmed"] = True
    result = start(payload)
    if result.get("success"):
        result["restored"] = True
    return result
TABLE = "keyland_router"
IFACE = re.compile(r"^[A-Za-z0-9_.:-]{1,15}$")


def run(args: list[str], timeout: int = 12, stdin: str | None = None) -> dict[str, Any]:
    try:
        p = subprocess.run(args, input=stdin, capture_output=True, text=True,
                           timeout=timeout, check=False, errors="replace")
        return {"ok": p.returncode == 0, "out": p.stdout.strip(),
                "err": p.stderr.strip() or None}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "out": "", "err": str(exc)}


def interfaces() -> list[dict[str, Any]]:
    r = run(["iw", "dev"])
    if not r["ok"]:
        return []
    out, phy, cur = [], None, None
    for raw in r["out"].splitlines():
        line = raw.strip()
        if line.startswith("phy#"):
            if cur:
                out.append(cur)
                cur = None
            phy = line
        elif line.startswith("Unnamed/non-netdev interface"):
            if cur:
                out.append(cur)
                cur = None
        elif line.startswith("Interface "):
            if cur: out.append(cur)
            cur = {"name": line.split(maxsplit=1)[1], "phy": phy,
                   "type": None, "ssid": None, "channel": None}
        elif cur:
            if line.startswith("type "): cur["type"] = line.split(maxsplit=1)[1]
            elif line.startswith("ssid "): cur["ssid"] = line[5:].strip()
            else:
                m = re.search(r"\bchannel\s+(\d+)\s+\((\d+)\s+MHz\)", line)
                if m: cur["channel"] = int(m.group(1))
    if cur:
        out.append(cur)

    # "iw dev" may omit the SSID for managed interfaces.
    # Read the live association so repeater validation sees the real upstream.
    for item in out:
        if item.get("type") not in ("managed", "P2P-client"):
            continue

        link = run(["iw", "dev", item["name"], "link"])
        if not link["ok"]:
            continue

        link_output = link["out"]
        if link_output.lower().startswith("not connected"):
            item["ssid"] = None
            item["channel"] = None
            continue

        for raw_line in link_output.splitlines():
            line = raw_line.strip()

            if line.startswith("SSID:"):
                item["ssid"] = line.split(":", 1)[1].strip() or None

            elif line.startswith("freq:"):
                match = re.search(r"(\d{4,5})", line)
                if match:
                    frequency = int(match.group(1))
                    if frequency == 2484:
                        item["channel"] = 14
                    elif 2412 <= frequency <= 2472:
                        item["channel"] = (frequency - 2407) // 5
                    elif 5000 <= frequency <= 5895:
                        item["channel"] = (frequency - 5000) // 5
                    elif 5955 <= frequency <= 7115:
                        item["channel"] = (frequency - 5950) // 5

    return out


def defaults() -> list[dict[str, Any]]:
    r = run(["ip", "-j", "route", "show", "default"])
    try: return json.loads(r["out"]) if r["ok"] else []
    except (ValueError, TypeError): return []


def read_state() -> dict[str, Any] | None:
    try: return json.loads(STATE.read_text())
    except (OSError, ValueError): return None


def write_state(data: dict[str, Any]) -> None:
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.chmod(tmp, 0o600)
    tmp.replace(STATE)


def _clients(ap: str | None) -> list[dict[str, Any]]:
    """Collect per-station counters and radio statistics from the AP driver."""
    if not ap:
        return []
    r = run(["iw", "dev", ap, "station", "dump"])
    if not r["ok"]:
        return []
    clients: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    numeric_fields = {
        "rx_bytes", "tx_bytes", "rx_packets", "tx_packets",
        "tx_retries", "tx_failed", "connected_time", "inactive_time",
        "expected_throughput",
    }
    for raw in r["out"].splitlines():
        line = raw.strip()
        if line.startswith("Station "):
            if current:
                clients.append(current)
            current = {"mac": line.split()[1].lower()}
            continue
        if not current or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower().replace(" ", "_").replace("/", "_")
        value = value.strip()
        if key in numeric_fields:
            match = re.search(r"-?\d+(?:\.\d+)?", value)
            if match:
                number = float(match.group())
                current[key] = int(number) if number.is_integer() else number
        elif key in ("signal", "signal_avg"):
            match = re.search(r"-?\d+(?:\.\d+)?", value)
            if match:
                current[key] = float(match.group())
        elif key in ("tx_bitrate", "rx_bitrate", "authorized",
                     "authenticated", "associated", "preamble",
                     "wmm_wme", "mfp", "tdls_peer"):
            current[key] = value
    if current:
        clients.append(current)
    return clients


def status() -> dict[str, Any]:
    state = read_state()
    alive = {}
    if state:
        for key in ("hostapd_pid", "dnsmasq_pid"):
            try: os.kill(int(state[key]), 0); alive[key] = True
            except (OSError, KeyError, ValueError, TypeError): alive[key] = False
    return {"configured": bool(state), "active": bool(state and all(alive.values())),
            "configuration": {k:v for k,v in (state or {}).items()
                              if k not in ("password", "hostapd_pid", "dnsmasq_pid")},
            "processes": alive, "interfaces": interfaces(), "default_routes": defaults(), "clients": _clients(state.get("interface") if state else None)}



def prepare_ap(p: dict[str, Any]) -> dict[str, Any]:
    """Prepare a dedicated AP interface without modifying the source Wi-Fi."""
    if p.get("confirmed") is not True:
        return {"success": False, "error": "Explicit administrator confirmation is required."}

    source_name = p.get("interface", "wlan0")
    ap_name = p.get("ap_interface", "keyland-ap")

    if (
        not isinstance(source_name, str)
        or not IFACE.fullmatch(source_name)
        or source_name == "lo"
        or not isinstance(ap_name, str)
        or not IFACE.fullmatch(ap_name)
        or ap_name == "lo"
        or source_name == ap_name
    ):
        return {"success": False, "error": "Invalid source or AP interface name."}

    if read_state():
        return {"success": False, "error": "Stop the active router before preparing its AP interface."}

    devs = interfaces()
    source = next((x for x in devs if x["name"] == source_name), None)
    ap = next((x for x in devs if x["name"] == ap_name), None)

    if not source or source.get("type") != "managed":
        return {"success": False, "error": "Source must be an existing managed Wi-Fi interface."}

    if ap and ap_name in {x.get("dev") for x in defaults()}:
        return {"success": False, "error": "Refusing to modify an AP interface used by a default route."}

    if ap and ap.get("ssid"):
        return {"success": False, "error": "The dedicated AP interface is connected to a Wi-Fi network."}

    if ap and ap.get("phy") != source.get("phy"):
        return {"success": False, "error": "The existing AP interface belongs to a different PHY."}

    iw_info = run(["iw", "list"], timeout=20)
    if not iw_info["ok"]:
        return {"success": False, "error": "Could not inspect Wi-Fi driver capabilities."}

    if not re.search(r"^\s*\*\s+AP\s*$", iw_info["out"], re.MULTILINE):
        return {"success": False, "error": "The Wi-Fi driver does not report AP mode support."}

    source_active = bool(
        source.get("ssid")
        or source_name in {x.get("dev") for x in defaults()}
    )

    if source_active:
        combinations = parse_interface_combinations(iw_info["out"])
        if not combinations.get("ap_client_combination_mentioned"):
            return {
                "success": False,
                "error": "Simultaneous AP + Client support is not confirmed. No changes made.",
            }

    created = False

    if not ap:
        result = run([
            "iw", "dev", source_name, "interface",
            "add", ap_name, "type", "__ap",
        ])
        if not result["ok"]:
            return {
                "success": False,
                "error": "Could not create dedicated AP interface: " + str(result["err"] or result["out"]),
            }
        created = True

    # Remember whether NetworkManager previously managed this AP interface.
    nm_state = run([
        "nmcli", "-g", "GENERAL.NM-MANAGED", "device", "show", ap_name
    ])
    was_managed = nm_state["ok"] and nm_state["out"].strip().lower() == "yes"

    def fail(message: str) -> dict[str, Any]:
        if was_managed:
            run(["nmcli", "device", "set", ap_name, "managed", "yes"])
        if created:
            run(["iw", "dev", ap_name, "del"])
        return {"success": False, "error": message}

    result = run(["nmcli", "device", "set", ap_name, "managed", "no"])
    if not result["ok"]:
        return fail("Could not detach dedicated AP from NetworkManager: " + str(result["err"] or result["out"]))

    current = next((x for x in interfaces() if x["name"] == ap_name), None)
    if not current:
        return fail("AP interface disappeared during preparation.")

    if current.get("type") != "AP":
        result = run(["ip", "link", "set", ap_name, "down"])
        if not result["ok"]:
            return fail("Could not bring dedicated AP interface down.")

        result = run(["iw", "dev", ap_name, "set", "type", "__ap"])
        if not result["ok"]:
            return fail("Could not switch dedicated interface to AP mode: " + str(result["err"] or result["out"]))

    verified = next((x for x in interfaces() if x["name"] == ap_name), None)
    if not verified or verified.get("type") != "AP":
        return fail("AP verification failed; expected interface type AP.")

    return {
        "success": True,
        "created": created,
        "interface": ap_name,
        "type": "AP",
        "phy": verified.get("phy"),
        "message": "Dedicated AP interface is ready.",
    }


def _validate(p: dict[str, Any]) -> dict[str, Any]:
    mode, ap, wan = p.get("mode", "hotspot"), p.get("interface", "keyland-ap"), p.get("upstream_interface")
    if mode not in ("hotspot", "repeater"): raise ValueError("Mode must be hotspot or repeater.")
    if not isinstance(ap, str) or not IFACE.fullmatch(ap) or ap == "lo": raise ValueError("Invalid AP interface.")
    if not isinstance(wan, str) or not IFACE.fullmatch(wan) or wan in (ap, "lo"): raise ValueError("Choose a valid upstream interface.")
    ssid, password = p.get("ssid"), p.get("password")
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode()) <= 32 or any(ord(ch) < 32 or ord(ch) == 127 for ch in ssid): raise ValueError("SSID must contain 1–32 UTF-8 bytes and no control characters.")
    if not isinstance(password, str) or not 8 <= len(password) <= 63 or not password.isascii() or not password.isprintable(): raise ValueError("WPA2 password must contain 8–63 printable ASCII characters.")
    try: addr = ipaddress.ip_interface(p.get("ipv4_cidr", "192.168.88.1/24"))
    except (ValueError, TypeError): raise ValueError("Invalid AP IPv4/CIDR.")
    if addr.version != 4 or not 8 <= addr.network.prefixlen <= 30 or addr.ip in (addr.network.network_address, addr.network.broadcast_address):
        raise ValueError("AP IPv4 must be a usable address with prefix /8 through /30.")
    dns = p.get("dns_servers") or ["1.1.1.1", "8.8.8.8"]
    if not isinstance(dns, list) or len(dns) > 4: raise ValueError("Provide up to four DNS servers.")
    try: dns = [str(ipaddress.ip_address(x)) for x in dns]
    except (ValueError, TypeError): raise ValueError("Invalid DNS server.")
    if any(ipaddress.ip_address(x).version != 4 for x in dns): raise ValueError("Only IPv4 DNS servers are supported.")
    addr_result = run(["ip", "-j", "-4", "addr", "show"])
    if addr_result["ok"]:
        try:
            for link in json.loads(addr_result["out"]):
                if link.get("ifname") in (ap, "lo"): continue
                for info in link.get("addr_info", []):
                    if info.get("family") == "inet":
                        existing = ipaddress.ip_interface(str(info["local"]) + "/" + str(info["prefixlen"]))
                        if addr.network.overlaps(existing.network):
                            raise ValueError("AP subnet " + str(addr.network) + " overlaps with " + str(link.get("ifname")) + " (" + str(existing) + ").")
        except (ValueError, KeyError, TypeError) as exc:
            if isinstance(exc, ValueError) and str(exc).startswith("AP subnet"): raise
    if not all(shutil.which(x) for x in ("hostapd", "dnsmasq", "nft", "ip", "iw", "nmcli", "sysctl")):
        raise ValueError("Install hostapd, dnsmasq, nftables, iproute2, iw and NetworkManager.")
    if wan not in {x.get("dev") for x in defaults()}: raise ValueError("Selected upstream has no active default route.")
    devs = interfaces()
    apdev = next((x for x in devs if x["name"] == ap), None)
    if not apdev or apdev["type"] != "AP": raise ValueError("A dedicated interface already in AP mode is required (normally keyland-ap).")
    if ap in {x.get("dev") for x in defaults()}: raise ValueError("Refusing to use the default-route interface as AP.")
    if mode == "repeater":
        upstream = next((x for x in devs if x["name"] == wan), None)
        if not upstream or upstream["type"] != "managed" or not upstream.get("ssid"):
            raise ValueError("Repeater mode requires a connected Wi-Fi client upstream.")
    if mode == "repeater" and wan.startswith(("rmnet", "qmap", "wwan", "ppp")):
        raise ValueError("Select Hotspot mode for cellular/SIM upstream.")
    # Only a connected station on the AP's own PHY constrains AP/client
    # concurrency. A station on another radio does not share this limit.
    station = next((
        x for x in devs
        if x["type"] == "managed" and x.get("ssid")
        and x.get("phy") == apdev.get("phy")
    ), None)
    combinations = {}
    if station:
        iw_list = run(["iw", "list"], timeout=20)
        combinations = parse_interface_combinations(iw_list["out"]) if iw_list["ok"] else {}
        if not combinations.get("ap_client_combination_mentioned"):
            raise ValueError("The Wi-Fi driver does not confirm simultaneous AP + Client operation on this PHY.")
    # Validate requested AP band/channel against the current regulatory database.
    band = p.get("band", "2.4")
    if band not in ("2.4", "5"):
        raise ValueError("Wi-Fi band must be 2.4 or 5 GHz.")

    station_channel = (station or {}).get("channel")

    # In repeater mode, follow the connected upstream channel automatically.
    # AP and managed client share one PHY and may be limited to one channel.
    if mode == "repeater" and station:
        if not isinstance(station_channel, int):
            raise ValueError("Could not determine the connected Wi-Fi channel.")
        band = "2.4" if station_channel <= 14 else "5"
        p["band"] = band
        p["channel"] = station_channel

    default_channel = 36 if band == "5" else (station_channel if station_channel in range(1, 12) else 1)
    try:
        requested_channel = int(p.get("channel") or default_channel)
    except (TypeError, ValueError):
        raise ValueError("Invalid Wi-Fi channel.")
    if (
        mode == "repeater"
        and station
        and station.get("phy") == apdev.get("phy")
        and station_channel != requested_channel
    ):
        raise ValueError("Repeater AP must use the connected Wi-Fi channel.")
    phy_info = run(["iw", "list"], timeout=20)
    if not phy_info["ok"]:
        raise ValueError("Could not read supported Wi-Fi channels.")
    available_by_band = {"2.4": set(), "5": set()}
    for raw in phy_info["out"].splitlines():
        match = re.search(
            r"\*\s+(\d+(?:\.\d+)?)\s+MHz\s+\[(\d+)\]\s+\((.*)\)",
            raw,
        )
        if not match:
            continue

        frequency = float(match.group(1))
        channel_number = int(match.group(2))
        flags = match.group(3).lower()

        if any(flag in flags for flag in ("disabled", "no ir", "radar detection")):
            continue

        if 2400 <= frequency < 2500:
            available_by_band["2.4"].add(channel_number)
        elif 4900 <= frequency < 5925:
            available_by_band["5"].add(channel_number)

    if requested_channel not in available_by_band[band]:
        raise ValueError(
            f"Choose a supported non-DFS channel. Channel {requested_channel} "
            f"is not currently permitted for AP operation in the {band} GHz band."
        )
    if station and station.get("phy") == apdev.get("phy") and station_channel != requested_channel:
        multi = any(re.search(r"#\s*channels\s*<=\s*2", item) for item in combinations.get("details", []))
        if not multi:
            raise ValueError("This Wi-Fi driver requires AP and Wi-Fi Client to use the same channel.")
    return {"mode": mode, "interface": ap, "upstream_interface": wan, "ssid": ssid,
            "password": password, "ipv4_cidr": str(addr), "gateway": str(addr.ip),
            "network": str(addr.network), "dns_servers": dns, "band": band,
            "channel": requested_channel}


def _stop_managed_processes(state: dict[str, Any]) -> list[str]:
    """Stop only verified Keyland daemons and wait for their exit."""
    expected = {
        "hostapd_pid": "hostapd",
        "dnsmasq_pid": "dnsmasq",
    }
    processes = []
    errors = []

    # Validate every PID before signalling any process.
    for key, name in expected.items():
        raw_pid = state.get(key)
        if raw_pid is None:
            continue
        try:
            pid = int(raw_pid)
            if pid <= 1:
                raise ValueError("invalid PID")

            proc = Path(f"/proc/{pid}")
            if not proc.exists():
                continue

            stat = (proc / "stat").read_text()
            status = stat.rsplit(")", 1)[1].strip().split()[0]
            if status in ("Z", "X"):
                continue

            argv = (proc / "cmdline").read_bytes().split(bytes([0]))
            executable = Path(argv[0].decode(errors="replace")).name if argv[0] else ""

            if executable != name:
                errors.append(f"{name}: PID identity mismatch; left untouched.")
                continue

            processes.append((pid, name, proc))

        except (OSError, KeyError, ValueError, TypeError, IndexError) as exc:
            errors.append(f"{name}: {exc}")

    if errors:
        return errors

    # Send SIGTERM only after all identities have been validated.
    for pid, name, proc in processes:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError as exc:
            errors.append(f"{name}: could not send SIGTERM: {exc}")

    if errors:
        return errors

    # Wait up to 8 seconds for each daemon.
    for pid, name, proc in processes:
        deadline = time.monotonic() + 8
        stopped = False

        while time.monotonic() < deadline:
            try:
                if not proc.exists():
                    stopped = True
                    break

                stat = (proc / "stat").read_text()
                status = stat.rsplit(")", 1)[1].strip().split()[0]
                if status in ("Z", "X"):
                    stopped = True
                    break

                argv = (proc / "cmdline").read_bytes().split(bytes([0]))
                executable = Path(argv[0].decode(errors="replace")).name if argv[0] else ""
                if executable != name:
                    stopped = True
                    break

            except (FileNotFoundError, ProcessLookupError):
                stopped = True
                break
            except (OSError, IndexError) as exc:
                errors.append(f"{name}: could not verify shutdown: {exc}")
                break

            time.sleep(0.2)

        if not stopped and not any(name in e for e in errors):
            errors.append(f"{name}: did not stop within 8 seconds.")

    return errors


def start(p: dict[str, Any]) -> dict[str, Any]:
    if p.get("confirmed") is not True: return {"success": False, "error": "Explicit administrator confirmation is required."}
    if read_state(): return {"success": False, "error": "Router Wi-Fi is already active. Stop it first."}
    try: cfg = _validate(p)
    except ValueError as exc: return {"success": False, "error": str(exc)}
    ap, wan = cfg["interface"], cfg["upstream_interface"]
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    state = {**cfg, "source_interface": p.get("source_interface", "wlan0"),
             "previous_ip_forward": Path("/proc/sys/net/ipv4/ip_forward").read_text().strip(),
             "previous_wan_rp_filter": None}
    policy_routes_created = False
    policy_rule_added = False
    firewall_created = False
    try:
        # Only the dedicated AP is detached from NetworkManager; upstream is untouched.
        r = run(["nmcli", "device", "set", ap, "managed", "no"])
        if not r["ok"]: raise RuntimeError("Could not detach AP from NetworkManager: " + str(r["err"] or r["out"]))
        r = run(["sysctl", "-w", "net.ipv4.ip_forward=1"])
        if not r["ok"]: raise RuntimeError("Could not enable IPv4 forwarding.")
        # Cellular links can receive replies on qmapmux while the phone's
        # main default route remains wlan0. Strict reverse-path filtering
        # would drop those valid asymmetric replies. Use loose mode only on
        # the selected cellular WAN and restore its exact previous value.
        if wan.startswith(("rmnet", "qmap", "wwan", "ppp")):
            rp_filter = Path("/proc/sys/net/ipv4/conf") / wan / "rp_filter"
            if rp_filter.exists():
                state["previous_wan_rp_filter"] = rp_filter.read_text().strip()
                rp_filter.write_text("2\n")
        run(["ip", "link", "set", ap, "down"])
        run(["ip", "addr", "flush", "dev", ap])
        if not run(["ip", "addr", "replace", cfg["ipv4_cidr"], "dev", ap])["ok"]: raise RuntimeError("Could not assign AP gateway IP.")
        if not run(["ip", "link", "set", ap, "up"])["ok"]: raise RuntimeError("Could not bring AP interface up.")
        hconf = ROOT / "hostapd.conf"
        hw_mode = "g" if cfg["band"] == "2.4" else "a"
        band_features = "ieee80211n=1\n" if cfg["band"] == "2.4" else "ieee80211n=1\nieee80211ac=1\nvht_oper_chwidth=0\n"
        hconf.write_text(f"interface={ap}\ndriver=nl80211\nssid={cfg['ssid']}\ncountry_code=GE\nhw_mode={hw_mode}\nchannel={cfg['channel']}\n{band_features}wmm_enabled=1\nauth_algs=1\nwpa=2\nwpa_key_mgmt=WPA-PSK\nrsn_pairwise=CCMP\nwpa_passphrase={cfg['password']}\n")
        os.chmod(hconf, 0o600)
        with (ROOT / "hostapd.log").open("a") as hostapd_log:
            hp = subprocess.Popen(["hostapd", str(hconf)], stdout=hostapd_log, stderr=subprocess.STDOUT, start_new_session=True)
        state["hostapd_pid"] = hp.pid
        time.sleep(2)
        if hp.poll() is not None: raise RuntimeError("hostapd exited during startup; inspect driver logs.")
        net = ipaddress.ip_network(cfg["network"])
        if net.num_addresses < 10: raise RuntimeError("AP subnet is too small for DHCP.")
        first = net.network_address + 1
        last = net.broadcast_address - 1
        gateway_ip = ipaddress.ip_address(cfg["gateway"])
        if first == gateway_ip: first += 1
        if last == gateway_ip: last -= 1
        dconf = ROOT / "dnsmasq.conf"
        dns_monitoring = p.get("dns_monitoring") is True
        dns_servers = [cfg["gateway"]] if dns_monitoring else cfg["dns_servers"]
        lease_file = ROOT / "dnsmasq.leases"
        query_log = ROOT / "dnsmasq-query.log"
        dconf_text = (
            f"interface={ap}\nbind-interfaces\nexcept-interface=lo\n"
            f"dhcp-range={first},{last},{net.netmask},12h\n"
            f"dhcp-option=3,{cfg['gateway']}\n"
            f"dhcp-option=6,{','.join(dns_servers)}\n"
            f"dhcp-leasefile={lease_file}\nno-resolv\n"
            + "".join(f"server={x}\n" for x in cfg["dns_servers"])
            + f"pid-file={ROOT / 'dnsmasq.pid'}\n"
        )
        if dns_monitoring:
            dconf_text += f"log-queries=extra\nlog-facility={query_log}\nlog-async=25\n"
        dconf.write_text(dconf_text)
        state["dns_monitoring"] = dns_monitoring
        os.chmod(dconf, 0o600)
        dp = subprocess.Popen(["dnsmasq", "--keep-in-foreground", "--conf-file=" + str(dconf)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        state["dnsmasq_pid"] = dp.pid
        time.sleep(1)
        if dp.poll() is not None: raise RuntimeError("dnsmasq exited during startup.")
        # Add an isolated policy-routing table so AP clients use the selected WAN
        # without changing the phone's existing default routes.
        table_id = "42002"
        rules_now = run(["ip", "rule", "show"])
        if not rules_now["ok"]:
            raise RuntimeError("Could not inspect existing policy-routing rules.")
        if re.search(r"^\s*1000:", rules_now["out"], re.MULTILINE):
            raise RuntimeError("Policy-routing priority 1000 is already in use.")
        if run(["ip", "route", "show", "table", table_id])["out"]:
            raise RuntimeError("Policy-routing table 42002 is already in use.")
        selected_route = next((x for x in defaults() if x.get("dev") == wan), None)
        if not selected_route:
            raise RuntimeError("Selected WAN default route disappeared.")
        gateway = selected_route.get("gateway")
        if gateway:
            r = run(["ip", "route", "replace", str(gateway) + "/32", "dev", wan, "scope", "link", "table", table_id])
            if not r["ok"]: raise RuntimeError("Could not add selected WAN gateway route: " + str(r["err"]))
            policy_routes_created = True
            r = run(["ip", "route", "replace", "default", "via", str(gateway), "dev", wan, "table", table_id])
        else:
            r = run(["ip", "route", "replace", "default", "dev", wan, "table", table_id])
            if r["ok"]: policy_routes_created = True
        if not r["ok"]: raise RuntimeError("Could not add selected WAN default route: " + str(r["err"]))
        r = run(["ip", "rule", "add", "priority", "1000", "iif", ap, "lookup", table_id])
        if not r["ok"]: raise RuntimeError("Could not add AP policy-routing rule: " + str(r["err"]))
        policy_rule_added = True
        state["policy_table"] = table_id
        state["policy_priority"] = "1000"
        state["policy_gateway"] = gateway
        rules = f'''table inet {TABLE} {{
 chain forward {{ type filter hook forward priority filter; policy accept;
  iifname "{ap}" oifname "{wan}" accept;
  iifname "{wan}" oifname "{ap}" ct state established,related accept;
 }}
 chain postrouting {{ type nat hook postrouting priority srcnat; policy accept;
  oifname "{wan}" ip saddr {cfg["network"]} masquerade;
 }}
}}'''

        if run(["nft", "list", "table", "inet", TABLE])["ok"]: raise RuntimeError("Keyland nftables table already exists; refusing to overwrite it.")
        r = run(["nft", "-f", "-"], stdin=rules)
        if not r["ok"]: raise RuntimeError("NAT rules failed: " + str(r["err"] or r["out"]))
        firewall_created = True
        state["firewall_table"] = TABLE
        write_state({k: v for k, v in state.items() if k != "password"})
        saved = {**cfg, "source_interface": p.get("source_interface", "wlan0")}
        _write_autostart(saved)
        return {"success": True, "active": True, "autostart": True, "mode": cfg["mode"], "interface": ap,
                "upstream_interface": wan, "ssid": cfg["ssid"], "ipv4_cidr": cfg["ipv4_cidr"],
                "band": cfg["band"], "channel": cfg["channel"], "dns_servers": cfg["dns_servers"], "dhcp": True, "nat": True,
                "message": "AP, DHCP and selected-uplink NAT started. Test a client connection."}
    except Exception as exc:
        process_errors = _stop_managed_processes(state)
        if process_errors:
            write_state({k: v for k, v in state.items() if k != "password"})
            return {
                "success": False,
                "error": str(exc),
                "cleanup_required": True,
                "process_errors": process_errors,
                "message": "Daemon shutdown not confirmed; network configuration left untouched.",
            }

        if firewall_created:
            run(["nft", "delete", "table", "inet", TABLE])
        if policy_rule_added:
            run(["ip", "rule", "del", "priority", "1000", "iif", ap, "lookup", "42002"])
        if policy_routes_created:
            run(["ip", "route", "flush", "table", "42002"])

        run(["ip", "addr", "flush", "dev", ap])
        run(["ip", "link", "set", ap, "down"])
        run(["sysctl", "-w", "net.ipv4.ip_forward=" + state["previous_ip_forward"]])

        if state.get("previous_wan_rp_filter") is not None:
            try:
                (Path("/proc/sys/net/ipv4/conf") / wan / "rp_filter").write_text(
                    state["previous_wan_rp_filter"] + "\n"
                )
            except OSError:
                pass

        run(["nmcli", "device", "set", ap, "managed", "yes"])
        for name in ("hostapd.conf", "dnsmasq.conf"):
            (ROOT / name).unlink(missing_ok=True)
        STATE.unlink(missing_ok=True)

        return {"success": False, "error": str(exc), "rolled_back": True}



def _valid_mac(value: str) -> str:
    mac = value.strip().lower()
    if not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", mac):
        raise ValueError("A valid client MAC address is required.")
    if mac == "ff:ff:ff:ff:ff:ff" or mac.startswith(("01:", "33:33:")):
        raise ValueError("Multicast/broadcast MAC addresses cannot be managed.")
    return mac


def client_block(mac: str, blocked: bool) -> dict[str, Any]:
    """Block/unblock one station MAC in the isolated Keyland AP forward path."""
    state = read_state()
    if not state or not status().get("active"):
        return {"success": False, "error": "Keyland hotspot is not active."}
    try:
        mac = _valid_mac(mac)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    ap = state["interface"]
    clients = {x.get("mac", "").lower() for x in _clients(ap)}
    if mac not in clients:
        return {"success": False, "error": "Client is not currently connected to this hotspot."}

    table = "keyland_client_control"
    setup = [
        ["nft", "add", "table", "inet", table],
        ["nft", "add", "set", "inet", table, "blocked_macs", "{", "type", "ether_addr", ";", "}"],
        ["nft", "add", "chain", "inet", table, "forward", "{", "type", "filter", "hook", "forward", "priority", "-10", ";", "policy", "accept", ";", "}"],
    ]
    if blocked:
        # Existing table/set/chain are expected on repeat requests. Ignore only
        # "already exists" errors; never replace or flush another firewall table.
        for command in setup:
            result = run(command)
            if not result["ok"] and "File exists" not in str(result["err"]):
                return {"success": False, "error": "Could not prepare isolated client firewall: " + str(result["err"] or result["out"])}
        chain = run(["nft", "list", "chain", "inet", table, "forward"])
        if not chain["ok"] or "@blocked_macs" not in chain["out"]:
            result = run(["nft", "add", "rule", "inet", table, "forward", "iifname", ap, "ether", "saddr", "@blocked_macs", "drop"])
            if not result["ok"]:
                return {"success": False, "error": "Could not add isolated client firewall rule: " + str(result["err"] or result["out"])}
        result = run(["nft", "add", "element", "inet", table, "blocked_macs", "{", mac, "}"])
        if not result["ok"] and "File exists" not in str(result["err"]):
            return {"success": False, "error": "Could not block client: " + str(result["err"] or result["out"])}
    else:
        result = run(["nft", "delete", "element", "inet", table, "blocked_macs", "{", mac, "}"])
        if not result["ok"]:
            return {"success": False, "error": "Client is not blocked or firewall rule is unavailable."}
    return {"success": True, "mac": mac, "blocked": blocked}


def client_status() -> dict[str, Any]:
    state = read_state()
    if not state:
        return {"active": False, "clients": []}
    clients = _clients(state.get("interface"))
    # Every associated station uses the AP's current radio channel/band.
    # Read the live AP frequency rather than trusting the requested setting.
    ap_radio = {"ap_frequency_mhz": None, "ap_channel": None, "ap_band": None}
    ap_name = state.get("interface")
    if ap_name:
        radio_info = run(["iw", "dev", ap_name, "info"])
        if radio_info["ok"]:
            for radio_line in radio_info["out"].splitlines():
                radio_line = radio_line.strip()
                match = re.search(r"channel\s+(\d+)\s+\((\d+)\s+MHz\)", radio_line)
                if match:
                    ap_radio["ap_channel"] = int(match.group(1))
                    ap_radio["ap_frequency_mhz"] = int(match.group(2))
                    break
    frequency = ap_radio["ap_frequency_mhz"]
    if frequency:
        ap_radio["ap_band"] = (
            "6 GHz" if frequency >= 5925
            else "5 GHz" if frequency >= 4900
            else "2.4 GHz" if 2400 <= frequency <= 2500
            else None
        )
    # Fall back to configured band only if the driver does not expose frequency.
    if not ap_radio["ap_band"]:
        configured_band = str(state.get("band") or "")
        ap_radio["ap_band"] = (
            "2.4 GHz" if configured_band.startswith("2.4")
            else "5 GHz" if configured_band.startswith("5")
            else "6 GHz" if configured_band.startswith("6")
            else None
        )
    # dnsmasq lease format: expiry MAC IPv4 hostname client-id
    leases = ROOT / "dnsmasq.leases"
    lease_by_mac: dict[str, dict[str, Any]] = {}
    try:
        for line in leases.read_text(errors="replace").splitlines():
            fields = line.split()
            if len(fields) >= 4:
                lease_by_mac[fields[1].lower()] = {
                    "lease_expiry": int(fields[0]) if fields[0].isdigit() else None,
                    "ip": fields[2],
                    "hostname": fields[3] if fields[3] != "*" else None,
                    "client_id": fields[4] if len(fields) >= 5 and fields[4] != "*" else None,
                }
    except OSError:
        pass

    # Fallback for hostnames retained by the previous Keyland DHCP instance.
    # Only use records matching currently associated clients and the AP subnet.
    fallback_leases = Path("/var/lib/misc/dnsmasq.leases")
    try:
        network = ipaddress.ip_network(
            state.get("network", "192.168.88.0/24"),
            strict=False,
        )
        active_macs = {
            str(client.get("mac", "")).lower()
            for client in clients
            if client.get("mac")
        }

        for line in fallback_leases.read_text(errors="replace").splitlines():
            fields = line.split()
            if len(fields) < 4:
                continue

            expiry_raw = fields[0]
            if not expiry_raw.isdigit():
                continue

            lease_expiry = int(expiry_raw)
            if lease_expiry != 0 and lease_expiry <= int(time.time()):
                continue

            mac = fields[1].lower()
            hostname = fields[3]

            if mac not in active_macs or hostname == "*":
                continue

            try:
                lease_ip = ipaddress.ip_address(fields[2])
            except ValueError:
                continue

            if lease_ip not in network:
                continue

            existing = lease_by_mac.setdefault(mac, {})
            if not existing.get("hostname"):
                existing["hostname"] = hostname

    except (OSError, ValueError, TypeError):
        pass

    blocked: set[str] = set()
    traffic_file = ROOT / "client-traffic.json"
    try:
        traffic_state = json.loads(traffic_file.read_text())
        limits = traffic_state.get("limits", {}) if traffic_state.get("interface") == state.get("interface") else {}
    except (OSError, ValueError, TypeError):
        limits = {}
    listed = run(["nft", "list", "set", "inet", "keyland_client_control", "blocked_macs"])
    if listed["ok"]:
        blocked = set(re.findall(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", listed["out"].lower()))
    for client in clients:
        lease = lease_by_mac.get(str(client.get("mac", "")).lower(), {})
        client.update(lease)
        client_mac = str(client.get("mac", "")).lower()
        client["blocked"] = client_mac in blocked
        client["speed_limit"] = limits.get(client_mac)
        signal = client.get("signal_avg", client.get("signal"))
        client["signal_quality_percent"] = (
            max(0, min(100, round((float(signal) + 100) * 2)))
            if isinstance(signal, (int, float)) else None
        )
        client["radio_details_available"] = any(
            key in client for key in ("tx_bitrate", "rx_bitrate", "signal", "signal_avg")
        )
    for client in clients:
        client.update(ap_radio)
    return {
        "active": bool(status().get("active")),
        "interface": state.get("interface"),
        "radio": ap_radio,
        "client_count": len(clients),
        "clients": clients,
    }



def dns_queries(limit: int = 200) -> dict[str, Any]:
    """Return domain metadata observed by Keyland's local dnsmasq only."""
    state = read_state()
    if not state or not status().get("active"):
        return {"active": False, "enabled": False, "queries": [], "message": "Hotspot is not active."}
    if state.get("dns_monitoring") is not True:
        return {"active": True, "enabled": False, "queries": [], "message": "Enable DNS monitoring in Hotspot settings."}
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise ValueError("DNS query limit must be between 1 and 1000.")
    try:
        log_text = (ROOT / "dnsmasq-query.log").read_text(errors="replace")
    except OSError:
        log_text = ""
    try:
        lease_text = (ROOT / "dnsmasq.leases").read_text(errors="replace")
    except OSError:
        lease_text = ""
    leases = parse_dhcp_leases(lease_text)
    queries = parse_dns_query_log(log_text, leases, limit=limit)
    return {
        "active": True, "enabled": True, "count": len(queries), "queries": queries,
        "privacy": "DNS domain metadata only; HTTPS contents, URLs, passwords and messages are not inspected.",
    }

def client_limit(mac: str, download_mbps: int, upload_mbps: int) -> dict[str, Any]:
    """Apply per-station shaping on the dedicated AP interface using tc flower."""
    state = read_state()
    if not state or not status().get("active"):
        return {"success": False, "error": "Keyland hotspot is not active."}
    try:
        mac = _valid_mac(mac)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    clients = {x.get("mac", "").lower() for x in _clients(state["interface"])}
    if mac not in clients:
        return {"success": False, "error": "Client is not currently connected to this hotspot."}
    if not (1 <= download_mbps <= 1000 and 1 <= upload_mbps <= 1000):
        return {"success": False, "error": "Speed limits must be between 1 and 1000 Mbps."}
    if not shutil.which("tc"):
        return {"success": False, "error": "The tc utility is required for per-device speed limits."}

    ap = state["interface"]
    # Keep the qdisc namespace exclusive to Keyland; never overwrite an
    # existing non-Keyland qdisc or alter the WAN/upstream interface.
    current = run(["tc", "qdisc", "show", "dev", ap])
    if not current["ok"]:
        return {"success": False, "error": "Could not inspect AP traffic-control state."}
    root_exists = any(line.startswith("qdisc htb 1:") for line in current["out"].splitlines())
    ingress_exists = any(" ingress " in (" " + line + " ") for line in current["out"].splitlines())
    owner_file = ROOT / "client-traffic.json"
    try:
        owner_state = json.loads(owner_file.read_text())
    except (OSError, ValueError):
        owner_state = None
    if root_exists and (not owner_state or owner_state.get("interface") != ap):
        return {"success": False, "error": "AP has an unowned HTB qdisc; refusing to change traffic control."}
    if ingress_exists and (not owner_state or owner_state.get("interface") != ap):
        return {"success": False, "error": "AP has an unowned ingress qdisc; refusing to replace it."}
    if current["out"].strip() and not root_exists and not any(line.startswith("qdisc noqueue") for line in current["out"].splitlines()):
        return {"success": False, "error": "AP already has a non-Keyland traffic-control qdisc; refusing to replace it."}

    created_root = False
    if not root_exists:
        commands = [
            ["tc", "qdisc", "add", "dev", ap, "root", "handle", "1:", "htb", "default", "999"],
            ["tc", "class", "add", "dev", ap, "parent", "1:", "classid", "1:1", "htb", "rate", "1000mbit", "ceil", "1000mbit"],
            ["tc", "class", "add", "dev", ap, "parent", "1:1", "classid", "1:999", "htb", "rate", "1000mbit", "ceil", "1000mbit"],
        ]
        for index, command in enumerate(commands):
            result = run(command)
            if not result["ok"]:
                run(["tc", "qdisc", "del", "dev", ap, "root"])
                return {"success": False, "error": "Could not initialize isolated AP shaping: " + str(result["err"] or result["out"])}
        created_root = True
    # A stable class ID derived from the MAC avoids shell interpolation and
    # permits independent client entries. Hash collisions are checked below.
    class_id = 1000 + (int(mac.replace(":", ""), 16) % 59000)
    classes = run(["tc", "class", "show", "dev", ap])
    if classes["ok"]:
        occupied = re.findall(r"class htb 1:(\d+)", classes["out"])
        if str(class_id) in occupied:
            # Existing class is replaced only if its matching MAC filter exists.
            filters = run(["tc", "filter", "show", "dev", ap, "parent", "1:"])
            if not filters["ok"] or mac not in filters["out"].lower():
                return {"success": False, "error": "Traffic-control class ID collision; no change made."}

    commands = [
        ["tc", "class", "replace", "dev", ap, "parent", "1:1", "classid", f"1:{class_id}", "htb", "rate", f"{download_mbps}mbit", "ceil", f"{download_mbps}mbit"],
        ["tc", "filter", "replace", "dev", ap, "protocol", "all", "parent", "1:", "prio", str(class_id), "flower", "dst_mac", mac, "classid", f"1:{class_id}"],
        ["tc", "qdisc", "replace", "dev", ap, "handle", "ffff:", "ingress"],
        ["tc", "filter", "replace", "dev", ap, "parent", "ffff:", "protocol", "all", "prio", str(class_id), "flower", "src_mac", mac, "action", "police", "rate", f"{upload_mbps}mbit", "burst", "256k", "drop"],
    ]
    for command in commands:
        result = run(command)
        if not result["ok"]:
            if created_root:
                run(["tc", "qdisc", "del", "dev", ap, "root"])
                run(["tc", "qdisc", "del", "dev", ap, "ingress"])
            return {"success": False, "error": "Traffic shaping command failed: " + str(result["err"] or result["out"]), "partial": True}
    owner_state = owner_state if isinstance(owner_state, dict) else {"interface": ap, "limits": {}}
    owner_state.setdefault("limits", {})[mac] = {
        "download_mbps": download_mbps,
        "upload_mbps": upload_mbps,
    }
    owner_state["interface"] = ap
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    owner_file.write_text(json.dumps(owner_state))
    os.chmod(owner_file, 0o600)
    return {"success": True, "mac": mac, "download_mbps": download_mbps, "upload_mbps": upload_mbps, "interface": ap}

def stop(confirmed: bool) -> dict[str, Any]:
    if confirmed is not True: return {"success": False, "error": "Explicit administrator confirmation is required."}
    portal_result = captive_disable()
    if not portal_result.get("success"):
        return {"success": False, "error": "Captive Portal could not be safely disabled: " + str(portal_result.get("error"))}
    state = read_state()
    if not state: return {"success": False, "error": "Keyland router Wi-Fi is not active."}
    ap, errors = state["interface"], []
    process_errors = _stop_managed_processes(state)
    if process_errors:
        return {
            "success": False,
            "active": True,
            "configured": True,
            "errors": process_errors,
            "message": "Daemon shutdown not confirmed; AP networking left untouched.",
        }
    traffic_state_file = ROOT / "client-traffic.json"
    try:
        traffic_state = json.loads(traffic_state_file.read_text())
    except (OSError, ValueError):
        traffic_state = None
    if isinstance(traffic_state, dict) and traffic_state.get("interface") == ap:
        qdiscs = run(["tc", "qdisc", "show", "dev", ap])
        if qdiscs["ok"]:
            if any(line.startswith("qdisc htb 1:") for line in qdiscs["out"].splitlines()):
                run(["tc", "qdisc", "del", "dev", ap, "root"])
            if any(" ingress " in (" " + line + " ") for line in qdiscs["out"].splitlines()):
                run(["tc", "qdisc", "del", "dev", ap, "ingress"])
        traffic_state_file.unlink(missing_ok=True)
    run(["nft", "delete", "table", "inet", "keyland_client_control"])
    # Remove only Keyland's AP rules: the current priority and the legacy one.
    run(["ip", "rule", "del", "priority", "1000", "iif", ap, "lookup", "42002"])
    run(["ip", "rule", "del", "priority", "42002", "iif", ap, "lookup", "42002"])
    run(["ip", "route", "flush", "table", "42002"])
    r = run(["nft", "delete", "table", "inet", TABLE])
    if not r["ok"] and "No such file" not in str(r["err"]): errors.append("Could not remove Keyland NAT table.")
    run(["ip", "addr", "flush", "dev", ap]); run(["ip", "link", "set", ap, "down"])
    run(["sysctl", "-w", "net.ipv4.ip_forward=" + state.get("previous_ip_forward", "0")])
    if state.get("previous_wan_rp_filter") is not None:
        try:
            (Path("/proc/sys/net/ipv4/conf") / state["upstream_interface"] / "rp_filter").write_text(state["previous_wan_rp_filter"] + "\n")
        except OSError:
            errors.append("Could not restore the cellular WAN reverse-path filter setting.")
    if not run(["nmcli", "device", "set", ap, "managed", "yes"])["ok"]: errors.append("Could not restore NetworkManager management.")
    if not errors:
        STATE.unlink(missing_ok=True)
        for name in ("hostapd.conf", "dnsmasq.conf"):
            (ROOT / name).unlink(missing_ok=True)
        disabled = disable_autostart()
        if not disabled.get("success"):
            errors.append(disabled.get("error", "Could not disable automatic startup."))
    return {"success": not errors, "active": False, "autostart": AUTOSTART_FILE.exists(), "configured": bool(errors), "errors": errors,
            "message": "AP and NAT stopped; automatic startup disabled; selected upstream was not reconfigured."}


CAPTIVE_TABLE = "keyland_captive"
CAPTIVE_STATE = ROOT / "captive-portal.json"


def _portal_process_alive(pid: object) -> bool:
    try:
        number = int(pid)
        if number <= 1:
            return False
        cmdline = Path(f"/proc/{number}/cmdline").read_bytes().decode(errors="replace")
        return "keyland.captive_http" in cmdline
    except (OSError, ValueError, TypeError):
        return False


def captive_status() -> dict[str, Any]:
    router_state = read_state()
    router_active = bool(router_state and status().get("active"))
    firewall = run(["nft", "list", "table", "inet", CAPTIVE_TABLE])
    try:
        portal_state = json.loads(CAPTIVE_STATE.read_text())
    except (OSError, ValueError, TypeError):
        portal_state = {}
    server_running = _portal_process_alive(portal_state.get("pid"))
    enabled = bool(firewall["ok"])
    return {
        "enabled": enabled,
        "active": enabled and server_running,
        "server_running": server_running,
        "runtime_ready": router_active,
        "interface": (router_state or {}).get("interface"),
        "gateway": (router_state or {}).get("gateway"),
        "network": (router_state or {}).get("network"),
        "message": (
            "Captive Portal is active."
            if enabled and server_running
            else "Captive Portal firewall is installed but its HTTP service is not running."
            if enabled
            else "Start Keyland Router Wi-Fi before enabling Captive Portal."
            if not router_active
            else "Captive Portal runtime is ready."
        ),
    }


def captive_enable() -> dict[str, Any]:
    state = read_state()
    if not state or not status().get("active"):
        return {"success": False, "error": "Start Keyland Router Wi-Fi before enabling Captive Portal."}
    ap = state.get("interface")
    gateway = state.get("gateway")
    network = state.get("network")
    if not isinstance(ap, str) or not IFACE.fullmatch(ap):
        return {"success": False, "error": "The active AP interface is invalid."}
    try:
        gateway_ip = ipaddress.ip_address(gateway)
        ap_network = ipaddress.ip_network(network, strict=False)
        if gateway_ip.version != 4 or gateway_ip not in ap_network:
            raise ValueError("invalid gateway")
    except (ValueError, TypeError):
        return {"success": False, "error": "The active AP IPv4 configuration is invalid."}
    settings_path = Path(__file__).resolve().parents[3] / "data" / "captive-portal.json"
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"success": False, "error": "Save valid Captive Portal settings before activation."}
    if not isinstance(settings, dict):
        return {"success": False, "error": "Captive Portal settings are invalid."}
    if settings.get("auth_mode") in ("password", "both") and not settings.get("password_hash"):
        return {"success": False, "error": "Configure the guest password before activation."}
    if run(["nft", "list", "table", "inet", CAPTIVE_TABLE])["ok"]:
        return {"success": False, "error": "Captive Portal firewall already exists; inspect status or disable it first."}
    if CAPTIVE_STATE.exists():
        try:
            stale = json.loads(CAPTIVE_STATE.read_text())
        except (OSError, ValueError, TypeError):
            stale = {}
        stale_pid = stale.get("pid") if isinstance(stale, dict) else None
        if _portal_process_alive(stale_pid):
            try:
                os.kill(int(stale_pid), signal.SIGTERM)
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline and _portal_process_alive(stale_pid):
                    time.sleep(0.1)
            except (OSError, ValueError, TypeError):
                return {"success": False, "error": "A stale captive HTTP process could not be stopped safely."}
            if _portal_process_alive(stale_pid):
                return {"success": False, "error": "A stale captive HTTP process is still running."}
        CAPTIVE_STATE.unlink(missing_ok=True)
    if not shutil.which("nft") or not shutil.which("ip") or not shutil.which("iw"):
        return {"success": False, "error": "nftables, iproute2 and iw are required."}

    project_root = Path(__file__).resolve().parents[3]
    log_path = ROOT / "captive-portal.log"
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        log_stream = log_path.open("a")
        process = subprocess.Popen(
            [
                sys.executable, "-m", "keyland.captive_http",
                "--host", str(gateway_ip), "--port", "8088",
                "--ap", ap, "--network", str(ap_network),
                "--gateway", str(gateway_ip),
            ],
            cwd=str(project_root),
            stdin=subprocess.DEVNULL,
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        log_stream.close()
    except OSError as exc:
        return {"success": False, "error": f"Could not start the captive HTTP service: {exc}"}

    ready = False
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        try:
            with socket.create_connection((str(gateway_ip), 8088), timeout=0.4):
                ready = True
                break
        except OSError:
            time.sleep(0.2)
    if not ready:
        try:
            process.terminate()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            pass
        return {"success": False, "error": "Captive HTTP service did not become ready; existing Wi-Fi rules were not changed."}

    rules = f"""table inet {CAPTIVE_TABLE} {{
 set authorized_clients {{
  type ipv4_addr . ether_addr
  flags timeout
  timeout 24h
 }}
 chain prerouting {{
  type nat hook prerouting priority -110; policy accept;
  iifname "{ap}" ip protocol tcp tcp dport 80 dnat to {gateway_ip}:8088
  iifname "{ap}" udp dport 53 dnat to {gateway_ip}:53
  iifname "{ap}" tcp dport 53 dnat to {gateway_ip}:53
 }}
 chain input {{
  type filter hook input priority -20; policy accept;
  iifname "{ap}" tcp dport 8088 accept
  iifname "{ap}" udp dport 53 accept
  iifname "{ap}" tcp dport 53 accept
  iifname "{ap}" udp dport 67 accept
 }}
 chain forward {{
  type filter hook forward priority -20; policy accept;
  iifname "{ap}" ip saddr . ether saddr @authorized_clients accept
  iifname "{ap}" meta nfproto ipv6 drop
  iifname "{ap}" drop
 }}
}}"""
    applied = run(["nft", "-f", "-"], stdin=rules)
    if not applied["ok"]:
        try:
            process.terminate()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            pass
        return {"success": False, "error": "Could not install isolated Captive Portal rules: " + str(applied["err"] or applied["out"])}
    portal_state = {
        "pid": process.pid,
        "interface": ap,
        "gateway": str(gateway_ip),
        "network": str(ap_network),
        "port": 8088,
        "started_at": int(time.time()),
    }
    temporary = CAPTIVE_STATE.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(portal_state))
        os.chmod(temporary, 0o600)
        temporary.replace(CAPTIVE_STATE)
    except OSError as exc:
        run(["nft", "delete", "table", "inet", CAPTIVE_TABLE])
        try:
            process.terminate()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            pass
        return {"success": False, "error": f"Could not persist Captive Portal state: {exc}"}
    return {
        "success": True,
        "enabled": True,
        "active": True,
        "interface": ap,
        "gateway": str(gateway_ip),
        "network": str(ap_network),
        "http_redirect_port": 8088,
        "message": "Captive Portal enabled. New clients must authorize before IPv4 internet forwarding.",
    }


def captive_disable() -> dict[str, Any]:
    firewall = run(["nft", "list", "table", "inet", CAPTIVE_TABLE])
    if firewall["ok"]:
        removed = run(["nft", "delete", "table", "inet", CAPTIVE_TABLE])
        if not removed["ok"]:
            return {"success": False, "error": "Could not remove Captive Portal firewall rules: " + str(removed["err"] or removed["out"])}
    try:
        portal_state = json.loads(CAPTIVE_STATE.read_text())
    except (OSError, ValueError, TypeError):
        portal_state = {}
    pid = portal_state.get("pid") if isinstance(portal_state, dict) else None
    if _portal_process_alive(pid):
        try:
            os.kill(int(pid), signal.SIGTERM)
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline and _portal_process_alive(pid):
                time.sleep(0.1)
            if _portal_process_alive(pid):
                return {"success": False, "enabled": False, "error": "Firewall was removed, but the portal HTTP process did not stop."}
        except (OSError, ValueError, TypeError) as exc:
            return {"success": False, "enabled": False, "error": f"Firewall was removed, but the portal HTTP process could not be stopped: {exc}"}
    CAPTIVE_STATE.unlink(missing_ok=True)
    return {"success": True, "enabled": False, "active": False, "message": "Captive Portal disabled; only its own firewall rules were removed."}

