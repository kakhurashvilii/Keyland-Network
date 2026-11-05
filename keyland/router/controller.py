"""RouterController: starts/stops hotspot (hostapd + dnsmasq + nftables) and
steers hotspot traffic between Wi-Fi WAN and SIM WAN with failover/balancing.

Design rules:
  * Never use a shell; every command is an argv list built from validated values.
  * Only own resources are touched: iface ap0, nft tables `keyland*`,
    routing table 100 + one ip rule. NetworkManager, Tailscale, ZeroTier and LXC
    configuration are left alone.
  * Everything is reversible: stop() undoes what start() did.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from keyland.adapters.wifi.capabilities import (
    parse_interface_combinations, parse_supported_modes,
)
from .config import (
    ROOT, RouterConfig, load_config, save_config, subnet_conflicts,
)
from .render import hw_mode_for, render_dnsmasq, render_hostapd, render_nft
from .system import (
    Runner, default_gateway, detect_sim_iface, find_gsm_connection, iface_exists,
    iface_ipv4, parse_iw_info, parse_iw_link, parse_leases, parse_ping_latency,
    parse_nmcli_wifi_list, list_saved_wifi,
    parse_station_dump, run_cmd, sim_status,
)
from .wan import (
    RULE_PRIORITY, TABLE_ID, WanState, build_route_cmds, choose_active,
    record_probe,
)

REQUIRED_TOOLS = ["ip", "iw", "hostapd", "dnsmasq", "nft", "ping"]
SSID_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f]")
MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")


class RouterError(Exception):
    pass


def _pick_run_dir() -> Path:
    wanted = Path(os.getenv("KEYLAND_RUN_DIR", "/run/keyland"))
    for candidate in (wanted, ROOT / "data" / "run"):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            os.chmod(candidate, 0o700)
            return candidate
        except OSError:
            continue
    raise RouterError("no writable runtime directory")


class RouterController:
    def __init__(self, config_path: Path | None = None, run: Runner = run_cmd,
                 run_dir: Path | None = None):
        self._run = run
        self._config_path = config_path
        self._lock = threading.RLock()
        self.cfg: RouterConfig = load_config(config_path)
        self.run_dir = run_dir or _pick_run_dir()
        self.ctrl_dir = self.run_dir / "hostapd_ctrl"
        self.deny_file = self.run_dir / "hostapd.deny"
        self.state = "stopped"          # stopped | starting | running | error
        self.error: str | None = None
        self.started_at: float | None = None
        self.events: deque[dict] = deque(maxlen=200)
        self.procs: dict[str, subprocess.Popen] = {}
        self.wans: dict[str, WanState] = {}
        self.active: list[str] = []
        self.ap_channel: int | None = None
        self._route_sig: tuple | None = None
        self._saved_sysctl: dict[str, str] = {}
        self._sim_iface_cache: str | None = None
        self._channel_mismatch = 0
        self.uplink: dict = {}
        self._sim_cache: tuple[float, dict] | None = None
        self._restart_times: deque[float] = deque(maxlen=6)
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ utilities
    def log(self, level: str, msg: str) -> None:
        self.events.append({"ts": time.strftime("%H:%M:%S"), "level": level, "msg": msg})

    def _need_root(self) -> None:
        if hasattr(os, "geteuid") and os.geteuid() != 0 and not os.getenv("KEYLAND_ALLOW_NONROOT"):
            raise RouterError("Root privileges required (run the service as root).")

    def _sysctl_set(self, path: str, value: str) -> None:
        p = Path("/proc/sys") / path
        try:
            if path not in self._saved_sysctl:
                self._saved_sysctl[path] = p.read_text().strip()
            p.write_text(value)
        except OSError as exc:
            self.log("warn", f"sysctl {path}: {exc}")

    def _restore_sysctl(self) -> None:
        for path, value in self._saved_sysctl.items():
            try:
                (Path("/proc/sys") / path).write_text(value)
            except OSError:
                pass
        self._saved_sysctl.clear()

    def _tail(self, name: str, lines: int = 15) -> str:
        try:
            text = (self.run_dir / f"{name}.log").read_text(errors="replace")
        except OSError:
            return ""
        return "\n".join(text.splitlines()[-lines:])

    # ------------------------------------------------------------ preflight
    def preflight(self) -> dict:
        cfg = self.cfg
        checks = []

        def add(name: str, ok: bool, detail: str = "") -> None:
            checks.append({"name": name, "ok": ok, "detail": detail})

        is_root = not hasattr(os, "geteuid") or os.geteuid() == 0 or bool(os.getenv("KEYLAND_ALLOW_NONROOT"))
        add("root", is_root, "" if is_root else "service must run as root")
        for tool in REQUIRED_TOOLS:
            add(f"tool:{tool}", shutil.which(tool) is not None)
        add("hostapd_cli", shutil.which("hostapd_cli") is not None, "needed for kick/block")
        add("passphrase", bool(cfg.passphrase), "" if cfg.passphrase else "set a Wi-Fi password")
        add("client_iface", iface_exists(self._run, cfg.wifi_wan_iface), cfg.wifi_wan_iface)
        conflicts = subnet_conflicts(cfg)
        add("subnet_free", not conflicts, ", ".join(conflicts) or cfg.subnet)
        phy = self._run(["iw", "list"], timeout=20)
        if phy.ok:
            modes = parse_supported_modes(phy.out)
            combos = parse_interface_combinations(phy.out)
            add("ap_mode", "AP" in modes, "driver supports AP" if "AP" in modes else "driver has no AP mode")
            if combos["reported"]:
                ok = combos["ap_client_combination_mentioned"]
                detail = ("managed + AP, max channels: %s" % combos["max_channels_ap_client"]) if ok \
                    else "driver does not list managed + AP together"
                add("ap_plus_client", ok, detail)
        sim = cfg.sim_wan_iface or detect_sim_iface(self._run)
        add("sim_iface", bool(sim), sim or "SIM data interface not found (SIM WAN unavailable)")
        return {"ok": all(c["ok"] for c in checks if c["name"] not in ("sim_iface", "hostapd_cli")), "checks": checks}

    # ------------------------------------------------------------ WAN refresh
    def _sim_iface(self) -> str:
        if self.cfg.sim_wan_iface:
            return self.cfg.sim_wan_iface
        if not self._sim_iface_cache or not iface_exists(self._run, self._sim_iface_cache):
            self._sim_iface_cache = detect_sim_iface(self._run)
        return self._sim_iface_cache or ""

    def _refresh_wans(self) -> None:
        cfg = self.cfg
        spec = {"wifi": (cfg.wifi_wan_iface, cfg.weight_wifi), "sim": (self._sim_iface(), cfg.weight_sim)}
        for name, (iface, weight) in spec.items():
            ws = self.wans.get(name) or WanState(name=name)
            if ws.iface != iface:
                ws.healthy, ws.fails, ws.successes = None, 0, 0
            ws.iface, ws.weight = iface, weight
            ws.present = bool(iface) and iface_exists(self._run, iface)
            ws.ipv4 = iface_ipv4(self._run, iface) if ws.present else None
            ws.gateway = default_gateway(self._run, iface) if ws.ipv4 else None
            if not ws.ipv4:
                ws.healthy, ws.latency_ms = None, None
            self.wans[name] = ws
        r = self._run(["iw", "dev", cfg.wifi_wan_iface, "link"])
        self.uplink = parse_iw_link(r.out) if r.ok else {"connected": False}

    def _probe(self, iface: str, targets: list[str]) -> tuple[bool, float | None]:
        for target in targets:
            r = self._run(["ping", "-n", "-c", "1", "-W", "2", "-I", iface, target], timeout=6)
            if r.ok:
                return True, parse_ping_latency(r.out)
        return False, None

    def _probe_all(self) -> None:
        with self._lock:
            jobs = [(n, w.iface) for n, w in self.wans.items() if w.routable()]
            targets = list(self.cfg.probe_targets)
        results = {n: self._probe(i, targets) for n, i in jobs}   # outside the lock
        with self._lock:
            for n, (ok, lat) in results.items():
                record_probe(self.wans[n], ok, lat, self.cfg)

    def _reconcile_routes(self, force: bool = False) -> None:
        names = choose_active(self.cfg, self.wans)
        active = [self.wans[n] for n in names]
        sig = tuple((w.name, w.iface, w.gateway, w.weight) for w in active)
        if not force and sig == self._route_sig:
            return
        for cmd in build_route_cmds(self.cfg, active):
            r = self._run(cmd)
            if not r.ok:
                self.log("warn", f"route cmd failed: {' '.join(cmd[:6])}… {r.err}")
        self._ensure_rule()
        if names != self.active:
            self.log("info", f"Active WAN: {', '.join(names) or 'none'} (mode {self.cfg.wan_mode})")
        self.active, self._route_sig = names, sig

    def _ensure_rule(self) -> None:
        r = self._run(["ip", "rule", "show"])
        want = f"from {self.cfg.subnet} lookup {TABLE_ID}"
        if r.ok and want in r.out:
            return
        self._run(["ip", "rule", "add", "from", self.cfg.subnet, "lookup", str(TABLE_ID),
                   "priority", str(RULE_PRIORITY)])

    # ------------------------------------------------------------ AP / DHCP
    def _ap_channel(self) -> tuple[int, str]:
        cfg = self.cfg
        if cfg.follow_wan_channel:
            r = self._run(["iw", "dev", cfg.wifi_wan_iface, "info"])
            if r.ok:
                info = parse_iw_info(r.out)
                if info["channel"]:
                    return info["channel"], hw_mode_for(info["freq"])
        return cfg.channel, ("a" if cfg.channel > 14 else "g")

    def _ensure_ap_iface(self) -> None:
        cfg = self.cfg
        if iface_exists(self._run, cfg.ap_iface):
            return
        r = self._run(["iw", "dev", cfg.wifi_wan_iface, "interface", "add", cfg.ap_iface, "type", "__ap"])
        if not r.ok:
            raise RouterError(f"Cannot create {cfg.ap_iface}: {r.err or 'driver refused'}")
        self.log("info", f"Created virtual AP interface {cfg.ap_iface}")

    def _spawn(self, name: str, argv: list[str]) -> subprocess.Popen:
        logfile = open(self.run_dir / f"{name}.log", "wb")
        os.chmod(self.run_dir / f"{name}.log", 0o600)
        proc = subprocess.Popen(argv, stdout=logfile, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        logfile.close()
        return proc

    def _kill(self, name: str) -> None:
        proc = self.procs.pop(name, None)
        if not proc:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(2)

    def _ap_is_up(self) -> bool:
        r = self._run(["iw", "dev", self.cfg.ap_iface, "info"])
        if not r.ok:
            return False
        info = parse_iw_info(r.out)
        return info["type"] == "AP" and info["ssid"] == self.cfg.ssid

    def _start_hostapd(self) -> None:
        cfg = self.cfg
        channel, hw = self._ap_channel()
        conf = self.run_dir / "hostapd.conf"
        fd = os.open(conf, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(render_hostapd(cfg, channel, hw, str(self.ctrl_dir), str(self.deny_file)))
        self._write_deny_file()
        self._run(["nmcli", "device", "set", cfg.ap_iface, "managed", "no"])
        argv = ["hostapd", str(conf)]
        if shutil.which("stdbuf"):          # piped stdout is block-buffered by default
            argv = ["stdbuf", "-oL", "-eL"] + argv
        self.procs["hostapd"] = self._spawn("hostapd", argv)
        deadline = time.time() + 15
        while time.time() < deadline:
            proc = self.procs["hostapd"]
            if self._ap_is_up() or "AP-ENABLED" in self._tail("hostapd", 80):
                self.ap_channel = channel
                self.log("info", f"Hotspot '{cfg.ssid}' up on channel {channel}")
                return
            if proc.poll() is not None:
                break
            time.sleep(0.4)
        detail = self._tail("hostapd", 8)
        code = self.procs["hostapd"].poll()
        self._kill("hostapd")
        hint = ""
        if "channel" in detail.lower():
            hint = " (AP channel must equal the Wi-Fi WAN channel on one radio)"
        raise RouterError(
            "hostapd failed to start" + (f" (exit {code})" if code is not None else " (no AP after 15 s)")
            + ": " + (detail.replace("\n", " | ") or "no output") + hint)

    def _configure_ap_ip(self) -> None:
        cfg = self.cfg
        self._run(["ip", "addr", "flush", "dev", cfg.ap_iface])
        r = self._run(["ip", "addr", "add", f"{cfg.gateway_ip}/24", "dev", cfg.ap_iface])
        if not r.ok:
            raise RouterError(f"Cannot assign {cfg.gateway_ip} to {cfg.ap_iface}: {r.err}")
        self._run(["ip", "link", "set", cfg.ap_iface, "up"])

    def _start_dnsmasq(self) -> None:
        leases = str(self.run_dir / "dnsmasq.leases")
        pid = str(self.run_dir / "dnsmasq.pid")
        conf = self.run_dir / "dnsmasq.conf"
        for with_dns in (True, False):
            conf.write_text(render_dnsmasq(self.cfg, leases, pid, with_dns))
            os.chmod(conf, 0o600)
            self.procs["dnsmasq"] = self._spawn(
                "dnsmasq", ["dnsmasq", f"--conf-file={conf}", "--keep-in-foreground"])
            time.sleep(1.2)
            if self.procs["dnsmasq"].poll() is None:
                if not with_dns:
                    self.log("warn", "Port 53 busy: DHCP only, clients use external DNS")
                return
            self.log("warn", "dnsmasq exited: " + self._tail("dnsmasq", 3).replace("\n", " | "))
        raise RouterError("dnsmasq failed to start: " + self._tail("dnsmasq", 5))

    def _apply_nft(self) -> None:
        wans = [w.iface for w in self.wans.values() if w.iface]
        r = self._run(["nft", "-f", "-"], input=render_nft(self.cfg, wans))
        if not r.ok:
            raise RouterError(f"nftables rules failed: {r.err}")

    def _apply_kernel(self) -> None:
        self._sysctl_set("net/ipv4/ip_forward", "1")
        self._sysctl_set("net/ipv4/fib_multipath_hash_policy", "1")
        for w in self.wans.values():
            if w.iface:
                self._sysctl_set(f"net/ipv4/conf/{w.iface}/rp_filter", "2")

    # ------------------------------------------------------------ lifecycle
    def start(self) -> dict:
        with self._lock:
            if self.state in ("running", "starting"):
                return self.status()
            self._need_root()
            pre = self.preflight()
            failed = [c for c in pre["checks"] if not c["ok"] and c["name"] not in ("sim_iface", "hostapd_cli")]
            if failed:
                detail = "; ".join(f"{c['name']} {c['detail']}".strip() for c in failed)
                self.state, self.error = "error", f"Preflight failed: {detail}"
                raise RouterError(self.error)
            self.state, self.error = "starting", None
            self.log("info", "Starting router…")
            try:
                self._ensure_ap_iface()
                self._start_hostapd()
                self._configure_ap_ip()
                self._refresh_wans()
                self._apply_kernel()
                self._apply_nft()
                self._start_dnsmasq()
                self._reconcile_routes(force=True)
            except Exception as exc:
                self.log("error", str(exc))
                self._teardown()
                self.state, self.error = "error", str(exc)
                raise RouterError(str(exc)) from exc
            self.state, self.started_at = "running", time.time()
            self._stop_evt.clear()
            self._thread = threading.Thread(target=self._monitor, name="keyland-monitor", daemon=True)
            self._thread.start()
            return self.status()

    def _teardown(self) -> None:
        cfg = self.cfg
        for name in ("dnsmasq", "hostapd"):
            self._kill(name)
        self._run(["nft", "delete", "table", "inet", "keyland"])
        self._run(["nft", "delete", "table", "ip", "keyland_nat"])
        for _ in range(4):
            if not self._run(["ip", "rule", "del", "priority", str(RULE_PRIORITY)]).ok:
                break
        self._run(["ip", "route", "flush", "table", str(TABLE_ID)])
        self._run(["ip", "addr", "flush", "dev", cfg.ap_iface])
        self._run(["iw", "dev", cfg.ap_iface, "del"])
        self._restore_sysctl()
        self.active, self._route_sig, self.ap_channel = [], None, None

    def stop(self) -> dict:
        self._stop_evt.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=15)
        with self._lock:
            self._teardown()
            self.state, self.error, self.started_at = "stopped", None, None
            self.log("info", "Router stopped")
            return self.status()

    def restart(self) -> dict:
        self.stop()
        return self.start()

    def _restart_ap_stack(self, reason: str) -> None:
        """Restart hostapd+dnsmasq in place (channel change or crashed process)."""
        now = time.time()
        self._restart_times.append(now)
        if len(self._restart_times) >= 6 and now - self._restart_times[0] < 300:
            self.state, self.error = "error", f"Too many restarts ({reason})"
            self.log("error", self.error)
            self._teardown()
            self._stop_evt.set()
            return
        self.log("warn", f"Restarting hotspot: {reason}")
        try:
            for name in ("dnsmasq", "hostapd"):
                self._kill(name)
            self._ensure_ap_iface()
            self._start_hostapd()
            self._configure_ap_ip()
            self._start_dnsmasq()
            self._reconcile_routes(force=True)
        except RouterError as exc:
            self.log("error", f"Restart failed: {exc}")

    # ------------------------------------------------------------ monitor
    def _monitor(self) -> None:
        while not self._stop_evt.wait(self.cfg.probe_interval):
            try:
                with self._lock:
                    if self.state != "running":
                        break
                    self._refresh_wans()
                self._probe_all()
                with self._lock:
                    if self.state != "running":
                        break
                    self._reconcile_routes()
                    self._check_channel()
                    self._check_procs()
            except Exception as exc:  # keep the monitor alive
                self.log("error", f"monitor: {exc}")

    def _check_channel(self) -> None:
        if not self.cfg.follow_wan_channel or self.ap_channel is None:
            return
        r = self._run(["iw", "dev", self.cfg.wifi_wan_iface, "info"])
        channel = parse_iw_info(r.out)["channel"] if r.ok else None
        if channel and channel != self.ap_channel:
            self._channel_mismatch += 1
            if self._channel_mismatch >= 2:      # debounce: ignore a single glitch
                self._channel_mismatch = 0
                self._restart_ap_stack(f"Wi-Fi WAN moved to channel {channel}")
        else:
            self._channel_mismatch = 0

    def _check_procs(self) -> None:
        for name in ("hostapd", "dnsmasq"):
            proc = self.procs.get(name)
            if proc is None or proc.poll() is not None:
                self._restart_ap_stack(f"{name} stopped unexpectedly")
                return

    # ------------------------------------------------------------ public API
    def status(self) -> dict:
        with self._lock:
            cfg = self.cfg
            return {
                "state": self.state,
                "error": self.error,
                "uptime_s": int(time.time() - self.started_at) if self.started_at else None,
                "ap": {
                    "ssid": cfg.ssid, "iface": cfg.ap_iface, "gateway": cfg.gateway_ip,
                    "subnet": cfg.subnet, "channel": self.ap_channel,
                },
                "mode": cfg.wan_mode,
                "primary": cfg.primary,
                "active_wan": list(self.active),
                "uplink": dict(self.uplink),
                "wans": {n: w.public() for n, w in self.wans.items()},
                "events": list(self.events)[-30:],
            }

    def update_config(self, new_cfg: RouterConfig) -> None:
        with self._lock:
            save_config(new_cfg, self._config_path)
            self.cfg = new_cfg
            self._route_sig = None

    def set_wan_mode(self, mode: str, primary: str | None = None) -> dict:
        with self._lock:
            data = self.cfg.model_dump()
            data["wan_mode"] = mode
            if primary:
                data["primary"] = primary
            self.update_config(RouterConfig.model_validate(data))
            if self.state == "running":
                self._reconcile_routes(force=True)
            return self.status()

    def clients(self) -> list[dict]:  # connected stations; blocked list is in cfg
        cfg = self.cfg
        r = self._run(["iw", "dev", cfg.ap_iface, "station", "dump"])
        stations = parse_station_dump(r.out) if r.ok else []
        try:
            leases = parse_leases((self.run_dir / "dnsmasq.leases").read_text())
        except OSError:
            leases = {}
        for s in stations:
            lease = leases.get(s["mac"].lower(), {})
            s["ip"], s["hostname"] = lease.get("ip"), lease.get("hostname", "")
        return stations

    def logs(self, service: str, lines: int = 60) -> str:
        if service == "events":
            return "\n".join(f"{e['ts']} [{e['level']}] {e['msg']}" for e in self.events)
        if service not in ("hostapd", "dnsmasq"):
            raise RouterError("unknown log")
        return self._tail(service, min(max(lines, 1), 300))

    def wifi_connect(self, ssid: str, password: str | None) -> dict:
        if not 1 <= len(ssid.encode()) <= 32 or SSID_FORBIDDEN.search(ssid):
            raise RouterError("Invalid SSID")
        if password is not None and not (8 <= len(password) <= 63 or re.fullmatch(r"[0-9a-fA-F]{64}", password)):
            raise RouterError("Invalid password length")
        argv = ["nmcli", "device", "wifi", "connect", ssid]
        if password:
            argv += ["password", password]
        argv += ["ifname", self.cfg.wifi_wan_iface]
        r = self._run(argv, timeout=45)
        self.log("info" if r.ok else "warn", f"Wi-Fi WAN connect '{ssid}': {'ok' if r.ok else (r.err or 'failed')}")
        if not r.ok:
            raise RouterError(r.err or "Connection failed")
        return {"connected": True, "ssid": ssid}

    def sim_control(self, up: bool) -> dict:
        name = find_gsm_connection(self._run)
        if not name:
            raise RouterError("No SIM/GSM connection profile in NetworkManager")
        r = self._run(["nmcli", "connection", "up" if up else "down", name], timeout=45)
        self.log("info" if r.ok else "warn", f"SIM {'up' if up else 'down'}: {'ok' if r.ok else r.err}")
        if not r.ok:
            raise RouterError(r.err or "SIM command failed")
        return {"sim": "up" if up else "down", "connection": name}

    def sim_info(self) -> dict:
        now = time.time()
        if self._sim_cache and now - self._sim_cache[0] < 10:
            return self._sim_cache[1]
        info = sim_status(self._run)
        info["iface"] = self._sim_iface() or None
        self._sim_cache = (now, info)
        return info

    # --- client management (hostapd control socket) -----------------------
    def _write_deny_file(self) -> None:
        fd = os.open(self.deny_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write("".join(m + "\n" for m in self.cfg.blocked_macs))

    def _hostapd_cli(self, *args: str) -> bool:
        if self.state != "running" or not shutil.which("hostapd_cli"):
            return False
        return self._run(["hostapd_cli", "-p", str(self.ctrl_dir), "-i", self.cfg.ap_iface, *args]).ok

    @staticmethod
    def _mac(mac: str) -> str:
        mac = mac.strip().lower()
        if not MAC_RE.match(mac):
            raise RouterError("Invalid MAC address")
        return mac

    def kick_client(self, mac: str) -> dict:
        mac = self._mac(mac)
        done = self._hostapd_cli("deauthenticate", mac)
        self.log("info", f"Kick {mac}: {'ok' if done else 'not connected / router stopped'}")
        return {"kicked": done, "mac": mac}

    def block_client(self, mac: str) -> dict:
        mac = self._mac(mac)
        with self._lock:
            data = self.cfg.model_dump()
            if mac not in data["blocked_macs"]:
                data["blocked_macs"].append(mac)
            self.update_config(RouterConfig.model_validate(data))
            self._write_deny_file()
        self._hostapd_cli("deny_acl", "ADD_MAC", mac)
        self._hostapd_cli("deauthenticate", mac)
        self.log("info", f"Blocked {mac}")
        return {"blocked": True, "mac": mac, "blocked_macs": self.cfg.blocked_macs}

    def unblock_client(self, mac: str) -> dict:
        mac = self._mac(mac)
        with self._lock:
            data = self.cfg.model_dump()
            data["blocked_macs"] = [m for m in data["blocked_macs"] if m != mac]
            self.update_config(RouterConfig.model_validate(data))
            self._write_deny_file()
        self._hostapd_cli("deny_acl", "DEL_MAC", mac)
        self.log("info", f"Unblocked {mac}")
        return {"blocked": False, "mac": mac, "blocked_macs": self.cfg.blocked_macs}

    # --- Wi-Fi uplink (repeater source) ------------------------------------
    def wifi_scan(self, rescan: bool = True) -> dict:
        iface = self.cfg.wifi_wan_iface
        fields = "IN-USE,SSID,BSSID,CHAN,FREQ,SIGNAL,SECURITY"
        base = ["nmcli", "-t", "-f", fields, "device", "wifi", "list", "ifname", iface]
        r = self._run(base + ["--rescan", "yes" if rescan else "no"], timeout=30)
        if not r.ok and rescan:      # NetworkManager rate-limits rescans: use its cache
            r = self._run(base + ["--rescan", "no"], timeout=15)
        if not r.ok:
            raise RouterError(r.err or "Wi-Fi scan failed")
        return {
            "networks": parse_nmcli_wifi_list(r.out, list_saved_wifi(self._run)),
            "note": ("Scanning can briefly interrupt hotspot clients while the hotspot is running."
                     if self.state == "running" else None),
        }

    def autostart(self) -> None:
        """Boot helper: try a few times, since wlan0/modem may come up late."""
        def job() -> None:
            for _ in range(4):
                try:
                    self.start()
                    return
                except RouterError as exc:
                    self.log("warn", f"autostart: {exc}")
                    self.state = "stopped"
                    time.sleep(10)
        if self.cfg.start_on_boot and self.cfg.passphrase:
            threading.Thread(target=job, name="keyland-autostart", daemon=True).start()
