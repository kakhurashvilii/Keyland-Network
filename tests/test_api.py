import os
import tempfile
from pathlib import Path

# იზოლირებული სატესტო ბაზა
_test_dir = tempfile.TemporaryDirectory()
os.environ["KEYLAND_DB_PATH"] = str(Path(_test_dir.name) / "test.db")
os.environ["KEYLAND_JWT_SECRET"] = "test-only-secret-key-never-use-in-production-123456"

from fastapi.testclient import TestClient

from keyland.main import app
from keyland.database import create_user
from keyland.auth import hash_password


unauth_client = TestClient(app)

# სატესტო ადმინისტრატორი
ADMIN_USERNAME = "testadmin"
ADMIN_PASSWORD = "TestAdminPassword123"

create_user(
    username=ADMIN_USERNAME,
    password_hash=hash_password(ADMIN_PASSWORD),
    role="admin",
)

# Admin-ის Token
login_response = unauth_client.post(
    "/api/v1/auth/token",
    data={
        "username": ADMIN_USERNAME,
        "password": ADMIN_PASSWORD,
    },
)

assert login_response.status_code == 200
ADMIN_TOKEN = login_response.json()["access_token"]

client = TestClient(
    app,
    headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
)


def test_health():
    response = unauth_client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "online"
    assert response.json()["version"] == "1.5.0"
    assert response.json()["api"] == "v1"
    assert response.json()["management"]["dns"] == "/api/v1/network/dns"


def test_sms_endpoints_require_admin():
    assert unauth_client.get("/api/v1/cellular/sms/messages").status_code == 401
    assert unauth_client.delete("/api/v1/cellular/sms/messages/0").status_code == 401


def test_device():
    response = client.get("/api/v1/device")
    assert response.status_code == 200


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
    assert client.get("/api/v1/wifi").status_code == 200


def test_services():
    assert client.get("/api/v1/services").status_code == 200


def test_traffic():
    response = client.get("/api/v1/network/traffic")
    assert response.status_code == 200
    assert "interfaces" in response.json()


def test_system_resources():
    response = client.get("/api/v1/system/resources")
    assert response.status_code == 200
    assert "cpu_percent" in response.json()


def test_system_power_endpoint():
    response = client.get("/api/v1/system/power")
    assert response.status_code == 200
    data = response.json()
    assert "available" in data
    assert "chargers" in data


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
    assert "samples" in response.json()


def test_internet_status():
    response = client.get("/api/v1/network/internet")
    assert response.status_code == 200
    assert isinstance(response.json()["connected"], bool)


def test_active_interfaces():
    response = client.get(
        "/api/v1/network/interfaces?active_only=true"
    )
    assert response.status_code == 200

    for interface in response.json():
        assert interface["is_up"] is True


def test_network_diagnostics():
    response = client.get("/api/v1/network/diagnostics")
    assert response.status_code == 200

    data = response.json()
    assert "gateway" in data
    assert "gateway_ping" in data
    assert "dns" in data


def test_admin_login():
    response = unauth_client.post(
        "/api/v1/auth/token",
        data={
            "username": ADMIN_USERNAME,
            "password": ADMIN_PASSWORD,
        },
    )
    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    assert "access_token" in response.json()


def test_admin_identity():
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 200
    assert response.json()["username"] == ADMIN_USERNAME
    assert response.json()["role"] == "admin"


def test_auth_requires_token():
    response = unauth_client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_registration_disabled():
    response = unauth_client.post(
        "/api/v1/auth/register",
        json={
            "username": "newuser",
            "password": "TestPassword123",
        },
    )
    assert response.status_code == 404


def test_invalid_admin_password():
    response = unauth_client.post(
        "/api/v1/auth/token",
        data={
            "username": ADMIN_USERNAME,
            "password": "WrongPassword123",
        },
    )
    assert response.status_code == 401



def test_regular_user_cannot_access_admin():
    create_user(
        username="regularuser",
        password_hash=hash_password("RegularPassword123"),
        role="user",
    )

    # ჩვეულებრივ მომხმარებელს ადმინისტრატორის Token არ ეძლევა.
    login = unauth_client.post(
        "/api/v1/auth/token",
        data={
            "username": "regularuser",
            "password": "RegularPassword123",
        },
    )

    assert login.status_code == 401

    # Token-ის გარეშე ადმინისტრატორის API მიუწვდომელია.
    response = unauth_client.get("/api/v1/auth/admin-check")
    assert response.status_code == 401


def test_admin_access():
    response = client.get("/api/v1/auth/admin-check")
    assert response.status_code == 200
    assert response.json()["role"] == "admin"


def test_dns_status_requires_admin():
    response = unauth_client.get("/api/v1/network/dns")
    assert response.status_code == 401


