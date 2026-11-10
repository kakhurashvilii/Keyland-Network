"""ZeroTier Legacy Central API integration and network administration.

The API token is read only from the server environment and is never returned
to callers or sent to the browser.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "https://api.zerotier.com/api/v1"
TOKEN_ENV = "KEYLAND_ZEROTIER_CENTRAL_TOKEN"
_NETWORK_ID_RE = re.compile(r"^[0-9a-fA-F]{16}$")
_MEMBER_ID_RE = re.compile(r"^[0-9a-fA-F]{10}$")


def valid_network_id(network_id: str) -> bool:
    return bool(_NETWORK_ID_RE.fullmatch(network_id or ""))


def valid_member_id(member_id: str) -> bool:
    return bool(_MEMBER_ID_RE.fullmatch(member_id or ""))


def _central_request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    token = os.getenv(TOKEN_ENV, "").strip()
    if not token:
        return {"configured": False, "available": False,
                "error": f"{TOKEN_ENV} is not configured", "data": None}

    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"{BASE_URL}{path}",
        data=body,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Keyland-Network/1.5",
        },
        method=method,
    )
    try:
        with urlopen(request, timeout=12) as response:
            raw = response.read()
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
        return {"configured": True, "available": True, "error": None, "data": parsed}
    except HTTPError as exc:
        messages = {
            400: "Central API rejected the request (HTTP 400)",
            401: "Central API token was rejected (HTTP 401)",
            403: "Token does not have permission (HTTP 403)",
            404: "Network or member was not found (HTTP 404)",
            409: "Central API reported a conflict (HTTP 409)",
            429: "Central API rate limit reached (HTTP 429)",
        }
        return {"configured": True, "available": False,
                "error": messages.get(exc.code, f"Central API request failed (HTTP {exc.code})"),
                "data": None}
    except URLError:
        return {"configured": True, "available": False,
                "error": "Could not connect to ZeroTier Central", "data": None}
    except (TimeoutError, OSError):
        return {"configured": True, "available": False,
                "error": "Central API connection timed out or failed", "data": None}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"configured": True, "available": False,
                "error": "Central API returned invalid JSON", "data": None}


def _central_get(path: str) -> dict[str, Any]:
    return _central_request("GET", path)


def _network_summary(item: dict[str, Any]) -> dict[str, Any]:
    config = item.get("config") if isinstance(item.get("config"), dict) else {}
    return {
        "id": item.get("id"),
        "name": config.get("name") or item.get("name") or "",
        "description": config.get("description") or "",
        "private": config.get("private"),
        "total_member_count": item.get("totalMemberCount"),
        "authorized_member_count": item.get("authorizedMemberCount"),
        "creation_time": config.get("creationTime"),
        "ip_assignment_pools": config.get("ipAssignmentPools") or [],
        "routes": config.get("routes") or [],
        "dns": config.get("dns") or {},
    }


def get_central_networks() -> dict[str, Any]:
    result = _central_get("/network")
    if not result["available"]:
        return {**result, "networks": []}
    payload = result["data"]
    if not isinstance(payload, list):
        return {"configured": True, "available": False,
                "error": "Central API returned an unexpected network list", "networks": []}
    return {
        "configured": True, "available": True, "error": None,
        "networks": [_network_summary(item) for item in payload if isinstance(item, dict)],
    }


def get_central_members(network_id: str) -> dict[str, Any]:
    if not valid_network_id(network_id):
        return {"configured": bool(os.getenv(TOKEN_ENV, "").strip()),
                "available": False, "error": "Invalid ZeroTier network ID",
                "network_id": network_id, "members": []}

    result = _central_get(f"/network/{network_id}/member")
    if not result["available"]:
        return {"configured": result["configured"], "available": False,
                "error": result["error"], "network_id": network_id, "members": []}

    payload = result["data"]
    if not isinstance(payload, list):
        return {"configured": True, "available": False,
                "error": "Central API returned an unexpected member list",
                "network_id": network_id, "members": []}

    members = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        config = item.get("config") if isinstance(item.get("config"), dict) else {}
        members.append({
            "id": item.get("id"),
            "name": item.get("name") or config.get("name") or "",
            "description": item.get("description") or config.get("description") or "",
            "authorized": config.get("authorized", item.get("authorized")),
            "physical_address": item.get("physicalAddress"),
            "ip_assignments": item.get("ipAssignments") or config.get("ipAssignments") or [],
            "last_seen": item.get("lastSeen"),
            "last_authorized_time": item.get("lastAuthorizedTime") or config.get("lastAuthorizedTime"),
            "client_version": item.get("clientVersion"),
            "protocol_version": item.get("protocolVersion"),
        })
    return {"configured": True, "available": True, "error": None,
            "network_id": network_id, "members": members}


def set_central_member_authorization(
    network_id: str, member_id: str, authorized: bool
) -> dict[str, Any]:
    if not valid_network_id(network_id) or not valid_member_id(member_id):
        return {"success": False, "error": "Invalid network or member ID"}
    result = _central_request(
        "POST", f"/network/{network_id}/member/{member_id}",
        {"config": {"authorized": bool(authorized)}},
    )
    if not result["available"]:
        return {"success": False, "error": result["error"]}
    return {"success": True, "member": result["data"]}


def update_central_network(network_id: str, config: dict[str, Any]) -> dict[str, Any]:
    if not valid_network_id(network_id):
        return {"success": False, "error": "Invalid ZeroTier network ID"}
    allowed = {"name", "description", "private", "routes", "ipAssignmentPools", "dns"}
    clean = {key: value for key, value in config.items() if key in allowed}
    if not clean:
        return {"success": False, "error": "No supported network settings supplied"}
    result = _central_request("POST", f"/network/{network_id}", {"config": clean})
    if not result["available"]:
        return {"success": False, "error": result["error"]}
    data = result["data"]
    return {"success": True,
            "network": _network_summary(data) if isinstance(data, dict) else data}
