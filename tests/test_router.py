import os
import tempfile
from pathlib import Path

_d = tempfile.TemporaryDirectory()
os.environ.setdefault("KEYLAND_DB_PATH", str(Path(_d.name) / "t.db"))
os.environ.setdefault("KEYLAND_JWT_SECRET", "test-only-secret-key-never-use-in-production-123456")
os.environ["KEYLAND_ROUTER_CONFIG"] = str(Path(_d.name) / "router.json")
os.environ["KEYLAND_RUN_DIR"] = str(Path(_d.name) / "run")

import pytest
from pydantic import ValidationError

from keyland.router.config import RouterConfig, load_config, save_config
from keyland.router.render import render_hostapd, render_dnsmasq, render_nft
from keyland.router.system import (
    CmdResult, parse_iw_info, parse_station_dump, parse_leases, parse_ping_latency,
)
from keyland.router.wan import WanState, choose_active, record_probe, build_route_cmds


def cfg(**kw):
    return RouterConfig(passphrase="supersecret1", **kw)


def wans(wifi_ok=True, sim_ok=True):
    return {
        "wifi": WanState("wifi", "wlan0", True, "192.168.0.24", "192.168.0.1", wifi_ok, weight=1),
        "sim": WanState("sim", "qmapmux0.0", True, "10.2.148.15", None, sim_ok, weight=1),
    }


def test_config_validation():
    with pytest.raises(ValidationError):
        RouterConfig(passphrase="short")
    with pytest.raises(ValidationError):
        RouterConfig(subnet="8.8.8.0/24")
    with pytest.raises(ValidationError):
        RouterConfig(ap_iface="a b; rm")
    with pytest.raises(ValidationError):
        RouterConfig(dns_servers=["1.1.1.1; reboot"])
    with pytest.raises(ValidationError):
        RouterConfig(ssid="x" * 33)
    assert cfg().gateway_ip == "192.168.50.1"
    assert cfg().dhcp_range() == ("192.168.50.10", "192.168.50.200")


def test_config_roundtrip_and_public_hides_passphrase(tmp_path):
    p = tmp_path / "r.json"
    save_config(cfg(ssid="Georgia ქსელი"), p)
    assert oct(p.stat().st_mode)[-3:] == "600"
    loaded = load_config(p)
    assert loaded.ssid == "Georgia ქსელი"
    pub = loaded.public_dict()
    assert "passphrase" not in pub and pub["passphrase_set"] is True


def test_render_hostapd_and_dnsmasq_and_nft():
    c = cfg()
    h = render_hostapd(c, 6, "g")
    assert "interface=ap0" in h and "channel=6" in h and "wpa_passphrase=supersecret1" in h
    with pytest.raises(ValueError):
        render_hostapd(RouterConfig(), 6, "g")
    d = render_dnsmasq(c, "/l", "/p", True)
    assert "dhcp-range=192.168.50.10,192.168.50.200" in d and "server=1.1.1.1" in d
    assert "port=0" in render_dnsmasq(c, "/l", "/p", False)
    n = render_nft(c, ["wlan0", "qmapmux0.0"])
    assert 'oifname { "wlan0", "qmapmux0.0" } masquerade' in n
    assert "dport 8000 drop" in n
    assert "dport" not in render_nft(cfg(panel_on_wan=True), ["wlan0"])


def test_parsers():
    info = parse_iw_info("Interface wlan0\n\ttype managed\n\tssid X\n\tchannel 11 (2462 MHz), width: 20 MHz\n")
    assert info["channel"] == 11 and info["freq"] == 2462 and info["ssid"] == "X"
    st = parse_station_dump("Station aa:bb:cc:dd:ee:ff (on ap0)\n\tsignal:  -40 dBm\n\tconnected time:\t120 seconds\n")
    assert st == [{"mac": "aa:bb:cc:dd:ee:ff", "signal_dbm": -40, "connected_s": 120}]
    assert parse_leases("1700 aa:bb:cc:dd:ee:ff 192.168.50.20 phone *")["aa:bb:cc:dd:ee:ff"]["ip"] == "192.168.50.20"
    assert parse_ping_latency("64 bytes from 1.1.1.1: time=12.3 ms") == 12.3


