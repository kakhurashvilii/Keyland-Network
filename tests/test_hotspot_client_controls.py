import pytest

from keyland.adapters.wifi import router


@pytest.mark.parametrize("mac", [
    "64:32:a8:b5:3e:ed",
    "02:00:00:00:00:01",
])
def test_valid_unicast_client_mac(mac):
    assert router._valid_mac(mac) == mac


@pytest.mark.parametrize("mac", [
    "",
    "64:32:a8:b5:3e",
    "gg:32:a8:b5:3e:ed",
    "ff:ff:ff:ff:ff:ff",
    "01:00:5e:00:00:01",
])
def test_reject_invalid_or_multicast_mac(mac):
    with pytest.raises(ValueError):
        router._valid_mac(mac)


def test_client_block_requires_active_hotspot():
    from unittest.mock import patch

    with (
        patch.object(router, "read_state", return_value=None),
        patch.object(router, "status", return_value={"active": False}),
    ):
        result = router.client_block("64:32:a8:b5:3e:ed", True)

    assert result["success"] is False
    assert "not active" in result["error"].lower()


def test_client_limit_rejects_out_of_range_speed():
    from unittest.mock import patch

    state = {"interface": "keyland-ap"}
    with (
        patch.object(router, "read_state", return_value=state),
        patch.object(router, "status", return_value={"active": True}),
        patch.object(router, "_clients", return_value=[{"mac": "64:32:a8:b5:3e:ed"}]),
    ):
        result = router.client_limit("64:32:a8:b5:3e:ed", 0, 5)

    assert result["success"] is False
    assert "between 1 and 1000" in result["error"]

def test_station_dump_includes_radio_and_packet_details():
    from unittest.mock import patch

    sample = """Station 64:32:a8:b5:3e:ed (on keyland-ap)
        inactive time: 35 ms
        rx bytes: 1024
        rx packets: 8
        tx bytes: 2048
        tx packets: 12
        tx retries: 2
        tx failed: 1
        signal: -48 dBm
        signal avg: -50 dBm
        tx bitrate: 72.2 MBit/s
        rx bitrate: 65.0 MBit/s
        expected throughput: 65000.000Mbps
        authorized: yes
        authenticated: yes
        associated: yes
"""
    with patch.object(router, "run", return_value={"ok": True, "out": sample}):
        clients = router._clients("keyland-ap")

    assert len(clients) == 1
    client = clients[0]
    assert client["mac"] == "64:32:a8:b5:3e:ed"
    assert client["rx_bytes"] == 1024
    assert client["tx_packets"] == 12
    assert client["tx_retries"] == 2
    assert client["inactive_time"] == 35
    assert client["signal_avg"] == -50
    assert client["tx_bitrate"] == "72.2 MBit/s"
    assert client["rx_bitrate"] == "65.0 MBit/s"
    assert client["authorized"] == "yes"
