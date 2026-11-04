from keyland.adapters.wifi.hotspot import _validate, start_hotspot


def test_hotspot_config_validation_accepts_wpa2():
    interface, ssid, dns, password = _validate({
        "interface": "ap0",
        "ssid": "Keyland-Home",
        "password": "StrongPassphrase123",
        "security": "wpa2-psk",
        "dns_servers": ["1.1.1.1", "2606:4700:4700::1111"],
    })
    assert interface == "ap0"
    assert ssid == "Keyland-Home"
    assert dns == ["1.1.1.1", "2606:4700:4700::1111"]
    assert password == "StrongPassphrase123"


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
