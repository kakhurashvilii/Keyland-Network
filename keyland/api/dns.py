"""Authenticated DNS management endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from keyland.auth import require_admin
from keyland.adapters.linux.dns import get_dns_status, set_dns, reset_dns

router = APIRouter(prefix="/api/v1/network/dns", tags=["DNS"])


class DNSRequest(BaseModel):
    interface: str = Field(min_length=1, max_length=15)
    servers: list[str] = Field(min_length=1, max_length=4)
    confirmed: bool = False


class DNSResetRequest(BaseModel):
    interface: str = Field(min_length=1, max_length=15)
    confirmed: bool = False


@router.get("")
def dns_status(user: dict = Depends(require_admin)):
    return get_dns_status()


@router.post("")
def dns_apply(payload: DNSRequest, user: dict = Depends(require_admin)):
    result = set_dns(payload.interface, payload.servers, payload.confirmed)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/reset")
def dns_reset(payload: DNSResetRequest, user: dict = Depends(require_admin)):
    result = reset_dns(payload.interface, payload.confirmed)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result
