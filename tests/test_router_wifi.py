import json
from unittest.mock import patch

import pytest

from keyland.adapters.wifi import router


def base_payload(**overrides):
    payload = {
        "mode": "hotspot",
        "interface": "keyland-ap",
        "upstream_interface": "qmapmux0.0",
        "ssid": "Keyland-Test",
        "password": "SafePass123",
        "ipv4_cidr": "192.168.88.1/24",
        "dns_servers": ["1.1.1.1", "8.8.8.8"],
    }
    payload.update(overrides)
    return payload


def patch_preflight(addr_output="[]", interfaces=None, routes=None):
    def fake_run(args, **kwargs):
        if args[:2] == ["iw", "list"]:
            return {"ok": True, "out": """
        valid interface combinations:
                 * #{ managed } <= 2, #{ AP, P2P-client, P2P-GO } <= 2, total <= 4, #channels <= 2
        * 2412.0 MHz [1] (20.0 dBm)
        * 2437.0 MHz [6] (20.0 dBm)
        * 5180.0 MHz [36] (18.0 dBm)
        * 5200.0 MHz [40] (18.0 dBm)
        * 5220.0 MHz [44] (18.0 dBm)
        * 5240.0 MHz [48] (18.0 dBm)
        * 5260.0 MHz [52] (18.0 dBm) (no IR, radar detection)
        * 5500.0 MHz [100] (disabled)
""", "err": None}
        return {"ok": True, "out": addr_output, "err": None}
    return (
        patch.object(router, "run", side_effect=fake_run),
        patch.object(router, "interfaces", return_value=interfaces or [
            {"name": "keyland-ap", "type": "AP", "phy": "phy#0", "channel": 6},
            {"name": "wlan0", "type": "managed", "ssid": "DiagOnNet", "channel": 6, "phy": "phy#0"},
        ]),
        patch.object(router, "defaults", return_value=routes or [
            {"dev": "qmapmux0.0", "gateway": "10.2.148.16"},
            {"dev": "wlan0", "gateway": "10.236.189.69"},
        ]),
        patch.object(router.shutil, "which", return_value="/usr/sbin/tool"),
    )


def test_validate_selects_cellular_wan_without_touching_wifi():
    patches = patch_preflight()
    with patches[0], patches[1], patches[2], patches[3]:
        result = router._validate(base_payload())
    assert result["upstream_interface"] == "qmapmux0.0"
    assert result["interface"] == "keyland-ap"
    assert result["channel"] == 6


def test_validate_rejects_non_ascii_hostapd_passphrase():
    patches = patch_preflight()
    with patches[0], patches[1], patches[2], patches[3]:
        with pytest.raises(ValueError, match="ASCII"):
            router._validate(base_payload(password="პაროლი123"))


def test_validate_rejects_overlapping_lan_subnet():
    overlap = json.dumps([{
        "ifname": "wlan0",
        "addr_info": [{"family": "inet", "local": "192.168.88.10", "prefixlen": 24}],
    }])
    patches = patch_preflight(addr_output=overlap)
    with patches[0], patches[1], patches[2], patches[3]:
        with pytest.raises(ValueError, match="overlaps"):
            router._validate(base_payload())


def test_repeater_requires_connected_wifi_upstream():
    patches = patch_preflight()
    with patches[0], patches[1], patches[2], patches[3]:
        with pytest.raises(ValueError, match="connected Wi-Fi"):
            router._validate(base_payload(mode="repeater", upstream_interface="qmapmux0.0"))


def test_validate_accepts_5ghz_non_dfs_channel():
    patches = patch_preflight()
    with patches[0], patches[1], patches[2], patches[3]:
        result = router._validate(base_payload(band="5", channel=36))
    assert result["band"] == "5"
    assert result["channel"] == 36


def test_validate_rejects_disabled_or_dfs_5ghz_channel():
    patches = patch_preflight()
    with patches[0], patches[1], patches[2], patches[3]:
        with pytest.raises(ValueError, match="supported non-DFS channel"):
            router._validate(base_payload(band="5", channel=52))


def test_repeater_must_follow_upstream_channel():
    patches = patch_preflight()
    with patches[0], patches[1], patches[2], patches[3]:
        with pytest.raises(ValueError, match="Repeater AP must use"):
            router._validate(base_payload(mode="repeater", upstream_interface="wlan0", band="5", channel=36))


def test_prepare_ap_converts_only_dedicated_interface():
    initial = [
        {"name": "wlan0", "type": "managed", "phy": "phy#0",
         "ssid": "DiagOnNet", "channel": 11},
        {"name": "keyland-ap", "type": "managed", "phy": "phy#0",
         "ssid": None, "channel": None},
    ]

    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[:2] == ["iw", "list"]:
            return {
                "ok": True,
                "out": """
Supported interface modes:
        * managed
        * AP
valid interface combinations:
        * #{ managed } <= 2, #{ AP } <= 1, total <= 3, #channels <= 2
""",
                "err": None,
            }
        return {"ok": True, "out": "", "err": None}

    states = [
        initial,
        [
            {"name": "wlan0", "type": "managed", "phy": "phy#0",
             "ssid": "DiagOnNet", "channel": 11},
            {"name": "keyland-ap", "type": "managed", "phy": "phy#0",
             "ssid": None, "channel": None},
        ],
        [
            {"name": "wlan0", "type": "managed", "phy": "phy#0",
             "ssid": "DiagOnNet", "channel": 11},
            {"name": "keyland-ap", "type": "AP", "phy": "phy#0",
             "ssid": None, "channel": None},
        ],
    ]

    with (
        patch.object(router, "interfaces", side_effect=lambda: states.pop(0)),
        patch.object(router, "defaults", return_value=[
            {"dev": "wlan0", "gateway": "10.0.0.1"}
        ]),
        patch.object(router, "run", side_effect=fake_run),
        patch.object(router, "read_state", return_value=None),
    ):
        result = router.prepare_ap({
            "interface": "wlan0",
            "ap_interface": "keyland-ap",
            "confirmed": True,
        })

    assert result["success"] is True
    assert result["type"] == "AP"
    assert ["iw", "dev", "keyland-ap", "set", "type", "__ap"] in calls
    assert not any(
        "wlan0" in command and (
            "set" in command or "down" in command or "del" in command
        )
        for command in calls
    )


def test_prepare_ap_requires_confirmation():
    result = router.prepare_ap({
        "interface": "wlan0",
        "ap_interface": "keyland-ap",
        "confirmed": False,
    })
    assert result["success"] is False
    assert "confirmation" in result["error"].lower()


def test_prepare_ap_rejects_source_equal_to_ap():
    result = router.prepare_ap({
        "interface": "wlan0",
        "ap_interface": "wlan0",
        "confirmed": True,
    })
    assert result["success"] is False
    assert "Invalid" in result["error"]
