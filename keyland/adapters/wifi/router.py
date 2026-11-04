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
            phy = line
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
    # The driver must explicitly report AP + managed concurrency on a shared PHY.
    station = next((x for x in devs if x["type"] == "managed" and x.get("ssid")), None)
    if station and station.get("phy") == apdev.get("phy"):
        iw_list = run(["iw", "list"], timeout=20)
        combinations = parse_interface_combinations(iw_list["out"]) if iw_list["ok"] else {}
        if not combinations.get("ap_client_combination_mentioned"):
            raise ValueError("The Wi-Fi driver does not confirm simultaneous AP + Client operation on this PHY.")
    # Shared PHY must remain on the station's channel.
    channel = (station or {}).get("channel") or apdev.get("channel") or 6
    return {"mode": mode, "interface": ap, "upstream_interface": wan, "ssid": ssid,
            "password": password, "ipv4_cidr": str(addr), "gateway": str(addr.ip),
            "network": str(addr.network), "dns_servers": dns, "channel": channel}


def start(p: dict[str, Any]) -> dict[str, Any]:
    if p.get("confirmed") is not True: return {"success": False, "error": "Explicit administrator confirmation is required."}
    if read_state(): return {"success": False, "error": "Router Wi-Fi is already active. Stop it first."}
    try: cfg = _validate(p)
    except ValueError as exc: return {"success": False, "error": str(exc)}
    ap, wan = cfg["interface"], cfg["upstream_interface"]
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    state = {**cfg, "previous_ip_forward": Path("/proc/sys/net/ipv4/ip_forward").read_text().strip()}
    policy_routes_created = False
    policy_rule_added = False
    firewall_created = False
    try:
        # Only the dedicated AP is detached from NetworkManager; upstream is untouched.
        r = run(["nmcli", "device", "set", ap, "managed", "no"])
        if not r["ok"]: raise RuntimeError("Could not detach AP from NetworkManager: " + str(r["err"] or r["out"]))
        r = run(["sysctl", "-w", "net.ipv4.ip_forward=1"])
        if not r["ok"]: raise RuntimeError("Could not enable IPv4 forwarding.")
        run(["ip", "link", "set", ap, "down"])
        run(["ip", "addr", "flush", "dev", ap])
        if not run(["ip", "addr", "replace", cfg["ipv4_cidr"], "dev", ap])["ok"]: raise RuntimeError("Could not assign AP gateway IP.")
        if not run(["ip", "link", "set", ap, "up"])["ok"]: raise RuntimeError("Could not bring AP interface up.")
        hconf = ROOT / "hostapd.conf"
        hconf.write_text(f"interface={ap}\ndriver=nl80211\nssid={cfg['ssid']}\ncountry_code=GE\nhw_mode=g\nchannel={cfg['channel']}\nieee80211n=1\nwmm_enabled=1\nauth_algs=1\nwpa=2\nwpa_key_mgmt=WPA-PSK\nrsn_pairwise=CCMP\nwpa_passphrase={cfg['password']}\n")
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
                "channel": cfg["channel"], "dns_servers": cfg["dns_servers"], "dhcp": True, "nat": True,
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
    if not run(["nmcli", "device", "set", ap, "managed", "yes"])["ok"]: errors.append("Could not restore NetworkManager management.")
    if not errors:
        STATE.unlink(missing_ok=True)
        for name in ("hostapd.conf", "dnsmasq.conf"):
            (ROOT / name).unlink(missing_ok=True)
    return {"success": not errors, "active": False, "configured": bool(errors), "errors": errors,
            "message": "AP and NAT stopped; selected upstream was not reconfigured."}
