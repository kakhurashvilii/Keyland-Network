import json
from unittest.mock import patch

from keyland.adapters.wifi.repeater import _parse_iw_dev


def test_parse_iw_dev_reads_ap_and_managed_roles():
    sample = """phy#0
    Interface ap0
        type AP
        ssid KEYLAND-ROUTER
        channel 6 (2437 MHz), width: 20 MHz
    Interface wlan0
        type managed
        ssid DiagOnNet
        channel 6 (2437 MHz), width: 20 MHz
"""
    result = _parse_iw_dev(sample)
    assert len(result) == 2
    assert result[0]["type"] == "AP"
    assert result[0]["ssid"] == "KEYLAND-ROUTER"
    assert result[1]["type"] == "managed"
    assert result[0]["channel"] == result[1]["channel"] == 6


def test_parse_iw_dev_handles_no_interfaces():
    assert _parse_iw_dev("phy#0\n") == []
