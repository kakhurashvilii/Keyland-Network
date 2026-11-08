"""SIM SMS inbox and sending through ModemManager (mmcli).

This module never changes modem registration, APN, bearer, routing or radio state.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any

from keyland.adapters.cellular.monitor import get_cellular_status


def _run(args: list[str], timeout: int = 25) -> dict[str, Any]:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, errors="replace",
            timeout=timeout, check=False,
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except FileNotFoundError:
        return {"success": False, "stdout": "", "stderr": "mmcli not found"}
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": "ModemManager request timed out"}
    except OSError as exc:
        return {"success": False, "stdout": "", "stderr": str(exc)}


def _modem_id() -> str:
    if shutil.which("mmcli") is None:
        raise RuntimeError("ModemManager (mmcli) is not installed.")
    status = get_cellular_status()
    modems = status.get("modems") or []
    modem = next((item for item in modems if item.get("available")), None)
    if not modem or modem.get("modem_id") is None:
        raise RuntimeError("No available SIM modem was detected.")
    modem_id = str(modem["modem_id"])
    if not modem_id.isdigit():
        raise RuntimeError("Invalid modem identifier.")
    return modem_id


def _find(data: Any, keys: set[str]) -> Any:
    if isinstance(data, dict):
        for key, value in data.items():
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            if normalized in keys:
                if isinstance(value, dict) and "value" in value:
                    return value["value"]
                return value
        for value in data.values():
            found = _find(value, keys)
            if found is not None:
                return found
    elif isinstance(data, list):
        for value in data:
            found = _find(value, keys)
            if found is not None:
                return found
    return None


def _sms_detail(sms_id: str, listing_state: str = "") -> dict[str, Any]:
    result = _run(["mmcli", "--output-json", "-s", sms_id], timeout=15)
    if not result["success"]:
        result = _run(["mmcli", "-s", sms_id], timeout=15)
        raw = result["stdout"]
        def field(name: str) -> str | None:
            match = re.search(r"^\s*\|\s*" + re.escape(name) + r"\s*:\s*(.*?)\s*$", raw, re.I | re.M)
            return match.group(1) if match else None
        number, body = field("number"), field("text")
        timestamp, state, pdu = field("timestamp"), field("state"), field("PDU type")
    else:
        try:
            payload = json.loads(result["stdout"])
        except (json.JSONDecodeError, TypeError):
            payload = {}
        number = _find(payload, {"number", "sender", "recipient"})
        body = _find(payload, {"text", "message"})
        timestamp = _find(payload, {"timestamp", "receivedtimestamp"})
        state = _find(payload, {"state"})
        pdu = _find(payload, {"pdutype", "type"})

    pdu_text = str(pdu or "").lower()
    state_text = str(state or listing_state or "").lower()
    direction = "received" if ("deliver" in pdu_text or "received" in state_text or "received" in listing_state.lower()) else "sent"
    return {
        "id": sms_id,
        "number": str(number or "Unknown"),
        "text": str(body or ""),
        "timestamp": str(timestamp or ""),
        "state": str(state or listing_state or "unknown"),
        "direction": direction,
    }


def list_sms_messages(limit: int = 100) -> dict[str, Any]:
    modem_id = _modem_id()
    status = _run(["mmcli", "-m", modem_id, "--messaging-status"], timeout=15)
    if not status["success"]:
        raise RuntimeError(status["stderr"] or "SMS messaging is not available on this modem.")
    listing = _run(["mmcli", "-m", modem_id, "--messaging-list-sms"], timeout=25)
    if not listing["success"]:
        raise RuntimeError(listing["stderr"] or "Could not read SIM messages.")
    matches = re.findall(r"/SMS/(\d+)\s*\(([^)]*)\)", listing["stdout"])
    messages = [_sms_detail(sms_id, label) for sms_id, label in matches[:max(1, min(limit, 100))]]
    messages.sort(key=lambda item: (item.get("timestamp") or "", int(item["id"])), reverse=True)
    return {"available": True, "modem_id": modem_id, "count": len(messages), "messages": messages}


def send_sms_message(number: str, message: str) -> dict[str, Any]:
    modem_id = _modem_id()
    cleaned_number = re.sub(r"[\s()\-]", "", number)
    if not re.fullmatch(r"\+?[0-9]{6,20}", cleaned_number):
        raise ValueError("Enter a valid phone number, including country code when possible.")
    if not message or not message.strip():
        raise ValueError("SMS message cannot be empty.")
    if len(message) > 1600:
        raise ValueError("SMS text is too long (maximum 1600 characters).")

    def quote(value: str) -> str:
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"

    specification = "number=" + quote(cleaned_number) + ",text=" + quote(message)
    created = _run([
        "mmcli", "-m", modem_id,
        "--messaging-create-sms=" + specification,
    ], timeout=30)
    if not created["success"]:
        raise RuntimeError(created["stderr"] or "Could not create SMS.")
    match = re.search(r"/SMS/(\d+)", created["stdout"])
    if not match:
        raise RuntimeError("ModemManager created a message but did not return its identifier.")
    sms_id = match.group(1)
    sent = _run(["mmcli", "-s", sms_id, "--send"], timeout=60)
    if not sent["success"]:
        raise RuntimeError(
            (sent["stderr"] or "SMS send failed.") + " Message ID: " + sms_id
        )
    return {
        "success": True,
        "sms_id": sms_id,
        "number": cleaned_number,
        "message": "SMS was accepted by ModemManager for sending.",
    }
