"""Command runner (no shell) and parsers for iw / ip / mmcli / nmcli output."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from typing import Callable


@dataclass
class CmdResult:
    ok: bool
    out: str = ""
    err: str = ""


Runner = Callable[..., CmdResult]


def run_cmd(args: list[str], timeout: int = 10, input: str | None = None) -> CmdResult:
    try:
        r = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout,
            input=input, check=False, errors="replace",
        )
        return CmdResult(r.returncode == 0, r.stdout.strip(), r.stderr.strip())
    except FileNotFoundError:
        return CmdResult(False, "", f"command not found: {args[0]}")
    except subprocess.TimeoutExpired:
        return CmdResult(False, "", "timeout")
    except OSError as exc:
        return CmdResult(False, "", str(exc))


def parse_iw_info(text: str) -> dict:
    """`iw dev X info` -> {type, ssid, channel, freq}. Missing fields stay None."""
    info = {"type": None, "ssid": None, "channel": None, "freq": None}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("type "):
            info["type"] = line.split(maxsplit=1)[1]
        elif line.startswith("ssid "):
            info["ssid"] = line[5:]
        else:
            m = re.search(r"channel\s+(\d+)\s+\((\d+)\s+MHz\)", line)
            if m:
                info["channel"], info["freq"] = int(m.group(1)), int(m.group(2))
    return info


def parse_station_dump(text: str) -> list[dict]:
    stations, cur = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("Station "):
            cur = {"mac": line.split()[1], "signal_dbm": None, "connected_s": None}
            stations.append(cur)
        elif cur is not None:
            m = re.match(r"signal:\s+(-?\d+)", line)
            if m:
                cur["signal_dbm"] = int(m.group(1))
            m = re.match(r"connected time:\s+(\d+)", line)
            if m:
                cur["connected_s"] = int(m.group(1))
    return stations


def parse_leases(text: str) -> dict[str, dict]:
    """dnsmasq lease file: `expiry mac ip hostname clientid`."""
    leases = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            leases[parts[1].lower()] = {
                "ip": parts[2],
                "hostname": "" if parts[3] == "*" else parts[3],
            }
    return leases


def parse_ping_latency(text: str) -> float | None:
    m = re.search(r"time[=<]([\d.]+)\s*ms", text)
    return float(m.group(1)) if m else None


def default_gateway(run: Runner, iface: str) -> str | None:
    """Gateway of a default route bound to iface in the main table (None for p2p links)."""
    r = run(["ip", "-j", "-4", "route", "show", "default", "dev", iface])
    if not r.ok or not r.out:
        return None
    try:
        for route in json.loads(r.out):
            if route.get("gateway"):
                return route["gateway"]
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def iface_ipv4(run: Runner, iface: str) -> str | None:
    r = run(["ip", "-j", "-4", "addr", "show", "dev", iface])
    if not r.ok or not r.out:
        return None
    try:
        for item in json.loads(r.out):
            for a in item.get("addr_info", []):
                if a.get("local"):
                    return a["local"]
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def iface_exists(run: Runner, iface: str) -> bool:
    return run(["ip", "link", "show", "dev", iface]).ok


def detect_sim_iface(run: Runner) -> str | None:
    """ModemManager bearer interface first, then common modem interface names."""
    r = run(["mmcli", "-b", "0", "-K"])
    if r.ok:
        m = re.search(r"bearer\.status\.interface\s*:\s*(\S+)", r.out)
        if m and m.group(1) != "--" and iface_exists(run, m.group(1)):
            return m.group(1)
    r = run(["ip", "-j", "-o", "link", "show"])
    names: list[str] = []
    if r.ok and r.out:
        try:
            names = [i["ifname"] for i in json.loads(r.out)]
        except (json.JSONDecodeError, KeyError, TypeError):
            names = []
    for prefix in ("qmapmux", "rmnet_data", "rmnet_ipa", "wwan", "wwp", "ccmni"):
        for n in sorted(names):
            if n.startswith(prefix) and iface_ipv4(run, n):
                return n
    return None


def sim_status(run: Runner) -> dict:
    r = run(["mmcli", "-m", "0", "-J"], timeout=12)
    if not r.ok:
        return {"available": False, "error": r.err or "no modem"}
    try:
        modem = json.loads(r.out).get("modem", {})
    except (json.JSONDecodeError, AttributeError):
        return {"available": False, "error": "unreadable modem output"}
    g = modem.get("generic", {})
    gpp = modem.get("3gpp", {})
    quality = g.get("signal-quality", {})
    return {
        "available": True,
        "state": g.get("state"),
        "operator": gpp.get("operator-name") or None,
        "access_tech": g.get("access-technologies"),
        "signal_percent": int(quality["value"]) if str(quality.get("value", "")).isdigit() else None,
        "registration": gpp.get("registration-state"),
    }


def find_gsm_connection(run: Runner) -> str | None:
    r = run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"])
    if not r.ok:
        return None
    for line in r.out.splitlines():
        name, _, typ = line.rpartition(":")
        if typ in ("gsm", "cdma"):
            return name.replace("\\:", ":")
    return None


def freq_to_channel(freq: int | float | None) -> int | None:
    if not freq:
        return None
    freq = int(freq)
    if freq == 2484:
        return 14
    if 2412 <= freq <= 2472:
        return (freq - 2407) // 5
    if 5000 <= freq <= 5895:
        return (freq - 5000) // 5
    if 5955 <= freq <= 7115:
        return (freq - 5950) // 5
    return None


def parse_iw_link(text: str) -> dict:
    """`iw dev X link` -> uplink quality."""
    info = {"connected": False, "ssid": None, "bssid": None, "freq": None, "channel": None,
            "band": None, "signal_dbm": None, "rx_mbit": None, "tx_mbit": None}
    if not text or text.startswith("Not connected"):
        return info
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r"Connected to ([0-9a-fA-F:]{17})", line)
        if m:
            info["connected"], info["bssid"] = True, m.group(1).lower()
        elif line.startswith("SSID:"):
            info["ssid"] = line[5:].strip()
        elif line.startswith("freq:"):
            try:
                info["freq"] = int(float(line.split(":", 1)[1]))
            except ValueError:
                pass
        elif line.startswith("signal:"):
            m = re.search(r"(-?\d+)", line)
            info["signal_dbm"] = int(m.group(1)) if m else None
        elif line.startswith(("rx bitrate:", "tx bitrate:")):
            m = re.search(r"([\d.]+)\s*MBit/s", line)
            if m:
                info["rx_mbit" if line.startswith("rx") else "tx_mbit"] = float(m.group(1))
    info["channel"] = freq_to_channel(info["freq"])
    if info["freq"]:
        info["band"] = "5 GHz" if info["freq"] >= 5000 else "2.4 GHz"
    return info


def _nm_split(line: str) -> list[str]:
    parts = re.split(r"(?<!\\):", line)
    return [p.replace("\\:", ":").replace("\\\\", "\\") for p in parts]


def parse_nmcli_wifi_list(text: str, saved: set[str] | None = None) -> list[dict]:
    """`nmcli -t -f IN-USE,SSID,BSSID,CHAN,FREQ,SIGNAL,SECURITY device wifi list`."""
    saved = saved or set()
    best: dict[str, dict] = {}
    for line in text.splitlines():
        f = _nm_split(line)
        if len(f) < 7 or not f[1]:
            continue
        in_use, ssid, bssid, chan, freq, signal, sec = f[0], f[1], f[2], f[3], f[4], f[5], ":".join(f[6:])
        try:
            sig = int(signal)
        except ValueError:
            sig = 0
        try:
            freq_mhz = int(re.match(r"\d+", freq).group(0))
        except (AttributeError, ValueError):
            freq_mhz = None
        entry = {
            "ssid": ssid, "bssid": bssid.lower(), "signal": sig,
            "channel": int(chan) if chan.isdigit() else freq_to_channel(freq_mhz),
            "band": ("5 GHz" if freq_mhz and freq_mhz >= 5000 else "2.4 GHz") if freq_mhz else None,
            "security": "" if sec in ("", "--") else sec,
            "secured": sec not in ("", "--"),
            "in_use": in_use.strip() == "*",
            "saved": ssid in saved,
        }
        old = best.get(ssid)
        if old is None or entry["in_use"] or (sig > old["signal"] and not old["in_use"]):
            best[ssid] = entry
    return sorted(best.values(), key=lambda e: (not e["in_use"], -e["signal"]))


def list_saved_wifi(run: Runner) -> set[str]:
    r = run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"])
    names: set[str] = set()
    if r.ok:
        for line in r.out.splitlines():
            parts = _nm_split(line)
            if len(parts) >= 2 and parts[-1] == "802-11-wireless":
                names.add(":".join(parts[:-1]))
    return names
