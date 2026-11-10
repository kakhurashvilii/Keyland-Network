"""Read-only ZeroTier status API."""
from fastapi import APIRouter, Depends

from keyland.auth import require_admin
from keyland.adapters.zerotier.monitor import get_zerotier_status

router = APIRouter(prefix="/api/v1/zerotier", tags=["ZeroTier"])


@router.get("/status")
def zerotier_status(user: dict = Depends(require_admin)):
    return get_zerotier_status()
