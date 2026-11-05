"""Read-only SIM / modem status endpoints."""
from fastapi import APIRouter, Depends

from keyland.auth import require_admin
from keyland.adapters.cellular.monitor import (
    get_cellular_signal, get_cellular_status, get_cellular_usage,
)

router = APIRouter(prefix="/api/v1/cellular", tags=["Cellular"])


@router.get("/status")
def cellular_status(user: dict = Depends(require_admin)):
    return get_cellular_status()


@router.get("/signal")
def cellular_signal(user: dict = Depends(require_admin)):
    return get_cellular_signal()


@router.get("/usage")
def cellular_usage(user: dict = Depends(require_admin)):
    return get_cellular_usage()
