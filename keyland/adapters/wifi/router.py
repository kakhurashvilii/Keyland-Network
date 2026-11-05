"""Hostapd + dnsmasq + nftables controller for a dedicated Keyland AP."""
from __future__ import annotations
import ipaddress, json, os, re, shutil, signal, subprocess, time
from pathlib import Path
from typing import Any
from keyland.adapters.wifi.capabilities import parse_interface_combinations

ROOT = Path("/run/keyland-router")
STATE = ROOT / "state.json"
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
    if cur: out.append(cur)
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
    if not ap: return []
    r = run(["iw", "dev", ap, "station", "dump"])
    if not r["ok"]: return []
    clients: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw in r["out"].splitlines():
        line = raw.strip()
        if line.startswith("Station "):
            if current: clients.append(current)
            current = {"mac": line.split()[1]}
        elif current and ":" in line:
            key, value = line.split(":", 1)
            key = key.strip().lower().replace(" ", "_")
            value = value.strip()
            if key in ("signal", "signal_avg"):
                current[key] = value
            elif key in ("rx_bytes", "tx_bytes", "connected_time"):
                try: current[key] = int(value.split()[0])
                except (ValueError, IndexError): pass
    if current: clients.append(current)
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


def start(p: dict[str, Any]) -> dict[str, Any]:
    if p.get("confirmed") is not True: return {"success": False, "error": "Explicit administrator confirmation is required."}
    if read_state(): return {"success": False, "error": "Router Wi-Fi is already active. Stop it first."}
    try: cfg = _validate(p)
    except ValueError as exc: return {"success": False, "error": str(exc)}
    ap, wan = cfg["interface"], cfg["upstream_interface"]
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    state = {**cfg, "previous_ip_forward": Path("/proc/sys/net/ipv4/ip_forward").read_text().strip(),
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
        dconf.write_text(f"interface={ap}\nbind-interfaces\nexcept-interface=lo\ndhcp-range={first},{last},{net.netmask},12h\ndhcp-option=3,{cfg['gateway']}\ndhcp-option=6,{','.join(cfg['dns_servers'])}\nno-resolv\n" + "".join(f"server={x}\n" for x in cfg["dns_servers"]) + f"pid-file={ROOT / 'dnsmasq.pid'}\n")
        os.chmod(dconf, 0o600)
        dp = subprocess.Popen(["dnsmasq", "--keep-in-foreground", "--conf-file=" + str(dconf)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        state["dnsmasq_pid"] = dp.pid
        time.sleep(1)
        if dp.poll() is not None: raise RuntimeError("dnsmasq exited during startup.")
        # Add an isolated policy-routing table so AP clients use the selected WAN
        # without changing the phone's existing default routes.
        table_id = "42002"
        rules_now = run(["ip", "rule", "show"])
        if not rules_now["ok"] or "42002:" in rules_now["out"]:
            raise RuntimeError("Policy-routing priority 42002 is already in use.")
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
        r = run(["ip", "rule", "add", "priority", "42002", "iif", ap, "lookup", table_id])
        if not r["ok"]: raise RuntimeError("Could not add AP policy-routing rule: " + str(r["err"]))
        policy_rule_added = True
        state["policy_table"] = table_id
        state["policy_priority"] = "42002"
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
        return {"success": True, "active": True, "mode": cfg["mode"], "interface": ap,
                "upstream_interface": wan, "ssid": cfg["ssid"], "ipv4_cidr": cfg["ipv4_cidr"],
                "band": cfg["band"], "channel": cfg["channel"], "dns_servers": cfg["dns_servers"], "dhcp": True, "nat": True,
                "message": "AP, DHCP and selected-uplink NAT started. Test a client connection."}
    except Exception as exc:
        if firewall_created: run(["nft", "delete", "table", "inet", TABLE])
        if policy_rule_added: run(["ip", "rule", "del", "priority", "42002", "iif", ap, "lookup", "42002"])
        if policy_routes_created: run(["ip", "route", "flush", "table", "42002"])
        for key in ("dnsmasq_pid", "hostapd_pid"):
            try: os.kill(int(state.get(key, 0)), signal.SIGTERM)
            except (OSError, ValueError, TypeError): pass
        run(["ip", "addr", "flush", "dev", ap]); run(["ip", "link", "set", ap, "down"])
        run(["sysctl", "-w", "net.ipv4.ip_forward=" + state["previous_ip_forward"]])
        if state.get("previous_wan_rp_filter") is not None:
            try:
                (Path("/proc/sys/net/ipv4/conf") / wan / "rp_filter").write_text(state["previous_wan_rp_filter"] + "\n")
            except OSError:
                pass
        run(["nmcli", "device", "set", ap, "managed", "yes"])
        for name in ("hostapd.conf", "dnsmasq.conf"):
            (ROOT / name).unlink(missing_ok=True)
        return {"success": False, "error": str(exc), "rolled_back": True}


def stop(confirmed: bool) -> dict[str, Any]:
    if confirmed is not True: return {"success": False, "error": "Explicit administrator confirmation is required."}
    state = read_state()
    if not state: return {"success": False, "error": "Keyland router Wi-Fi is not active."}
    ap, errors = state["interface"], []
    run(["ip", "rule", "del", "priority", "42002", "iif", ap, "lookup", "42002"])
    run(["ip", "route", "flush", "table", "42002"])
    r = run(["nft", "delete", "table", "inet", TABLE])
    if not r["ok"] and "No such file" not in str(r["err"]): errors.append("Could not remove Keyland NAT table.")
    for key in ("dnsmasq_pid", "hostapd_pid"):
        try: os.kill(int(state[key]), signal.SIGTERM)
        except (OSError, KeyError, ValueError, TypeError): pass
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
    return {"success": not errors, "active": False, "configured": bool(errors), "errors": errors,
            "message": "AP and NAT stopped; selected upstream was not reconfigured."}
