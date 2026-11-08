"""Authenticated Wi-Fi hotspot management API."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from keyland.auth import require_admin
import json
import os
import tempfile
import subprocess
from pathlib import Path
from keyland.adapters.wifi.hotspot import (
    get_hotspot_capabilities, start_hotspot, stop_hotspot,
)
from keyland.adapters.wifi.repeater import connect_wifi, disconnect_wifi

router = APIRouter(prefix="/api/v1/wifi/hotspot", tags=["Wi-Fi Hotspot"])

# Last successfully applied dashboard configuration. Keep secrets readable only
# by the service account (0600); this endpoint is protected by require_admin.
_SETTINGS_PATH = Path(__file__).resolve().parents[2] / "data" / "hotspot-settings.json"


def _save_hotspot_settings(settings: dict) -> None:
    _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=_SETTINGS_PATH.parent, prefix=".hotspot-settings-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(settings, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, _SETTINGS_PATH)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


@router.get("/router/settings")
def router_wifi_saved_settings(user: dict = Depends(require_admin)):
    try:
        settings = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"saved": False, "settings": None}
    except (OSError, ValueError):
        raise HTTPException(status_code=500, detail="Saved Hotspot settings could not be read.")
    if not isinstance(settings, dict):
        raise HTTPException(status_code=500, detail="Saved Hotspot settings are invalid.")
    return {"saved": True, "settings": settings}


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
    dns_monitoring: bool = False
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


class RepeaterConnectRequest(BaseModel):
    interface: str = Field(min_length=1, max_length=15)
    ssid: str = Field(min_length=1, max_length=32)
    bssid: str = Field(min_length=17, max_length=17)
    password: str = Field(default="", max_length=63)
    confirmed: bool = False


class RepeaterDisconnectRequest(BaseModel):
    interface: str = Field(min_length=1, max_length=15)
    confirmed: bool = False


@router.post("/repeater/connect")
def repeater_connect(payload: RepeaterConnectRequest, user: dict = Depends(require_admin)):
    result = connect_wifi(
        payload.interface,
        payload.ssid,
        payload.bssid,
        payload.password,
        payload.confirmed,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Wi-Fi connection failed."))
    return result


@router.post("/repeater/disconnect")
def repeater_disconnect(payload: RepeaterDisconnectRequest, user: dict = Depends(require_admin)):
    result = disconnect_wifi(payload.interface, payload.confirmed)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Wi-Fi disconnect failed."))
    return result


@router.get("/router/autostart")
def router_wifi_autostart_status(user: dict = Depends(require_admin)):
    return _call_router_helper("autostart_status")


@router.post("/router/autostart/enable")
def router_wifi_autostart_enable(payload: StopRequest, user: dict = Depends(require_admin)):
    if payload.confirmed is not True:
        raise HTTPException(status_code=400, detail="Explicit administrator confirmation is required.")
    result = _call_router_helper("autostart_enable")
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Could not enable automatic startup."))
    return result


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
    settings = payload.model_dump()
    result = _call_router_helper("start", settings)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Router start failed."))
    try:
        _save_hotspot_settings(settings)
    except OSError:
        # Do not report a successful persistent configuration if it could not be saved.
        _call_router_helper("stop", {"confirmed": True})
        raise HTTPException(status_code=500, detail="Hotspot started but settings could not be saved; router was stopped.")
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


@router.get("/router/dns/queries")
def router_wifi_dns_queries(limit: int = 200, user: dict = Depends(require_admin)):
    if not 1 <= limit <= 1000:
        raise HTTPException(status_code=400, detail="DNS query limit must be between 1 and 1000.")
    return _call_router_helper("dns_queries", {"limit": limit})


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
