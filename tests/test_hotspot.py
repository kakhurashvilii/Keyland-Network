from keyland.adapters.wifi.hotspot import _validate, start_hotspot


def test_hotspot_config_validation_accepts_wpa2():
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
