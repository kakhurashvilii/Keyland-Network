import os
import secrets
import hmac
from pathlib import Path

from fastapi import (
    FastAPI,
    Depends,
    HTTPException,
    Request,
    Response,
)
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordRequestForm

from keyland.detector import detect_device, detect_capabilities
from keyland.adapters.wifi.capabilities import get_wifi_capabilities
from keyland.adapters.wifi.repeater import get_repeater_status
from keyland.adapters.cellular.monitor import (
    get_cellular_status,
    get_cellular_signal,
    get_cellular_usage,
)
from keyland.network import (
    get_interfaces,
    get_routes,
    get_wifi,
    get_services,
    get_traffic,
    get_wifi_scan,
    get_system_resources,
    get_traffic_history,
    get_internet_status,
    get_network_diagnostics,
    get_power_status,
)

from keyland.auth import (
    authenticate_user,
    create_access_token,
    get_current_user,
    require_admin,
    ACCESS_TOKEN_MINUTES,
    ACCESS_COOKIE_NAME,
)

from keyland.schemas import TokenResponse
from keyland.initialize import initialize
from keyland.api.dns import router as dns_router
from keyland.api.wifi import router as wifi_hotspot_router
from keyland.api.system import router as system_router
from keyland.api.sms import router as sms_router
from keyland.api.terminal import router as terminal_router


VERSION = "1.0.1"
ROOT = Path(__file__).resolve().parent.parent

COOKIE_SECURE = os.getenv("KEYLAND_COOKIE_SECURE", "false").lower() == "true"
CSRF_COOKIE_NAME = "keyland_csrf"

initialize()

app = FastAPI(
    title="Keyland Network",
    description="OpenWrt-style Network Management Platform",
    version=VERSION,
)

app.include_router(dns_router)
app.include_router(wifi_hotspot_router)
app.include_router(system_router)
app.include_router(sms_router)
app.include_router(terminal_router)


@app.middleware("http")
async def csrf_protection(request: Request, call_next):
    unsafe_methods = {"POST", "PUT", "PATCH", "DELETE"}

    # Cookie-authenticated state-changing requests must provide
    # a matching CSRF cookie and header.
    if (
        request.method in unsafe_methods
        and request.url.path != "/api/v1/auth/login"
        and request.cookies.get(ACCESS_COOKIE_NAME)
    ):
        csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME)
        csrf_header = request.headers.get("X-CSRF-Token")

        if (
            not csrf_cookie
            or not csrf_header
            or not hmac.compare_digest(csrf_cookie, csrf_header)
        ):
            return Response(
                content='{"detail":"CSRF validation failed"}',
                status_code=403,
                media_type="application/json",
            )

    return await call_next(request)


# Public pages

@app.get("/")
def dashboard():
    return FileResponse(
        ROOT / "web" / "index.html",
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
    )


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    favicon_path = ROOT / "web" / "favicon.ico"

    if favicon_path.exists():
        return FileResponse(favicon_path)

    return Response(status_code=204)


@app.get("/api/v1/health")
def health():
    return {
        "status": "online",
        "project": "Keyland-Network",
        "version": VERSION,
        "api": "v1",
        "management": {
            "dns": "/api/v1/network/dns",
            "wifi_hotspot": "/api/v1/wifi/hotspot/capabilities",
        },
    }


# Authentication

@app.post("/api/v1/auth/login")
def login(
    response: Response,
    form: OAuth2PasswordRequestForm = Depends(),
):
    user = authenticate_user(form.username, form.password)

    if not user or user["role"] != "admin":
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password",
        )

    token = create_access_token(user)
    csrf_token = secrets.token_urlsafe(32)

    response.set_cookie(
        key=ACCESS_COOKIE_NAME,
        value=token,
        max_age=ACCESS_TOKEN_MINUTES * 60,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="strict",
        path="/",
    )

    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        max_age=ACCESS_TOKEN_MINUTES * 60,
        httponly=False,
        secure=COOKIE_SECURE,
        samesite="strict",
        path="/",
    )

    return {
        "authenticated": True,
        "username": user["username"],
        "role": user["role"],
        "expires_in": ACCESS_TOKEN_MINUTES * 60,
    }


