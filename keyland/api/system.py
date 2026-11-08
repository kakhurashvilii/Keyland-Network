"""Authenticated system power and safe Keyland configuration recovery API."""
from __future__ import annotations

import json
import os
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from keyland.auth import require_admin

router = APIRouter(prefix="/api/v1/system", tags=["System"])

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
HOTSPOT_SETTINGS = DATA_DIR / "hotspot-settings.json"
BACKUP_DIR = DATA_DIR / "recovery-backups"
AUDIT_LOG = DATA_DIR / "system-actions.jsonl"


class ConfirmRequest(BaseModel):
    confirmed: bool = False
    confirmation_text: str = ""


class FactoryResetRequest(ConfirmRequest):
    scope: str = "keyland"


def _audit(action: str, result: str, user: dict, details: dict | None = None) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "result": result,
        "admin": user.get("username", "unknown"),
        "details": details or {},
    }
    fd = os.open(AUDIT_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def _require_confirmation(payload: ConfirmRequest, phrase: str) -> None:
    if payload.confirmed is not True or payload.confirmation_text != phrase:
        raise HTTPException(
            status_code=400,
            detail=f"Explicit confirmation required. Type: {phrase}",
        )


@router.get("/power/status")
def power_status(user: dict = Depends(require_admin)):
    service = subprocess.run(
        ["systemctl", "is-active", "keyland"],
        capture_output=True, text=True, timeout=5, check=False,
    )
    return {
        "service": "keyland",
        "service_state": service.stdout.strip() or "unknown",
        "device_reboot_supported": Path("/run/systemd/system").exists(),
        "warning": "Device reboot restarts the complete OnePlus/Kali system, not only the Kali userspace.",
    }


@router.post("/power/restart-keyland")
def restart_keyland(payload: ConfirmRequest, user: dict = Depends(require_admin)):
    _require_confirmation(payload, "RESTART KEYLAND")
    if not Path("/run/systemd/system").exists():
        raise HTTPException(status_code=503, detail="systemd is not available.")
    try:
        subprocess.Popen(
            ["systemctl", "restart", "keyland"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except OSError as exc:
        _audit("restart-keyland", "failed", user, {"error": str(exc)})
        raise HTTPException(status_code=503, detail="Could not schedule Keyland restart.")
    _audit("restart-keyland", "scheduled", user)
    return {"success": True, "scheduled": True, "message": "Keyland service restart scheduled."}


@router.post("/power/reboot-device")
def reboot_device(payload: ConfirmRequest, user: dict = Depends(require_admin)):
    _require_confirmation(payload, "REBOOT DEVICE")
    if not Path("/run/systemd/system").exists():
        raise HTTPException(status_code=503, detail="systemd is not available.")
    try:
        subprocess.Popen(
            ["systemctl", "reboot"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except OSError as exc:
        _audit("reboot-device", "failed", user, {"error": str(exc)})
        raise HTTPException(status_code=503, detail="Could not schedule device reboot.")
    _audit("reboot-device", "scheduled", user)
    return {
        "success": True, "scheduled": True,
        "message": "Full device reboot scheduled; all services and network connections will stop.",
    }


@router.post("/factory-reset")
def factory_reset(payload: FactoryResetRequest, user: dict = Depends(require_admin)):
    _require_confirmation(payload, "RESET KEYLAND")
    if payload.scope not in {"network", "keyland"}:
        raise HTTPException(status_code=400, detail="Scope must be network or keyland.")

    # Deliberately narrow reset boundary: preserve source code, database/admin,
    # OS, service files and unrelated user data. Only known Keyland runtime
    # configuration is removed after a verified backup.
    targets = [HOTSPOT_SETTINGS]
    existing = [path for path in targets if path.is_file()]
    if not existing:
        return {
            "success": True, "reset": False,
            "message": "No supported saved network settings were present; nothing was changed.",
        }

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = BACKUP_DIR / f"factory-reset-{stamp}.tar.gz"
    try:
        with tarfile.open(backup_path, "w:gz") as archive:
            for path in existing:
                archive.add(path, arcname=str(path.relative_to(ROOT)))
        if not tarfile.is_file(backup_path) or backup_path.stat().st_size == 0:
            raise OSError("Backup archive verification failed.")
        with tarfile.open(backup_path, "r:gz") as archive:
            if not archive.getmembers():
                raise OSError("Backup archive is empty.")
        os.chmod(backup_path, 0o600)
        for path in existing:
            path.unlink()
    except (OSError, tarfile.TarError) as exc:
        try:
            backup_path.unlink(missing_ok=True)
        except OSError:
            pass
        _audit("factory-reset", "failed", user, {"error": str(exc), "scope": payload.scope})
        raise HTTPException(status_code=500, detail="Backup or reset failed; settings were not intentionally removed.")

    _audit(
        "factory-reset", "completed", user,
        {"scope": payload.scope, "backup": str(backup_path.relative_to(ROOT)),
         "removed": [str(path.relative_to(ROOT)) for path in existing]},
    )
    return {
        "success": True, "reset": True, "scope": payload.scope,
        "backup": str(backup_path.relative_to(ROOT)),
        "removed": [str(path.relative_to(ROOT)) for path in existing],
        "preserved": ["administrator database", "Keyland source code", "Kali Linux/Android", "other data"],
    }
