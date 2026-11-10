"""Captive Portal configuration and live router integration API."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import subprocess
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


def _router_action(action: str) -> dict:
    try:
        command = ["/usr/local/sbin/keyland-routerctl"]
        if os.geteuid() != 0:
            command = ["sudo", "-n", *command]

        proc = subprocess.run(
            command,
            input=json.dumps({"action": action, "payload": {}}),
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=503, detail=f"Router helper unavailable: {exc}")
    try:
        result = json.loads(proc.stdout)
    except (ValueError, TypeError):
        raise HTTPException(status_code=503, detail="Router helper returned invalid data.")
    if not isinstance(result, dict):
        raise HTTPException(status_code=503, detail="Router helper returned an invalid response.")
    return result


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
    guest_password: str | None = Field(default=None, min_length=8, max_length=128)


@router.get("/config")
def get_config(user: dict = Depends(require_admin)):
    settings = _read_settings()
    runtime = _router_action("captive_status")
    settings["enabled"] = bool(runtime.get("enabled"))
    return {
        "settings": _public_settings(settings),
        "runtime_ready": bool(runtime.get("runtime_ready")),
        "runtime": runtime,
    }


@router.put("/config")
def update_config(payload: PortalSettings, user: dict = Depends(require_admin)):
    previous = _read_settings()
    runtime = _router_action("captive_status")
    settings = payload.model_dump(exclude={"guest_password"})
    # Saving the editor must never toggle the live network state.
    settings["enabled"] = bool(runtime.get("enabled"))
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

    return {
        "success": True,
        "settings": _public_settings(settings),
        "runtime_ready": bool(runtime.get("runtime_ready")),
        "runtime": runtime,
    }


@router.post("/enable")
def enable_portal(user: dict = Depends(require_admin)):
    result = _router_action("captive_enable")
    if not result.get("success"):
        raise HTTPException(status_code=409, detail=result.get("error", "Captive Portal could not be enabled."))
    settings = _read_settings()
    settings["enabled"] = True
    try:
        _write_settings(settings)
    except OSError as exc:
        _router_action("captive_disable")
        raise HTTPException(status_code=500, detail=f"Portal was rolled back because settings could not be saved: {exc}")
    return {"success": True, "enabled": True, "runtime_ready": True, "runtime": result}


@router.post("/disable")
def disable_portal(user: dict = Depends(require_admin)):
    result = _router_action("captive_disable")
    if not result.get("success"):
        raise HTTPException(status_code=503, detail=result.get("error", "Captive Portal could not be disabled."))
    settings = _read_settings()
    settings["enabled"] = False
    try:
        _write_settings(settings)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Portal firewall is disabled, but settings could not be saved: {exc}")
    return {"success": True, "enabled": False, "runtime_ready": bool(_router_action("captive_status").get("runtime_ready")), "runtime": result}


@router.get("/public/config")
def get_public_config():
    settings = _read_settings()
    return _public_settings(settings)
