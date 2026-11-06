"""Authenticated Wi-Fi hotspot management API."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from keyland.auth import require_admin
import json
import subprocess
from keyland.adapters.wifi.hotspot import (
    get_hotspot_capabilities, start_hotspot, stop_hotspot,
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
    ap_interface: str = Field(default="keyland-ap", min_length=1, max_length=15)
    confirmed: bool = False


@router.post("/create-interface")
def hotspot_create_interface(payload: VirtualApRequest, user: dict = Depends(require_admin)):
    result = _call_router_helper("prepare", {
        "interface": payload.interface,
        "ap_interface": payload.ap_interface,
        "confirmed": payload.confirmed,
    })
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "AP preparation failed"))
    return result


class RouterWifiRequest(BaseModel):
    mode: str = "hotspot"
    interface: str = Field(default="keyland-ap", min_length=1, max_length=15)
    source_interface: str = Field(default="wlan0", min_length=1, max_length=15)
    upstream_interface: str = Field(min_length=1, max_length=15)
    band: str = "2.4"
    channel: int | None = Field(default=None, ge=1, le=165)
    ssid: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=8, max_length=63)
    dns_servers: list[str] = Field(default_factory=list, max_length=4)
    ipv4_cidr: str = Field(default="192.168.88.1/24", min_length=9, max_length=18)
    confirmed: bool = False


def _call_router_helper(action: str, payload: dict | None = None) -> dict:
    request = {"action": action, "payload": payload or {}}

    try:
        proc = subprocess.run(
            ["sudo", "-n", "/usr/local/sbin/keyland-routerctl"],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=503, detail="Router helper timed out.")
    except OSError:
        raise HTTPException(status_code=503, detail="Router helper could not be started.")

    try:
        result = json.loads(proc.stdout)
    except (ValueError, TypeError):
        raise HTTPException(status_code=503, detail="Router helper returned invalid data.")

    if not isinstance(result, dict):
        raise HTTPException(status_code=503, detail="Router helper returned an invalid response.")

    return result


@router.get("/router/status")
def router_wifi_get_status(user: dict = Depends(require_admin)):
    return _call_router_helper("status")

@router.get("/router/autostart")
def router_wifi_autostart_status(user: dict = Depends(require_admin)):
    return _call_router_helper("autostart_status")


@router.post("/router/autostart/disable")
def router_wifi_autostart_disable(payload: StopRequest, user: dict = Depends(require_admin)):
    if payload.confirmed is not True:
        raise HTTPException(status_code=400, detail="Explicit administrator confirmation is required.")
    result = _call_router_helper("autostart_disable")
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Could not disable automatic startup."))
    return result


@router.post("/router/start")
def router_wifi_start(payload: RouterWifiRequest, user: dict = Depends(require_admin)):
    result = _call_router_helper("start", payload.model_dump())
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Router start failed."))
    return result


@router.post("/router/stop")
def router_wifi_stop(payload: StopRequest, user: dict = Depends(require_admin)):
    result = _call_router_helper("stop", {"confirmed": payload.confirmed})
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Router stop failed."))
    return result


class ClientActionRequest(BaseModel):
    mac: str = Field(min_length=17, max_length=17)
    confirmed: bool = False


class ClientLimitRequest(BaseModel):
    mac: str = Field(min_length=17, max_length=17)
    download_mbps: int = Field(ge=1, le=1000)
    upload_mbps: int = Field(ge=1, le=1000)
    confirmed: bool = False


@router.get("/router/clients")
def router_wifi_clients(user: dict = Depends(require_admin)):
    return _call_router_helper("clients")


@router.post("/router/clients/block")
def router_wifi_client_block(payload: ClientActionRequest, user: dict = Depends(require_admin)):
    if payload.confirmed is not True:
        raise HTTPException(status_code=400, detail="Explicit administrator confirmation is required.")
    result = _call_router_helper("client_block", {"mac": payload.mac, "blocked": True})
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Client block failed."))
    return result


@router.post("/router/clients/unblock")
def router_wifi_client_unblock(payload: ClientActionRequest, user: dict = Depends(require_admin)):
    if payload.confirmed is not True:
        raise HTTPException(status_code=400, detail="Explicit administrator confirmation is required.")
    result = _call_router_helper("client_block", {"mac": payload.mac, "blocked": False})
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Client unblock failed."))
    return result


@router.post("/router/clients/limit")
def router_wifi_client_limit(payload: ClientLimitRequest, user: dict = Depends(require_admin)):
    if payload.confirmed is not True:
        raise HTTPException(status_code=400, detail="Explicit administrator confirmation is required.")
    result = _call_router_helper("client_limit", {
        "mac": payload.mac,
        "download_mbps": payload.download_mbps,
        "upload_mbps": payload.upload_mbps,
    })
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Client speed limit failed."))
    return result
