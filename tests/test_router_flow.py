"""End-to-end start/failover/stop simulation with a fake command runner
(real device behaviour of the OnePlus 6T / ath10k_snoc is replayed from captured output)."""
import json
import os

os.environ["KEYLAND_ALLOW_NONROOT"] = "1"

import pytest

from keyland.adapters.wifi.capabilities import parse_interface_combinations, parse_supported_modes
from keyland.router import controller as ctl_mod
from keyland.router.config import RouterConfig
from keyland.router.system import CmdResult

# Captured from the OnePlus 6T (Kali NetHunter Pro, ath10k_snoc)
IW_LIST = """Wiphy phy0
	Supported interface modes:
		 * IBSS
		 * managed
		 * AP
		 * P2P-client
		 * P2P-GO
		 * P2P-device
	valid interface combinations:
		 * #{ managed } <= 2, #{ AP, P2P-client, P2P-GO } <= 2, #{ P2P-device } <= 1,
		   total <= 4, #channels <= 1
		 * #{ managed } <= 2, #{ P2P-client } <= 2, #{ AP, P2P-GO } <= 1, #{ P2P-device } <= 1,
		   total <= 4, #channels <= 2
		 * #{ managed } <= 1, #{ IBSS } <= 1,
		   total <= 2, #channels <= 1
	HT Capability overrides:
		 * MCS: ff ff ff ff ff ff ff ff ff ff
"""


def test_real_device_combinations_detected():
    data = parse_interface_combinations(IW_LIST)
    assert data["ap_client_combination_mentioned"] is True
    assert len(data["combinations"]) == 3
    assert [c["simultaneous_ap_client"] for c in data["combinations"]] == [True, True, False]
    assert data["combinations"][0]["max_channels"] == 1
    assert data["max_channels_ap_client"] == 2
    assert "AP" in parse_supported_modes(IW_LIST)


def test_no_false_positive_when_roles_are_in_different_combinations():
    text = ("valid interface combinations:\n * #{ managed } <= 1,\n   total <= 1, #channels <= 1\n"
            " * #{ AP } <= 1,\n   total <= 1, #channels <= 1\n")
    assert parse_interface_combinations(text)["ap_client_combination_mentioned"] is False


class FakeProc:
    def poll(self): return None
    def terminate(self): pass
    def wait(self, t=None): return 0
    def kill(self): pass


class Device:
    """Replays command output of a phone with wifi uplink + SIM."""
    def __init__(self):
        self.calls, self.stdin = [], {}
        self.wifi_ping_ok = True
        self.ap_exists = False

    def __call__(self, args, timeout=10, input=None):
        a = list(args); self.calls.append(a)
        if input: self.stdin[tuple(a[:2])] = input
        key = " ".join(a)
        ok = lambda out="": CmdResult(True, out, "")
        if a[:3] == ["iw", "dev", "wlan0"] and a[3] == "info":
            return ok("Interface wlan0\n\ttype managed\n\tssid DiagOnNet\n\tchannel 6 (2437 MHz), width: 20 MHz")
        if a[:3] == ["iw", "dev", "ap0"] and a[3] == "info":
            return ok("Interface ap0\n\ttype AP\n\tssid Keyland-Router\n\tchannel 6 (2437 MHz)") if self.ap_exists else CmdResult(False, "", "nodev")
        if a[:2] == ["iw", "list"]: return ok(IW_LIST)
        if "interface add ap0" in key:
            self.ap_exists = True; return ok()
        if a[:4] == ["ip", "link", "show", "dev"]:
            return ok() if a[4] in ("wlan0", "qmapmux0.0") or (a[4] == "ap0" and self.ap_exists) else CmdResult(False, "", "")
        if a[:2] == ["mmcli", "-b"]: return CmdResult(False, "", "no bearer")
        if a[:3] == ["ip", "-j", "-o"]: return ok(json.dumps([{"ifname": "lo"}, {"ifname": "wlan0"}, {"ifname": "qmapmux0.0"}]))
        if a[:4] == ["ip", "-j", "-4", "addr"]:
            ip = {"wlan0": "192.168.0.24", "qmapmux0.0": "10.2.148.15"}.get(a[-1])
            return ok(json.dumps([{"addr_info": [{"local": ip}]}])) if ip else ok("[]")
        if a[:4] == ["ip", "-j", "-4", "route"]:
            return ok(json.dumps([{"dst": "default", "gateway": "192.168.0.1"}])) if a[-1] == "wlan0" else ok("[]")
        if a[0] == "ping":
            iface = a[a.index("-I") + 1]
            good = self.wifi_ping_ok if iface == "wlan0" else True
            return CmdResult(good, "64 bytes: time=11.5 ms" if good else "", "")
        if a[:3] == ["ip", "rule", "show"]: return ok("")
        return ok()


