"""Authenticated SIM SMS endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from keyland.auth import require_admin
from keyland.adapters.cellular.sms import list_sms_messages, send_sms_message

router = APIRouter(prefix="/api/v1/cellular/sms", tags=["Cellular SMS"])


class SendSMSRequest(BaseModel):
    number: str = Field(min_length=6, max_length=25)
    message: str = Field(min_length=1, max_length=1600)


@router.get("/messages")
def sms_messages(user: dict = Depends(require_admin)):
    try:
        return list_sms_messages()
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/send")
def sms_send(payload: SendSMSRequest, user: dict = Depends(require_admin)):
    try:
        return send_sms_message(payload.number, payload.message)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
