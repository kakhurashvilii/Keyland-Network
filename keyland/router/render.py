"""Pure functions that render hostapd / dnsmasq / nftables configuration."""
from __future__ import annotations

from .config import RouterConfig


def hw_mode_for(freq_mhz: int | None) -> str:
    return "a" if freq_mhz and freq_mhz >= 5000 else "g"


def render_hostapd(cfg: RouterConfig, channel: int, hw_mode: str,
                   ctrl_dir: str | None = None, deny_file: str | None = None) -> str:
    if not cfg.passphrase:
        raise ValueError("passphrase not set")
    lines = [
        f"interface={cfg.ap_iface}",
        "driver=nl80211",
        f"ssid={cfg.ssid}",
        "utf8_ssid=1",
        f"country_code={cfg.country}",
        "ieee80211d=1",
        f"hw_mode={hw_mode}",
        f"channel={channel}",
        "ieee80211n=1",
        "wmm_enabled=1",
        f"max_num_sta={cfg.max_clients}",
        "macaddr_acl=0",
        f"ignore_broadcast_ssid={'1' if cfg.hidden else '0'}",
        "auth_algs=1",
        "wpa=2",
        "wpa_key_mgmt=WPA-PSK",
        "rsn_pairwise=CCMP",
        f"wpa_passphrase={cfg.passphrase}",
    ]
    if cfg.client_isolation:
        lines.append("ap_isolate=1")
    if ctrl_dir:
        lines += [f"ctrl_interface={ctrl_dir}", "ctrl_interface_group=0"]
    if deny_file:
        lines.append(f"deny_mac_file={deny_file}")
    return "\n".join(lines) + "\n"


def render_dnsmasq(cfg: RouterConfig, leasefile: str, pidfile: str, with_dns: bool) -> str:
    start, end = cfg.dhcp_range()
    gw = cfg.gateway_ip
    lines = [
        f"interface={cfg.ap_iface}",
        "bind-interfaces",
        f"listen-address={gw}",
        "except-interface=lo",
        "user=root",
        "log-facility=-",
        f"pid-file={pidfile}",
        f"dhcp-leasefile={leasefile}",
        f"dhcp-range={start},{end},255.255.255.0,{cfg.lease_time}",
        f"dhcp-option=3,{gw}",
        "dhcp-authoritative",
    ]
    if with_dns:
        lines += ["no-resolv", "cache-size=500", f"dhcp-option=6,{gw}"]
        lines += [f"server={s}" for s in cfg.dns_servers]
    else:
        lines += ["port=0", "dhcp-option=6," + ",".join(cfg.dns_servers)]
    return "\n".join(lines) + "\n"


def _set(names: list[str]) -> str:
    return "{ " + ", ".join(f'"{n}"' for n in names) + " }"


def render_nft(cfg: RouterConfig, wan_ifaces: list[str]) -> str:
    """Own tables only (inet keyland, ip keyland_nat): other rules stay untouched."""
    wans = _set(wan_ifaces)
    ap = cfg.ap_iface
    subnet = cfg.subnet
    panel_rule = ""
    if not cfg.panel_on_wan:
        panel_rule = f'        iifname {wans} tcp dport {cfg.panel_port} drop\n'
    return (
        "add table inet keyland\n"
        "delete table inet keyland\n"
        "add table ip keyland_nat\n"
        "delete table ip keyland_nat\n"
        "table inet keyland {\n"
        "    chain input {\n"
        "        type filter hook input priority -10; policy accept;\n"
        f"{panel_rule}"
        "    }\n"
        "    chain forward {\n"
        "        type filter hook forward priority -10; policy accept;\n"
        f'        iifname "{ap}" oifname {wans} accept\n'
        f'        iifname {wans} oifname "{ap}" ct state established,related accept\n'
        "    }\n"
        "}\n"
        "table ip keyland_nat {\n"
        "    chain postrouting {\n"
        "        type nat hook postrouting priority srcnat; policy accept;\n"
        f"        ip saddr {subnet} oifname {wans} masquerade\n"
        "    }\n"
        "}\n"
    )