def test_dns_status_endpoint():
    response = client.get("/api/v1/network/dns")
    assert response.status_code == 200
    data = response.json()
    assert "available" in data
    assert "persistent" in data


def test_dns_apply_requires_explicit_confirmation():
    response = client.post(
        "/api/v1/network/dns",
        json={
            "interface": "wlan0",
            "servers": ["1.1.1.1"],
            "confirmed": False,
        },
    )
    assert response.status_code == 400


def test_hotspot_capabilities_requires_admin():
    response = unauth_client.get("/api/v1/wifi/hotspot/capabilities")
    assert response.status_code == 401


def test_hotspot_capabilities_endpoint():
    response = client.get("/api/v1/wifi/hotspot/capabilities")
    assert response.status_code == 200
    assert "ap_candidates" in response.json()


def test_hotspot_start_rejects_weak_password():
    response = client.post(
        "/api/v1/wifi/hotspot/start",
        json={
            "interface": "ap0",
            "ssid": "Keyland",
            "password": "short",
            "confirmed": True,
        },
    )
    assert response.status_code == 422


def test_hotspot_virtual_interface_requires_confirmation():
    response = client.post(
        "/api/v1/wifi/hotspot/create-interface",
        json={"interface": "wlan0", "confirmed": False},
    )
    assert response.status_code == 400



def test_zerotier_status_requires_admin():
    response = unauth_client.get("/api/v1/zerotier/status")
    assert response.status_code == 401


def test_zerotier_status_endpoint():
    response = client.get("/api/v1/zerotier/status")
    assert response.status_code == 200
    data = response.json()
    assert "service" in data
    assert "networks" in data
    assert "peers" in data
    assert data["read_only"] is True



def test_zerotier_central_networks_requires_admin():
    response = unauth_client.get("/api/v1/zerotier/central/networks")
    assert response.status_code == 401


def test_zerotier_central_networks_without_token(monkeypatch):
    monkeypatch.delenv("KEYLAND_ZEROTIER_CENTRAL_TOKEN", raising=False)
    response = client.get("/api/v1/zerotier/central/networks")
    assert response.status_code == 200
    data = response.json()
    assert data["configured"] is False
    assert data["available"] is False
    assert data["networks"] == []


def test_zerotier_central_members_rejects_invalid_network_id():
    response = client.get(
        "/api/v1/zerotier/central/networks/not-a-network-id/members"
    )
    assert response.status_code == 400


def test_zerotier_central_members_requires_admin():
    response = unauth_client.get(
        "/api/v1/zerotier/central/networks/9e1948db63b1d491/members"
    )
    assert response.status_code == 401



def test_zerotier_diagnostics_requires_admin():
    assert unauth_client.get("/api/v1/zerotier/diagnostics").status_code == 401


def test_zerotier_join_requires_admin():
    response = unauth_client.post(
        "/api/v1/zerotier/local/join",
        json={"network_id": "9e1948db63b1d491", "confirmed": True,
              "confirmation_text": "JOIN NETWORK 9e1948db63b1d491"},
    )
    assert response.status_code == 401


def test_zerotier_join_requires_exact_confirmation():
    response = client.post(
        "/api/v1/zerotier/local/join",
        json={"network_id": "9e1948db63b1d491", "confirmed": False,
              "confirmation_text": ""},
    )
    assert response.status_code == 400
    assert "Explicit confirmation required" in response.json()["detail"]


def test_zerotier_leave_rejects_invalid_network_id():
    response = client.post(
        "/api/v1/zerotier/local/leave",
        json={"network_id": "not-a-network-id", "confirmed": True,
              "confirmation_text": "LEAVE NETWORK not-a-network-id"},
    )
    assert response.status_code == 422


def test_zerotier_member_authorization_requires_confirmation():
    response = client.post(
        "/api/v1/zerotier/central/networks/9e1948db63b1d491/members/1cc1eac232/authorize",
        json={"confirmed": False, "confirmation_text": ""},
    )
    assert response.status_code == 400


def test_zerotier_network_update_requires_confirmation():
    response = client.post(
        "/api/v1/zerotier/central/networks/9e1948db63b1d491/settings",
        json={"name": "Keyland test", "confirmed": False,
              "confirmation_text": ""},
    )
    assert response.status_code == 400


def test_zerotier_network_update_validates_route():
    response = client.post(
        "/api/v1/zerotier/central/networks/9e1948db63b1d491/settings",
        json={"routes": [{"target": "not-a-cidr"}], "confirmed": True,
              "confirmation_text": "UPDATE NETWORK 9e1948db63b1d491"},
    )
    assert response.status_code == 422
