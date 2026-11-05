"""Authenticated Wi-Fi hotspot management API."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from keyland.auth import require_admin
from keyland.adapters.wifi.router import start as start_router_wifi, stop as stop_router_wifi, status as router_wifi_status
from keyland.adapters.wifi.hotspot import (
    get_hotspot_capabilities, start_hotspot, stop_hotspot, create_virtual_ap,
)

router = APIRouter(prefix="/api/v1/wifi/hotspot", tags=["Wi-Fi Hotspot"])


class HotspotRequest(BaseModel):
    interface: str = Field(min_length=1, max_length=15)
    ssid: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=8, max_length=63)
    security: str = "wpa2-psk"
    dns_servers: list[str] = Field(default_factory=list, max_length=4)
    ipv4_cidr: str = Field(default="192.168.88.1/24", min_length=9, max_length=18)
    confirmed: bool = False


class StopRequest(BaseModel):
    confirmed: bool = False


@router.get("/capabilities")
def hotspot_capabilities(user: dict = Depends(require_admin)):
    return get_hotspot_capabilities()


@router.post("/start")
def hotspot_start(payload: HotspotRequest, user: dict = Depends(require_admin)):
    result = start_hotspot(payload.model_dump())
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/stop")
def hotspot_stop(payload: StopRequest, user: dict = Depends(require_admin)):
    result = stop_hotspot(payload.confirmed)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


class VirtualApRequest(BaseModel):
    interface: str = Field(min_length=1, max_length=15)
    confirmed: bool = False


@router.post("/create-interface")
def hotspot_create_interface(payload: VirtualApRequest, user: dict = Depends(require_admin)):
    result = create_virtual_ap(payload.interface, payload.confirmed)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


class RouterWifiRequest(BaseModel):
    mode: str = "hotspot"
    interface: str = Field(default="keyland-ap", min_length=1, max_length=15)
    upstream_interface: str = Field(min_length=1, max_length=15)
    band: str = "2.4"
    channel: int | None = Field(default=None, ge=1, le=165)
    ssid: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=8, max_length=63)
    dns_servers: list[str] = Field(default_factory=list, max_length=4)
    ipv4_cidr: str = Field(default="192.168.88.1/24", min_length=9, max_length=18)
    confirmed: bool = False


@router.get("/router/status")
def router_wifi_get_status(user: dict = Depends(require_admin)):
    return router_wifi_status()


@router.post("/router/start")
def router_wifi_start(payload: RouterWifiRequest, user: dict = Depends(require_admin)):
    result = start_router_wifi(payload.model_dump())
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/router/stop")
def router_wifi_stop(payload: StopRequest, user: dict = Depends(require_admin)):
    result = stop_router_wifi(payload.confirmed)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result
