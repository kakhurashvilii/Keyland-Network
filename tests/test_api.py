
#test_api.py
from fastapi.testclient import TestClient

from keyland.main import app


client = TestClient(app)


def test_health():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "online"
    assert response.json()["version"] == "0.1.3"

def test_device():
    response = client.get("/api/v1/device")
    assert response.status_code == 200

    data = response.json()
    assert "platform" in data
    assert "architecture" in data
    assert "hostname" in data


def test_capabilities():
    response = client.get("/api/v1/capabilities")
    assert response.status_code == 200
    assert "commands" in response.json()


def test_interfaces():
    response = client.get("/api/v1/network/interfaces")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_routes():
    response = client.get("/api/v1/network/routes")
    assert response.status_code == 200
    assert "routes" in response.json()


def test_wifi():
    response = client.get("/api/v1/wifi")
    assert response.status_code == 200


def test_services():
    response = client.get("/api/v1/services")
    assert response.status_code == 200


def test_traffic():
    response = client.get("/api/v1/network/traffic")
    assert response.status_code == 200

    data = response.json()
    assert "interfaces" in data
    assert data["unit"] == "Mbps"


def test_system_resources():
    response = client.get("/api/v1/system/resources")
    assert response.status_code == 200

    data = response.json()
    assert "cpu_percent" in data
    assert "memory" in data
    assert "disk" in data


def test_wifi_scan():
    response = client.get("/api/v1/wifi/scan")
    assert response.status_code == 200
    assert "available" in response.json()


def test_monitoring():
    response = client.get("/api/v1/monitoring")
    assert response.status_code == 200

    data = response.json()
    assert "device" in data
    assert "resources" in data
    assert "interfaces" in data
    assert "traffic" in data


def test_traffic_history():
    response = client.get("/api/v1/network/traffic/history")
    assert response.status_code == 200

    data = response.json()
    assert "samples" in data
    assert "count" in data
    assert data["unit"] == "Mbps"


def test_internet_status():
    response = client.get("/api/v1/network/internet")
    assert response.status_code == 200

    data = response.json()
    assert "connected" in data
    assert isinstance(data["connected"], bool)


def test_active_interfaces():
    response = client.get(
        "/api/v1/network/interfaces?active_only=true"
    )
    assert response.status_code == 200

    data = response.json()
    assert isinstance(data, list)

    for interface in data:
        assert interface["is_up"] is True



def test_network_diagnostics():
    response = client.get("/api/v1/network/diagnostics")

    assert response.status_code == 200

    data = response.json()

    assert "platform" in data
    assert "checked_at" in data
    assert "gateway" in data
    assert "gateway_ping" in data
    assert "dns" in data

    assert isinstance(data["gateway"]["available"], bool)
    assert isinstance(data["gateway_ping"]["success"], bool)
    assert isinstance(data["dns"]["success"], bool)