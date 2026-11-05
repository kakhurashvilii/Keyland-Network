"""HTTP API for router control (admin only; CSRF enforced by main middleware)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError

from keyland.auth import require_admin

from .config import RouterConfig
from .controller import RouterController, RouterError

controller = RouterController()
router = APIRouter(prefix="/api/v1/router", dependencies=[Depends(require_admin)])


def _fail(exc: Exception, code: int = 400):
    raise HTTPException(status_code=code, detail=str(exc))


class WanModeRequest(BaseModel):
    mode: Literal["wifi_only", "sim_only", "failover", "balance"]
    primary: Literal["wifi", "sim"] | None = None


class WifiConnectRequest(BaseModel):
    ssid: str = Field(min_length=1, max_length=32)
    password: str | None = Field(default=None, max_length=64)


@router.get("/status")
def status():
    return controller.status()


@router.get("/preflight")
def preflight():
    return controller.preflight()


@router.get("/config")
def get_config():
    return controller.cfg.public_dict()


@router.put("/config")
def put_config(body: dict, apply: bool = False):
    merged = controller.cfg.model_dump()
    incoming = {k: v for k, v in body.items() if k in merged}
    if not incoming.get("passphrase"):
        incoming.pop("passphrase", None)          # keep the stored password
    merged.update(incoming)
    try:
        new_cfg = RouterConfig.model_validate(merged)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_input=False, include_url=False,
                                                              include_context=False))
    was_running = controller.state == "running"
    controller.update_config(new_cfg)
    if apply and was_running:
        try:
            controller.restart()
        except RouterError as exc:
            _fail(exc, 409)
    return {"saved": True, "restart_required": was_running and not apply,
            "config": new_cfg.public_dict()}


@router.post("/start")
def start():
    try:
        return controller.start()
    except RouterError as exc:
        _fail(exc, 409)


@router.post("/stop")
def stop():
    return controller.stop()


@router.post("/restart")
def restart():
    try:
        return controller.restart()
    except RouterError as exc:
        _fail(exc, 409)


@router.post("/wan/mode")
def wan_mode(req: WanModeRequest):
    try:
        return controller.set_wan_mode(req.mode, req.primary)
    except ValidationError as exc:
        _fail(exc, 422)


@router.get("/clients")
def clients():
    return {"clients": controller.clients(), "blocked": controller.cfg.blocked_macs}


@router.post("/clients/{mac}/{action}")
def client_action(mac: str, action: Literal["kick", "block", "unblock"]):
    try:
        return getattr(controller, f"{action}_client")(mac)
    except RouterError as exc:
        _fail(exc, 400)


@router.get("/wifi/scan")
def wifi_scan(rescan: bool = True):
    try:
        return controller.wifi_scan(rescan)
    except RouterError as exc:
        _fail(exc, 409)


@router.get("/sim")
def sim():
    return controller.sim_info()


@router.post("/sim/{action}")
def sim_action(action: Literal["up", "down"]):
    try:
        return controller.sim_control(action == "up")
    except RouterError as exc:
        _fail(exc, 409)


@router.post("/wifi/connect")
def wifi_connect(req: WifiConnectRequest):
    try:
        return controller.wifi_connect(req.ssid, req.password)
    except RouterError as exc:
        _fail(exc, 409)


@router.get("/logs")
def logs(service: Literal["events", "hostapd", "dnsmasq"] = "events",
         lines: int = Query(60, ge=1, le=300)):
    try:
        return {"service": service, "text": controller.logs(service, lines)}
    except RouterError as exc:
        _fail(exc, 404)
