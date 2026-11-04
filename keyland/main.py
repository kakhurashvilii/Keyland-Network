from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from keyland.detector import (
    detect_device,
    detect_capabilities,
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
)


VERSION = "0.1.2"
ROOT = Path(__file__).resolve().parent.parent

app = FastAPI(
    title="Keyland-Network",
    description="Universal Network Management Platform",
    version=VERSION,
)


@app.get("/")
def dashboard():
    return FileResponse(ROOT / "web" / "index.html")


@app.get("/api/v1/health")
def health():
    return {
        "status": "online",
        "project": "Keyland-Network",
        "version": VERSION,
    }


@app.get("/api/v1/device")
def device():
    return detect_device()


@app.get("/api/v1/capabilities")
def capabilities():
    return detect_capabilities()


@app.get("/api/v1/network/interfaces")
def interfaces(active_only: bool = False):
    return get_interfaces(active_only=active_only)


@app.get("/api/v1/network/routes")
def routes():
    return get_routes()


@app.get("/api/v1/wifi")
def wifi():
    return get_wifi()


@app.get("/api/v1/services")
def services():
    return get_services()


@app.get("/api/v1/network/traffic")
def traffic():
    return get_traffic()


@app.get("/api/v1/network/traffic/history")
def traffic_history():
    return get_traffic_history()


@app.get("/api/v1/network/internet")
def internet():
    return get_internet_status()


@app.get("/api/v1/wifi/scan")
def wifi_scan():
    return get_wifi_scan()


@app.get("/api/v1/system/resources")
def system_resources():
    return get_system_resources()


@app.get("/api/v1/monitoring")
def monitoring():
    return {
        "device": detect_device(),
        "resources": get_system_resources(),
        "interfaces": get_interfaces(),
        "traffic": get_traffic(),
        "internet": get_internet_status(),
    }