def test_hysteresis():
    c = cfg(fail_threshold=3, recover_threshold=2)
    w = WanState("wifi")
    record_probe(w, True, 5, c); assert w.healthy is True
    record_probe(w, False, None, c); record_probe(w, False, None, c)
    assert w.healthy is True            # 2 failures: still up
    record_probe(w, False, None, c); assert w.healthy is False
    record_probe(w, True, 5, c); assert w.healthy is False
    record_probe(w, True, 5, c); assert w.healthy is True


def test_failover_selection():
    assert choose_active(cfg(), wans()) == ["wifi"]
    assert choose_active(cfg(), wans(wifi_ok=False)) == ["sim"]
    assert choose_active(cfg(primary="sim"), wans()) == ["sim"]
    assert choose_active(cfg(), wans(False, False)) == ["wifi"]       # keep primary, probes may recover
    assert choose_active(cfg(wan_mode="sim_only"), wans()) == ["sim"]
    assert choose_active(cfg(wan_mode="balance"), wans()) == ["wifi", "sim"]
    assert choose_active(cfg(wan_mode="balance"), wans(wifi_ok=False)) == ["sim"]
    w = wans(); w["sim"].present = False
    assert choose_active(cfg(wan_mode="sim_only"), w) == []


def test_route_cmds():
    w = wans()
    single = build_route_cmds(cfg(), [w["wifi"]])
    assert single[1] == ["ip", "route", "replace", "default", "via", "192.168.0.1", "dev", "wlan0", "table", "100"]
    multi = build_route_cmds(cfg(), [w["wifi"], w["sim"]])[1]
    assert multi.count("nexthop") == 2 and "qmapmux0.0" in multi
    assert "unreachable" in build_route_cmds(cfg(), [])[1]
    assert single[0][3] == "192.168.50.0/24"      # connected route inside table: replies reach clients


class FakeRun:
    def __init__(self):
        self.calls = []

    def __call__(self, args, timeout=10, input=None):
        self.calls.append(list(args))
        return CmdResult(True, "", "")


def test_controller_wan_mode_and_preflight(tmp_path):
    from keyland.router.controller import RouterController
    fake = FakeRun()
    c = RouterController(config_path=tmp_path / "c.json", run=fake, run_dir=tmp_path)
    out = c.set_wan_mode("balance", "sim")
    assert out["mode"] == "balance" and out["primary"] == "sim"
    assert load_config(tmp_path / "c.json").wan_mode == "balance"
    assert c.preflight()["ok"] is False            # no passphrase yet
    with pytest.raises(Exception):
        c.wifi_connect("bad\nssid", None)
    c.wifi_connect("Home", "password123")
    assert fake.calls[-1][:5] == ["nmcli", "device", "wifi", "connect", "Home"]


def test_hostapd_extras_and_block_validation(tmp_path):
    c = cfg(client_isolation=True, blocked_macs=["AA:BB:CC:DD:EE:FF", "aa:bb:cc:dd:ee:ff"])
    assert c.blocked_macs == ["aa:bb:cc:dd:ee:ff"]
    h = render_hostapd(c, 6, "g", "/run/k/ctl", "/run/k/deny")
    assert "ap_isolate=1" in h and "ctrl_interface=/run/k/ctl" in h and "deny_mac_file=/run/k/deny" in h
    with pytest.raises(ValidationError):
        cfg(blocked_macs=["not-a-mac"])


def test_block_unblock_roundtrip(tmp_path):
    from keyland.router.controller import RouterController
    c = RouterController(config_path=tmp_path / "c.json", run=FakeRun(), run_dir=tmp_path)
    out = c.block_client("AA:BB:CC:DD:EE:FF")
    assert out["blocked_macs"] == ["aa:bb:cc:dd:ee:ff"]
    assert "aa:bb:cc:dd:ee:ff" in (tmp_path / "hostapd.deny").read_text()
    assert c.unblock_client("aa:bb:cc:dd:ee:ff")["blocked_macs"] == []
    with pytest.raises(Exception):
        c.block_client("zz")
