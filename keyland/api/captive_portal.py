"""Captive Portal configuration API (editor foundation; no traffic interception yet)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from keyland.auth import require_admin

router = APIRouter(prefix="/api/v1/captive-portal", tags=["Captive Portal"])
_SETTINGS_PATH = Path(__file__).resolve().parents[2] / "data" / "captive-portal.json"
_DEFAULTS = {
    "enabled": False,
    "auth_mode": "consent",
    "logo_text": "KEYLAND",
    "title": "მოგესალმებით!",
    "welcome_text": "ინტერნეტით სარგებლობის გასაგრძელებლად გაეცანით პირობებს.",
    "terms_text": "ქსელით სარგებლობისას დაიცავით მოქმედი კანონმდებლობა და პატივი ეცით სხვა მომხმარებლებს.",
    "button_text": "ვეთანხმები და გაგრძელება",
    "background_color": "#101827",
    "accent_color": "#2878e8",
    "session_minutes": 1440,
}
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _read_settings() -> dict:
    try:
        raw = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return dict(_DEFAULTS)
    except (OSError, ValueError):
        raise HTTPException(status_code=500, detail="Captive Portal settings could not be read.")
    if not isinstance(raw, dict):
        raise HTTPException(status_code=500, detail="Captive Portal settings are invalid.")
    result = dict(_DEFAULTS)
    result.update({key: value for key, value in raw.items() if key in _DEFAULTS or key == "password_hash"})
    return result


def _write_settings(settings: dict) -> None:
    _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=_SETTINGS_PATH.parent, prefix=".captive-portal-", suffix=".tmp"
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


def _public_settings(settings: dict) -> dict:
    return {key: settings[key] for key in _DEFAULTS if key != "enabled"} | {
        "enabled": bool(settings.get("enabled", False)),
        "password_configured": bool(settings.get("password_hash")),
    }


class PortalSettings(BaseModel):
    enabled: bool = False
    auth_mode: Literal["consent", "password", "both"] = "consent"
    logo_text: str = Field(default="KEYLAND", min_length=1, max_length=40)
    title: str = Field(default="მოგესალმებით!", min_length=1, max_length=100)
    welcome_text: str = Field(default=_DEFAULTS["welcome_text"], min_length=1, max_length=500)
    terms_text: str = Field(default=_DEFAULTS["terms_text"], min_length=1, max_length=5000)
    button_text: str = Field(default="ვეთანხმები და გაგრძელება", min_length=1, max_length=70)
    background_color: str = Field(default="#101827", pattern=r"^#[0-9a-fA-F]{6}$")
    accent_color: str = Field(default="#2878e8", pattern=r"^#[0-9a-fA-F]{6}$")
    session_minutes: int = Field(default=1440, ge=5, le=10080)
    guest_password: str | None = Field(default=None, max_length=128)


@router.get("/config")
def get_config(user: dict = Depends(require_admin)):
    settings = _read_settings()
    return {"settings": _public_settings(settings), "runtime_ready": False}


@router.put("/config")
def update_config(payload: PortalSettings, user: dict = Depends(require_admin)):
    # The editor is being delivered before the captive network enforcement layer.
    # Never report an enabled portal while interception/authorization is not installed.
    if payload.enabled:
        raise HTTPException(
            status_code=409,
            detail="Portal runtime is not installed yet. Settings can be saved, but activation is not available in this build.",
        )

    previous = _read_settings()
    settings = payload.model_dump(exclude={"guest_password"})
    settings["enabled"] = False
    settings["password_hash"] = previous.get("password_hash")

    if payload.guest_password:
        salt = os.urandom(16)
        digest = hashlib.pbkdf2_hmac("sha256", payload.guest_password.encode("utf-8"), salt, 310000)
        settings["password_hash"] = salt.hex() + "$" + digest.hex()

    if payload.auth_mode in ("password", "both") and not settings.get("password_hash"):
        raise HTTPException(status_code=400, detail="ამ რეჟიმისთვის ჯერ სტუმრის პაროლი შეიყვანე.")

    try:
        _write_settings(settings)
    except OSError:
        raise HTTPException(status_code=500, detail="Captive Portal settings could not be saved.")

    return {"success": True, "settings": _public_settings(settings), "runtime_ready": False}


@router.get("/public/config")
def get_public_config():
    settings = _read_settings()
    return _public_settings(settings)
