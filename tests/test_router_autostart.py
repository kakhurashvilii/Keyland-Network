import json
from unittest.mock import patch

from keyland.adapters.wifi import router


def test_autostart_config_is_saved_root_only_and_status_hides_password(tmp_path):
    target = tmp_path / "router-autostart.json"
    config = {
        "mode": "hotspot",
        "interface": "keyland-ap",
        "upstream_interface": "qmapmux0.0",
        "source_interface": "wlan0",
        "ssid": "Keyland",
        "password": "PrivatePass123",
        "ipv4_cidr": "192.168.88.1/24",
        "dns_servers": ["1.1.1.1"],
        "band": "2.4",
        "channel": 6,
    }

    with patch.object(router, "AUTOSTART_FILE", target):
        router._write_autostart(config)
        assert target.exists()
        assert target.stat().st_mode & 0o777 == 0o600
        assert json.loads(target.read_text()) == config
        with patch.object(router, "status", return_value={"active": False}):
            public_status = router.autostart_status()

    assert public_status["enabled"] is True
    assert public_status["ssid"] == "Keyland"
    assert "password" not in public_status


def test_disable_autostart_removes_saved_configuration(tmp_path):
    target = tmp_path / "router-autostart.json"
    target.write_text('{"ssid":"Keyland"}')

    with patch.object(router, "AUTOSTART_FILE", target):
        result = router.disable_autostart()

    assert result == {"success": True, "enabled": False}
    assert not target.exists()


def test_restore_skips_when_no_persistent_configuration(tmp_path):
    target = tmp_path / "missing-router-autostart.json"

    with patch.object(router, "AUTOSTART_FILE", target):
        result = router.restore_autostart()

    assert result["success"] is True
    assert result["skipped"] is True

def test_restore_waits_for_selected_wan_without_preparing_ap(tmp_path):
    target = tmp_path / "router-autostart.json"
    target.write_text(json.dumps({
        "mode": "hotspot",
        "interface": "keyland-ap",
        "upstream_interface": "qmapmux0.0",
        "source_interface": "wlan0",
        "ssid": "Keyland",
        "password": "PrivatePass123",
        "ipv4_cidr": "192.168.88.1/24",
        "dns_servers": ["1.1.1.1"],
        "band": "2.4",
        "channel": 6,
    }))

    with (
        patch.object(router, "AUTOSTART_FILE", target),
        patch.object(router, "status", return_value={"active": False}),
        patch.object(router, "defaults", return_value=[]),
        patch.object(router, "prepare_ap") as prepare,
    ):
        result = router.restore_autostart()

    assert result["success"] is False
    assert result["waiting"] is True
    prepare.assert_not_called()

def test_enable_autostart_saves_live_config_from_root_only_hostapd_file(tmp_path):
    target = tmp_path / "router-autostart.json"
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "hostapd.conf").write_text(
        "interface=keyland-ap\nssid=Keyland\nwpa_passphrase=PrivatePass123\n"
    )
    state = {
        "mode": "hotspot",
        "interface": "keyland-ap",
        "upstream_interface": "qmapmux0.0",
        "source_interface": "wlan0",
        "ssid": "Keyland",
        "ipv4_cidr": "192.168.88.1/24",
        "dns_servers": ["1.1.1.1"],
        "band": "2.4",
        "channel": 6,
    }

    with (
        patch.object(router, "AUTOSTART_FILE", target),
        patch.object(router, "ROOT", runtime),
        patch.object(router, "read_state", return_value=state),
        patch.object(router, "status", return_value={"active": True}),
    ):
        result = router.enable_autostart()

    saved = json.loads(target.read_text())
    assert result == {"success": True, "enabled": True}
    assert saved["password"] == "PrivatePass123"
    assert saved["source_interface"] == "wlan0"
    assert target.stat().st_mode & 0o777 == 0o600
