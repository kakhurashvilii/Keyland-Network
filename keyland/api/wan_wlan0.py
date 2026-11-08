"""Safe IPv4 configuration for the primary Wi-Fi WAN (wlan0) only."""
from __future__ import annotations

import ipaddress
import shutil
import subprocess
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from keyland.auth import require_admin

router = APIRouter(prefix="/api/v1/network/wan/wlan0", tags=["WAN wlan0"])
IFACE = "wlan0"
_FIELDS = ("ipv4.method", "ipv4.addresses", "ipv4.gateway", "ipv4.dns", "ipv4.ignore-auto-dns")


def _nmcli(*args: str, timeout: int = 15) -> str:
    if not shutil.which("nmcli"):
        raise HTTPException(status_code=503, detail="NetworkManager (nmcli) is unavailable.")
    try:
        proc = subprocess.run(
            ["nmcli", "--terse", "--escape", "yes", *args],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=503, detail=f"NetworkManager command failed: {exc}")
    if proc.returncode:
        raise HTTPException(status_code=409, detail=(proc.stderr.strip() or "NetworkManager rejected the request."))
    return proc.stdout.strip()


def _profile() -> str:
    name = _nmcli("-g", "GENERAL.CONNECTION", "device", "show", IFACE)
    if not name or name == "--":
        raise HTTPException(status_code=409, detail="wlan0 has no active NetworkManager connection profile.")
    return name


def _values(profile: str) -> dict[str, str]:
    # Query each setting separately: nmcli omits empty fields in some versions,
    # which would otherwise shift later values into the wrong keys.
    return {
        key: _nmcli("-g", key, "connection", "show", profile)
        for key in _FIELDS
    }


def _split_values(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip() and item.strip() != "--"]


@router.get("")
def get_wlan0_wan(user: dict = Depends(require_admin)):
    profile = _profile()
    saved = _values(profile)
    active = {}
    for key in ("IP4.ADDRESS", "IP4.GATEWAY", "IP4.DNS"):
        raw = _nmcli("-g", key, "device", "show", IFACE)
        active[key] = _split_values(raw)
    address = (active["IP4.ADDRESS"] or _split_values(saved["ipv4.addresses"]))
    cidr = address[0] if address else ""
    return {
        "interface": IFACE,
        "profile": profile,
        "mode": "static" if saved["ipv4.method"] == "manual" else "dhcp",
        "active": {
            "addresses": active["IP4.ADDRESS"],
            "gateway": (active["IP4.GATEWAY"] or [None])[0],
            "dns": active["IP4.DNS"],
        },
        "saved": {
            "method": saved["ipv4.method"],
            "address": cidr,
            "gateway": saved["ipv4.gateway"] or (active["IP4.GATEWAY"] or [None])[0],
            "dns": _split_values(saved["ipv4.dns"]) or active["IP4.DNS"],
            "ignore_auto_dns": saved["ipv4.ignore-auto-dns"] == "yes",
        },
    }


class Wlan0IPv4Request(BaseModel):
    mode: Literal["dhcp", "static"]
    address: str | None = Field(default=None, max_length=15)
    prefix: int | None = Field(default=24, ge=1, le=32)
    gateway: str | None = Field(default=None, max_length=15)
    dns: list[str] = Field(default_factory=list, max_length=4)
    confirmed: bool = False


@router.put("")
def set_wlan0_wan(body: Wlan0IPv4Request, user: dict = Depends(require_admin)):
    if body.confirmed is not True:
        raise HTTPException(status_code=400, detail="Explicit administrator confirmation is required.")
    profile = _profile()
    old = _values(profile)

    if body.mode == "static":
        try:
            iface = ipaddress.IPv4Interface(f"{body.address}/{body.prefix}")
            gateway = ipaddress.IPv4Address(body.gateway or "")
            dns = [str(ipaddress.IPv4Address(item)) for item in body.dns]
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid static IPv4 settings: {exc}")
        if gateway not in iface.network or gateway == iface.ip:
            raise HTTPException(status_code=422, detail="Gateway must be inside the selected subnet and different from the device IP.")
        changes = {
            "ipv4.method": "manual",
            "ipv4.addresses": str(iface),
            "ipv4.gateway": str(gateway),
            "ipv4.dns": ",".join(dns),
            "ipv4.ignore-auto-dns": "yes" if dns else "no",
        }
    else:
        changes = {
            "ipv4.method": "auto",
            "ipv4.addresses": "",
            "ipv4.gateway": "",
            "ipv4.dns": "",
            "ipv4.ignore-auto-dns": "no",
        }

    try:
        args = ["connection", "modify", profile]
        for key, value in changes.items():
            args.extend([key, value])
        _nmcli(*args)
        # Reapply in place; do not bring wlan0 down or touch other interfaces.
        _nmcli("device", "reapply", IFACE, timeout=25)
    except HTTPException as exc:
        # Restore the saved profile if NetworkManager refuses to reapply.
        try:
            restore = ["connection", "modify", profile]
            for key in _FIELDS:
                restore.extend([key, old.get(key, "")])
            _nmcli(*restore)
            _nmcli("device", "reapply", IFACE, timeout=25)
        except Exception:
            pass
        raise exc

    return {"success": True, "interface": IFACE, "mode": body.mode,
            "message": "wlan0 IPv4 configuration updated. Verify Internet status before leaving this page."}
