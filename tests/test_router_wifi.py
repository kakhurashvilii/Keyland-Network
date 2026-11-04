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
    return (
        patch.object(router, "run", return_value={"ok": True, "out": addr_output, "err": None}),
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
