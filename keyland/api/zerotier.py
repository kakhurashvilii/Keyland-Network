"""Administrator-only ZeroTier management API."""
from __future__ import annotations

import ipaddress
import re
import subprocess
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from keyland.auth import require_admin
from keyland.adapters.zerotier.monitor import (
    get_zerotier_status,
    get_zerotier_diagnostics,
    join_network,
    leave_network,
)
from keyland.adapters.zerotier.central import (
    get_central_members,
    get_central_networks,
    set_central_member_authorization,
    update_central_network,
    valid_member_id,
    valid_network_id,
)

router = APIRouter(prefix="/api/v1/zerotier", tags=["ZeroTier"])


def _require_confirmation(confirmed: bool, phrase: str, expected: str) -> None:
    if confirmed is not True or phrase != expected:
        raise HTTPException(
            status_code=400,
            detail=f"Explicit confirmation required. Type exactly: {expected}",
        )


@router.get("/status")
def zerotier_status(user: dict = Depends(require_admin)):
    return get_zerotier_status()


@router.get("/diagnostics")
def zerotier_diagnostics(
    lines: int = 100,
    user: dict = Depends(require_admin),
):
    if not 1 <= lines <= 200:
        raise HTTPException(status_code=400, detail="Log lines must be between 1 and 200")
    return get_zerotier_diagnostics(lines)


class LocalNetworkRequest(BaseModel):
    network_id: str = Field(min_length=16, max_length=16)
    confirmed: bool = False
    confirmation_text: str = Field(default="", max_length=64)

    @field_validator("network_id")
    @classmethod
    def validate_network_id(cls, value: str) -> str:
        if not valid_network_id(value):
            raise ValueError("Network ID must be 16 hexadecimal characters")
        return value


@router.post("/local/join")
def zerotier_join(payload: LocalNetworkRequest, user: dict = Depends(require_admin)):
    _require_confirmation(payload.confirmed, payload.confirmation_text, f"JOIN NETWORK {payload.network_id}")
    try:
        result = join_network(payload.network_id)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=503, detail=f"ZeroTier CLI unavailable: {exc}")
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Could not join ZeroTier network")
    return result


@router.post("/local/leave")
def zerotier_leave(payload: LocalNetworkRequest, user: dict = Depends(require_admin)):
    _require_confirmation(payload.confirmed, payload.confirmation_text, f"LEAVE NETWORK {payload.network_id}")
    try:
        result = leave_network(payload.network_id)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=503, detail=f"ZeroTier CLI unavailable: {exc}")
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Could not leave ZeroTier network")
    return result


@router.get("/central/networks")
def zerotier_central_networks(user: dict = Depends(require_admin)):
    return get_central_networks()


@router.get("/central/networks/{network_id}/members")
def zerotier_central_members(network_id: str, user: dict = Depends(require_admin)):
    if not valid_network_id(network_id):
        raise HTTPException(status_code=400, detail="Invalid ZeroTier network ID")
    return get_central_members(network_id)


class MemberAuthorizationRequest(BaseModel):
    confirmed: bool = False
    confirmation_text: str = Field(default="", max_length=64)


@router.post("/central/networks/{network_id}/members/{member_id}/authorize")
def zerotier_authorize_member(
    network_id: str,
    member_id: str,
    payload: MemberAuthorizationRequest,
    user: dict = Depends(require_admin),
):
    if not valid_network_id(network_id) or not valid_member_id(member_id):
        raise HTTPException(status_code=400, detail="Invalid network or member ID")
    _require_confirmation(payload.confirmed, payload.confirmation_text, f"AUTHORIZE {member_id}")
    result = set_central_member_authorization(network_id, member_id, True)
    if not result.get("success"):
        raise HTTPException(status_code=502, detail=result.get("error") or "Central API authorization failed")
    return result


@router.post("/central/networks/{network_id}/members/{member_id}/deauthorize")
def zerotier_deauthorize_member(
    network_id: str,
    member_id: str,
    payload: MemberAuthorizationRequest,
    user: dict = Depends(require_admin),
):
    if not valid_network_id(network_id) or not valid_member_id(member_id):
        raise HTTPException(status_code=400, detail="Invalid network or member ID")
    _require_confirmation(payload.confirmed, payload.confirmation_text, f"DEAUTHORIZE {member_id}")
    result = set_central_member_authorization(network_id, member_id, False)
    if not result.get("success"):
        raise HTTPException(status_code=502, detail=result.get("error") or "Central API deauthorization failed")
    return result


class RouteRequest(BaseModel):
    target: str = Field(min_length=3, max_length=64)
    via: str | None = Field(default=None, max_length=64)

    @field_validator("target")
    @classmethod
    def validate_target(cls, value: str) -> str:
        try:
            ipaddress.ip_network(value, strict=False)
        except ValueError as exc:
            raise ValueError("Route target must be an IPv4 or IPv6 CIDR") from exc
        return value

    @field_validator("via")
    @classmethod
    def validate_via(cls, value: str | None) -> str | None:
        if value:
            try:
                ipaddress.ip_address(value)
            except ValueError as exc:
                raise ValueError("Route gateway must be an IP address") from exc
        return value


class NetworkSettingsRequest(BaseModel):
    confirmed: bool = False
    confirmation_text: str = Field(default="", max_length=64)
    name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=255)
    private: bool | None = None
    routes: list[RouteRequest] | None = Field(default=None, max_length=32)
    dns_servers: list[str] | None = Field(default=None, max_length=8)
    dns_domain: str | None = Field(default=None, max_length=253)

    @field_validator("dns_servers")
    @classmethod
    def validate_dns_servers(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return values
        for value in values:
            try:
                ipaddress.ip_address(value)
            except ValueError as exc:
                raise ValueError("DNS servers must be valid IP addresses") from exc
        return values


@router.post("/central/networks/{network_id}/settings")
def zerotier_update_network(
    network_id: str,
    payload: NetworkSettingsRequest,
    user: dict = Depends(require_admin),
):
    if not valid_network_id(network_id):
        raise HTTPException(status_code=400, detail="Invalid ZeroTier network ID")
    _require_confirmation(payload.confirmed, payload.confirmation_text, f"UPDATE NETWORK {network_id}")

    config: dict[str, Any] = {}
    if payload.name is not None:
        config["name"] = payload.name.strip()
    if payload.description is not None:
        config["description"] = payload.description.strip()
    if payload.private is not None:
        config["private"] = payload.private
    if payload.routes is not None:
        config["routes"] = [
            {"target": route.target, **({"via": route.via} if route.via else {})}
            for route in payload.routes
        ]
    if payload.dns_servers is not None or payload.dns_domain is not None:
        config["dns"] = {
            "servers": payload.dns_servers or [],
            **({"domain": payload.dns_domain} if payload.dns_domain else {}),
        }
    if not config:
        raise HTTPException(status_code=400, detail="No network settings supplied")

    result = update_central_network(network_id, config)
    if not result.get("success"):
        raise HTTPException(status_code=502, detail=result.get("error") or "Central API update failed")
    return result