@pytest.fixture
def router(tmp_path, monkeypatch):
    monkeypatch.setattr(ctl_mod.shutil, "which", lambda name: "/usr/bin/" + name)
    dev = Device()
    c = ctl_mod.RouterController(config_path=tmp_path / "c.json", run=dev, run_dir=tmp_path)
    monkeypatch.setattr(c, "_spawn", lambda name, argv: FakeProc())
    monkeypatch.setattr(ctl_mod.time, "sleep", lambda s: None)
    cfg = RouterConfig(passphrase="supersecret1", ssid="Keyland-Router", probe_interval=60)
    c.update_config(cfg)
    c.dev = dev
    yield c
    c.stop()


def test_start_failover_and_stop(router):
    pre = router.preflight()
    assert pre["ok"], pre
    assert any(x["name"] == "ap_plus_client" and x["ok"] for x in pre["checks"])

    st = router.start()
    assert st["state"] == "running" and st["ap"]["channel"] == 6      # AP follows wlan0 channel
    assert st["active_wan"] == ["wifi"]
    assert st["wans"]["sim"]["iface"] == "qmapmux0.0"

    # one nft transaction, own tables only
    ruleset = router.dev.stdin[("nft", "-f")]
    assert "keyland_nat" in ruleset and "flush ruleset" not in ruleset
    assert ["ip", "route", "replace", "default", "via", "192.168.0.1", "dev", "wlan0", "table", "100"] in router.dev.calls
    assert any(c[:3] == ["ip", "rule", "add"] for c in router.dev.calls)

    # Wi-Fi uplink dies -> after fail_threshold probes the SIM carries the hotspot
    router.dev.wifi_ping_ok = False
    for _ in range(router.cfg.fail_threshold):
        router._probe_all()
    router._reconcile_routes()
    assert router.status()["active_wan"] == ["sim"]
    assert ["ip", "route", "replace", "default", "dev", "qmapmux0.0", "table", "100"] in router.dev.calls

    # Wi-Fi returns -> back to primary after recover_threshold
    router.dev.wifi_ping_ok = True
    for _ in range(router.cfg.recover_threshold):
        router._probe_all()
    router._reconcile_routes()
    assert router.status()["active_wan"] == ["wifi"]

    router.stop()
    assert router.status()["state"] == "stopped"
    flat = [" ".join(c) for c in router.dev.calls]
    assert "nft delete table inet keyland" in flat and "nft delete table ip keyland_nat" in flat
    assert "ip route flush table 100" in flat


def test_channel_follow_restarts_ap(router):
    router.start()
    router.dev_channel = None
    orig = router._run

    def moved(args, timeout=10, input=None):
        if list(args)[:4] == ["iw", "dev", "wlan0", "info"]:
            return CmdResult(True, "Interface wlan0\n\ttype managed\n\tchannel 11 (2462 MHz)", "")
        return orig(args, timeout=timeout, input=input)
    router._run = moved
    router._check_channel()            # 1st sighting: debounced
    assert router.ap_channel == 6
    router._check_channel()            # 2nd sighting: AP restarts on channel 11
    assert router.ap_channel == 11


def test_wifi_scan_and_uplink_status(router):
    orig = router._run

    def run(args, timeout=10, input=None):
        a = list(args)
        if a[:3] == ["nmcli", "-t", "-f"] and "wifi" in a and "list" in a:
            return CmdResult(True, "*:DiagOnNet:EE\\:F1\\:81\\:F0\\:45\\:E1:6:2437 MHz:80:WPA2\n :Cafe:AA\\:BB\\:CC\\:DD\\:EE\\:FF:36:5180 MHz:55:--", "")
        if a[:3] == ["nmcli", "-t", "-f"] and "connection" in a:
            return CmdResult(True, "DiagOnNet:802-11-wireless\nvpn:vpn", "")
        if a[:4] == ["iw", "dev", "wlan0", "link"]:
            return CmdResult(True, "Connected to ee:f1:81:f0:45:e1 (on wlan0)\n\tSSID: DiagOnNet\n\tfreq: 2437.0\n\tsignal: -23 dBm", "")
        return orig(args, timeout=timeout, input=input)
    router._run = run
    nets = router.wifi_scan()["networks"]
    assert [n["ssid"] for n in nets] == ["DiagOnNet", "Cafe"]
    assert nets[0]["in_use"] and nets[0]["saved"] and not nets[1]["secured"]
    router.start()
    up = router.status()["uplink"]
    assert up["ssid"] == "DiagOnNet" and up["channel"] == 6 and up["signal_dbm"] == -23