@app.post("/api/v1/auth/logout")
def logout(response: Response):
    response.delete_cookie(
        key=ACCESS_COOKIE_NAME,
        path="/",
        secure=COOKIE_SECURE,
        httponly=True,
        samesite="strict",
    )

    response.delete_cookie(
        key=CSRF_COOKIE_NAME,
        path="/",
        secure=COOKIE_SECURE,
        httponly=False,
        samesite="strict",
    )

    return {"authenticated": False}


@app.get("/api/v1/auth/me")
def current_user(user: dict = Depends(require_admin)):
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "authenticated": True,
    }


# Bearer-token endpoint retained for API clients / Swagger.
@app.post("/api/v1/auth/token", response_model=TokenResponse)
def issue_api_token(form: OAuth2PasswordRequestForm = Depends()):
    user = authenticate_user(form.username, form.password)

    if not user or user["role"] != "admin":
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password",
        )

    return {
        "access_token": create_access_token(user),
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_MINUTES * 60,
    }


@app.get("/api/v1/auth/admin-check")
def admin_check(user: dict = Depends(require_admin)):
    return {
        "status": "authorized",
        "username": user["username"],
        "role": user["role"],
    }


# Protected router management API

@app.get("/api/v1/device")
def device(user: dict = Depends(require_admin)):
    return detect_device()


@app.get("/api/v1/capabilities")
def capabilities(user: dict = Depends(require_admin)):
    return detect_capabilities()


@app.get("/api/v1/network/interfaces")
def interfaces(
    active_only: bool = False,
    user: dict = Depends(require_admin),
):
    return get_interfaces(active_only=active_only)


@app.get("/api/v1/network/routes")
def routes(user: dict = Depends(require_admin)):
    return get_routes()


@app.get("/api/v1/wifi")
def wifi(user: dict = Depends(require_admin)):
    return get_wifi()


@app.get("/api/v1/services")
def services(user: dict = Depends(require_admin)):
    return get_services()


@app.get("/api/v1/network/traffic")
def traffic(user: dict = Depends(require_admin)):
    return get_traffic()


@app.get("/api/v1/network/traffic/history")
def traffic_history(user: dict = Depends(require_admin)):
    return get_traffic_history()


@app.get("/api/v1/network/internet")
def internet(user: dict = Depends(require_admin)):
    return get_internet_status()

# Cellular / SIM / LTE / 4G / 5G

@app.get("/api/v1/cellular/status")
def cellular_status(user: dict = Depends(require_admin)):
    return get_cellular_status()


@app.get("/api/v1/cellular/signal")
def cellular_signal(user: dict = Depends(require_admin)):
    return get_cellular_signal()


@app.get("/api/v1/cellular/usage")
def cellular_usage(user: dict = Depends(require_admin)):
    return get_cellular_usage()

@app.get("/api/v1/network/diagnostics")
def network_diagnostics(user: dict = Depends(require_admin)):
    return get_network_diagnostics()


@app.get("/api/v1/wifi/scan")
def wifi_scan(user: dict = Depends(require_admin)):
    return get_wifi_scan()

@app.get("/api/v1/wifi/capabilities")
def wifi_capabilities(user: dict = Depends(require_admin)):
    return get_wifi_capabilities()


@app.get("/api/v1/wifi/repeater/status")
def wifi_repeater_status(
    user: dict = Depends(require_admin),
):
    """Read-only AP/client/repeater readiness inspection."""
    return get_repeater_status()

@app.get("/api/v1/system/resources")
def system_resources(user: dict = Depends(require_admin)):
    return get_system_resources()




@app.get("/api/v1/system/power")
def system_power(user: dict = Depends(require_admin)):
    return get_power_status()


@app.get("/api/v1/monitoring")
def monitoring(user: dict = Depends(require_admin)):
    return {
        "device": detect_device(),
        "resources": get_system_resources(),
        "interfaces": get_interfaces(),
        "traffic": get_traffic(),
        "internet": get_internet_status(),
    }