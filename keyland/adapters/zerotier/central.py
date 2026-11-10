"""Read-only integration with the ZeroTier Legacy Central API.

The Central API token is read only from the server environment. It is never
returned to callers, included in error messages, or sent to the browser.
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


def valid_network_id(network_id: str) -> bool:
    return bool(_NETWORK_ID_RE.fullmatch(network_id or ""))


def _central_get(path: str) -> dict[str, Any]:
    token = os.getenv(TOKEN_ENV, "").strip()
    if not token:
        return {
            "configured": False,
            "available": False,
            "error": f"{TOKEN_ENV} is not configured",
            "data": None,
        }

    request = Request(
        f"{BASE_URL}{path}",
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/json",
            "User-Agent": "Keyland-Network/1.4",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {
            "configured": True,
            "available": True,
            "error": None,
            "data": payload,
        }
    except HTTPError as exc:
        messages = {
            401: "Central API token was rejected (HTTP 401)",
            403: "Token does not have access to this resource (HTTP 403)",
            404: "Network was not found or is not accessible (HTTP 404)",
            429: "Central API rate limit reached (HTTP 429)",
        }
        return {
            "configured": True,
            "available": False,
            "error": messages.get(exc.code, f"Central API request failed (HTTP {exc.code})"),
            "data": None,
        }
    except URLError:
        return {
            "configured": True,
            "available": False,
            "error": "Could not connect to ZeroTier Central",
            "data": None,
        }
    except (TimeoutError, OSError):
        return {
            "configured": True,
            "available": False,
            "error": "Central API connection timed out or failed",
            "data": None,
        }
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {
            "configured": True,
            "available": False,
            "error": "Central API returned invalid JSON",
            "data": None,
        }


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
        return {
            "configured": True,
            "available": False,
            "error": "Central API returned an unexpected network list",
            "networks": [],
        }
    return {
        "configured": True,
        "available": True,
        "error": None,
        "networks": [
            _network_summary(item)
            for item in payload
            if isinstance(item, dict)
        ],
    }


def get_central_members(network_id: str) -> dict[str, Any]:
    if not valid_network_id(network_id):
        return {
            "configured": bool(os.getenv(TOKEN_ENV, "").strip()),
            "available": False,
            "error": "Invalid ZeroTier network ID",
            "network_id": network_id,
            "members": [],
        }

    result = _central_get(f"/network/{network_id}/member")
    if not result["available"]:
        return {
            "configured": result["configured"],
            "available": False,
            "error": result["error"],
            "network_id": network_id,
            "members": [],
        }

    payload = result["data"]
    if not isinstance(payload, list):
        return {
            "configured": True,
            "available": False,
            "error": "Central API returned an unexpected member list",
            "network_id": network_id,
            "members": [],
        }

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

    return {
        "configured": True,
        "available": True,
        "error": None,
        "network_id": network_id,
        "members": members,
    }
