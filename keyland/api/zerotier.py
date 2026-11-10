"""Protected ZeroTier status and Central API read endpoints."""
from fastapi import APIRouter, Depends, HTTPException

from keyland.auth import require_admin
from keyland.adapters.zerotier.monitor import get_zerotier_status
from keyland.adapters.zerotier.central import (
    get_central_members,
    get_central_networks,
    valid_network_id,
)

router = APIRouter(prefix="/api/v1/zerotier", tags=["ZeroTier"])


@router.get("/status")
def zerotier_status(user: dict = Depends(require_admin)):
    return get_zerotier_status()


@router.get("/central/networks")
def zerotier_central_networks(user: dict = Depends(require_admin)):
    """List Central networks; token remains server-side."""
    return get_central_networks()


@router.get("/central/networks/{network_id}/members")
def zerotier_central_members(
    network_id: str,
    user: dict = Depends(require_admin),
):
    """List members of one Central network without changing authorization."""
    if not valid_network_id(network_id):
        raise HTTPException(status_code=400, detail="Invalid ZeroTier network ID")
    return get_central_members(network_id)
