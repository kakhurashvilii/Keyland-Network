"""Router configuration: validation and persistence."""
from __future__ import annotations

import ipaddress
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Literal

import psutil
from pydantic import BaseModel, Field, field_validator, model_validator

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = Path(os.getenv("KEYLAND_ROUTER_CONFIG", ROOT / "data" / "router.json"))

IFACE_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,15}$")
MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
HOST_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")
WanMode = Literal["wifi_only", "sim_only", "failover", "balance"]
WanName = Literal["wifi", "sim"]


def _check_iface(value: str, allow_empty: bool = False) -> str:
    if allow_empty and value == "":
        return value
    if not IFACE_RE.match(value):
        raise ValueError("invalid interface name")
    return value


def _check_ipv4(value: str) -> str:
    if not HOST_RE.match(value):
        raise ValueError(f"invalid IPv4 address: {value}")
    ipaddress.IPv4Address(value)
    return value


class RouterConfig(BaseModel):
    # --- Hotspot (AP) ---
    ssid: str = "Keyland-Router"
    passphrase: str | None = None
    hidden: bool = False
    country: str = "GE"
    channel: int = Field(default=6, ge=1, le=165)  # used when wlan0 is not connected
    follow_wan_channel: bool = True  # AP follows the Wi-Fi WAN channel (required on 1 radio)
    max_clients: int = Field(default=32, ge=1, le=64)
    client_isolation: bool = False       # hotspot clients cannot see each other
    blocked_macs: list[str] = []
    ap_iface: str = "ap0"

    # --- LAN ---
    subnet: str = "192.168.50.0/24"
    dhcp_start: int = Field(default=10, ge=2, le=250)
    dhcp_end: int = Field(default=200, ge=3, le=253)
    lease_time: str = "12h"
    dns_servers: list[str] = ["1.1.1.1", "8.8.8.8"]

    # --- WAN ---
    wifi_wan_iface: str = "wlan0"
    sim_wan_iface: str = ""  # empty = auto-detect
    wan_mode: WanMode = "failover"
    primary: WanName = "wifi"
    weight_wifi: int = Field(default=1, ge=1, le=10)
    weight_sim: int = Field(default=1, ge=1, le=10)
    probe_targets: list[str] = ["1.1.1.1", "8.8.8.8"]
    probe_interval: int = Field(default=5, ge=2, le=60)
    fail_threshold: int = Field(default=3, ge=1, le=20)
    recover_threshold: int = Field(default=2, ge=1, le=20)

    # --- Management ---
    start_on_boot: bool = False
    panel_port: int = Field(default=8000, ge=1, le=65535)
    panel_on_wan: bool = False  # allow the panel from the upstream Wi-Fi / SIM side

    @field_validator("ssid")
    @classmethod
    def _ssid(cls, v: str) -> str:
        raw = v.encode("utf-8")
        if not 1 <= len(raw) <= 32:
            raise ValueError("SSID must be 1-32 bytes")
        if any(ord(c) < 32 or ord(c) == 127 for c in v):
            raise ValueError("SSID contains control characters")
        return v

    @field_validator("passphrase")
    @classmethod
    def _pass(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not 8 <= len(v) <= 63 or not v.isascii() or not v.isprintable():
            raise ValueError("Passphrase: 8-63 printable ASCII characters")
        return v

    @field_validator("blocked_macs")
    @classmethod
    def _macs(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for m in v:
            m = m.strip().lower()
            if not MAC_RE.match(m):
                raise ValueError(f"invalid MAC address: {m}")
            if m not in out:
                out.append(m)
        if len(out) > 200:
            raise ValueError("too many blocked devices")
        return out

    @field_validator("country")
    @classmethod
    def _country(cls, v: str) -> str:
        v = v.upper()
        if not re.fullmatch(r"[A-Z]{2}", v):
            raise ValueError("country must be a 2-letter code")
        return v

    @field_validator("ap_iface", "wifi_wan_iface")
    @classmethod
    def _ifaces(cls, v: str) -> str:
        return _check_iface(v)

    @field_validator("sim_wan_iface")
    @classmethod
    def _sim_iface(cls, v: str) -> str:
        return _check_iface(v, allow_empty=True)

    @field_validator("lease_time")
    @classmethod
    def _lease(cls, v: str) -> str:
        if not re.fullmatch(r"\d{1,4}[smh]", v):
            raise ValueError("lease_time like 30m, 12h")
        return v

    @field_validator("dns_servers", "probe_targets")
    @classmethod
    def _ips(cls, v: list[str]) -> list[str]:
        if not 1 <= len(v) <= 4:
            raise ValueError("1-4 IPv4 addresses required")
        return [_check_ipv4(x) for x in v]

    @field_validator("subnet")
    @classmethod
    def _subnet(cls, v: str) -> str:
        net = ipaddress.ip_network(v, strict=True)
        if net.version != 4 or net.prefixlen != 24 or not net.is_private:
            raise ValueError("subnet must be a private IPv4 /24, e.g. 192.168.50.0/24")
        return str(net)

    @model_validator(mode="after")
    def _cross(self) -> "RouterConfig":
        if self.dhcp_start >= self.dhcp_end:
            raise ValueError("dhcp_start must be lower than dhcp_end")
        if self.ap_iface == self.wifi_wan_iface:
            raise ValueError("ap_iface and wifi_wan_iface must differ")
        return self

    # derived helpers
    @property
    def network(self) -> ipaddress.IPv4Network:
        return ipaddress.ip_network(self.subnet)

    @property
    def gateway_ip(self) -> str:
        return str(self.network.network_address + 1)

    def dhcp_range(self) -> tuple[str, str]:
        base = self.network.network_address
        return str(base + self.dhcp_start), str(base + self.dhcp_end)

    def public_dict(self) -> dict:
        data = self.model_dump()
        data["passphrase_set"] = bool(self.passphrase)
        data.pop("passphrase", None)
        return data


def subnet_conflicts(cfg: RouterConfig) -> list[str]:
    """Names of other interfaces whose IPv4 network overlaps the AP subnet."""
    conflicts = []
    for name, addrs in psutil.net_if_addrs().items():
        if name == cfg.ap_iface:
            continue
        for a in addrs:
            if a.family.name != "AF_INET" or not a.netmask:
                continue
            try:
                other = ipaddress.ip_network(f"{a.address}/{a.netmask}", strict=False)
            except ValueError:
                continue
            if other.overlaps(cfg.network):
                conflicts.append(f"{name} ({other})")
    return conflicts


def load_config(path: Path | None = None) -> RouterConfig:
    path = path or CONFIG_PATH
    try:
        return RouterConfig.model_validate(json.loads(path.read_text("utf-8")))
    except FileNotFoundError:
        return RouterConfig()
    except (ValueError, OSError):
        # Corrupted file: keep it for inspection and fall back to defaults.
        try:
            path.replace(path.with_suffix(".json.corrupt"))
        except OSError:
            pass
        return RouterConfig()


def save_config(cfg: RouterConfig, path: Path | None = None) -> None:
    path = path or CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".router-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(cfg.model_dump(), fh, indent=2, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
