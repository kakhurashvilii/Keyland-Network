from keyland.adapters.wifi.hotspot import _validate, start_hotspot


def test_hotspot_config_validation_accepts_wpa2():
    from unittest.mock import patch
    from keyland.adapters.wifi import hotspot

    with patch.object(
        hotspot,
        "_run",
        return_value={"success": True, "output": "[]", "error": None},
    ):
        interface, ssid, dns, password, ipv4_cidr = _validate({
            "interface": "ap0",
            "ssid": "Keyland-Home",
            "password": "StrongPassphrase123",
            "security": "wpa2-psk",
            "dns_servers": ["1.1.1.1", "8.8.8.8"],
        })
    assert interface == "ap0"
    assert ssid == "Keyland-Home"
    assert dns == ["1.1.1.1", "8.8.8.8"]
    assert password == "StrongPassphrase123"
    assert ipv4_cidr == "192.168.88.1/24"


def test_hotspot_config_rejects_invalid_dns():
    try:
        _validate({
            "interface": "ap0",
            "ssid": "Keyland",
            "password": "StrongPassphrase123",
            "dns_servers": ["not-an-ip"],
        })
    except ValueError as exc:
        assert "DNS" in str(exc)
    else:
        raise AssertionError("Invalid DNS address should be rejected")


def test_hotspot_requires_explicit_confirmation_before_commands():
    result = start_hotspot({
        "interface": "ap0",
        "ssid": "Keyland",
        "password": "StrongPassphrase123",
        "dns_servers": [],
        "confirmed": False,
    })
    assert result["success"] is False
    assert "confirmation" in result["error"].lower()


def test_hotspot_rejects_ipv6_dns_until_client_dns_is_supported():
    try:
        _validate({
            "interface": "ap0",
            "ssid": "Keyland",
            "password": "StrongPassphrase123",
            "dns_servers": ["2606:4700:4700::1111"],
        })
    except ValueError as exc:
        assert "IPv4" in str(exc)
    else:
        raise AssertionError("Hotspot DNS must reject unsupported IPv6 values")


def test_create_virtual_ap_sets_ap_mode_when_driver_creates_managed_interface():
    from unittest.mock import patch
    from keyland.adapters.wifi import hotspot

    interface_snapshots = [
        [],
        [{"name": "keyland-ap", "type": "managed", "phy": "phy#0"}],
        [{"name": "keyland-ap", "type": "AP", "phy": "phy#0"}],
    ]
    commands = []

    def fake_run(args, timeout=12):
        commands.append(args)
        return {"success": True, "output": "", "error": None}

    preflight = {
        "available": True,
        "ap_mode_supported": True,
        "interfaces": [{"name": "wlan0", "type": "managed", "ssid": "Cisco"}],
        "upstream_wifi_interfaces": [],
        "default_route_interfaces": ["wlan0"],
        "interface_combinations": {"ap_client_combination_mentioned": True},
    }

    with patch.object(hotspot.shutil, "which", return_value="/usr/bin/fake"), \
         patch.object(hotspot, "get_hotspot_capabilities", return_value=preflight), \
         patch.object(hotspot, "_wifi_interfaces", side_effect=interface_snapshots), \
         patch.object(hotspot, "_run", side_effect=fake_run):
        result = hotspot.create_virtual_ap("wlan0", True)

    assert result["success"] is True
    assert result["type"] == "AP"
    assert ["ip", "link", "set", "keyland-ap", "down"] in commands
    assert ["iw", "dev", "keyland-ap", "set", "type", "__ap"] in commands
