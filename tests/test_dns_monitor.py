from keyland.adapters.wifi.dns_monitor import (
    parse_dhcp_leases,
    parse_dns_query_log,
)


def test_parse_dns_query_log_maps_ip_to_dhcp_client():
    leases = parse_dhcp_leases(
        "1794150000 64:32:a8:b5:3e:ed 192.168.88.101 phone *\n"
    )
    events = parse_dns_query_log(
        "Nov  8 19:17:26 dnsmasq[123]: query[A] Example.COM. from 192.168.88.101\n"
        "Nov  8 19:17:27 dnsmasq[123]: query[AAAA] api.example.com from 192.168.88.101\n",
        leases,
    )
    assert len(events) == 2
    assert events[0]["domain"] == "example.com"
    assert events[0]["query_type"] == "A"
    assert events[0]["mac"] == "64:32:a8:b5:3e:ed"
    assert events[0]["hostname"] == "phone"
    assert events[1]["query_type"] == "AAAA"


def test_parse_dns_query_log_keeps_unknown_clients_without_guessing_identity():
    events = parse_dns_query_log(
        "Nov  8 19:17:26 dnsmasq[123]: query[HTTPS] example.org from 192.168.88.222\n"
    )
    assert events == [{
        "timestamp": "Nov  8 19:17:26",
        "query_type": "HTTPS",
        "domain": "example.org",
        "ip": "192.168.88.222",
        "mac": None,
        "hostname": None,
    }]


def test_parse_dns_query_log_ignores_non_query_and_malformed_lines():
    events = parse_dns_query_log(
        "Nov  8 19:17:26 dnsmasq[123]: forwarded example.com to 1.1.1.1\n"
        "Nov  8 19:17:27 dnsmasq[123]: query[A] bad_domain from 192.168.88.101\n"
        "not a dnsmasq log line\n"
    )
    assert events == []


def test_parse_dns_query_log_returns_newest_bounded_events():
    lines = "\n".join(
        f"Nov  8 19:17:{n:02d} dnsmasq[123]: query[A] host{n}.example from 192.168.88.101"
        for n in range(1, 6)
    )
    events = parse_dns_query_log(lines, limit=2)
    assert [event["domain"] for event in events] == [
        "host4.example", "host5.example"
    ]


def test_parse_dns_query_log_rejects_invalid_limit():
    import pytest

    with pytest.raises(ValueError):
        parse_dns_query_log("", limit=0)


def test_parse_dhcp_leases_ignores_invalid_entries():
    leases = parse_dhcp_leases(
        "1794150000 64:32:a8:b5:3e:ed 192.168.88.101 phone *\n"
        "bad-mac 192.168.88.102 invalid *\n"
        "1794150000 ff:ff:ff:ff:ff:ff not-an-ip * *\n"
    )
    assert list(leases) == ["192.168.88.101"]
