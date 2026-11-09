from types import SimpleNamespace

from keyland import captive_http


def test_read_settings_uses_defaults_and_ignores_unknown_fields(tmp_path, monkeypatch):
    path = tmp_path / "captive-portal.json"
    path.write_text('{"title":"Guest","password_hash":"salt$digest","unexpected":"value"}')
    monkeypatch.setattr(captive_http, "SETTINGS", path)

    settings = captive_http.read_settings()

    assert settings["title"] == "Guest"
    assert settings["auth_mode"] == "consent"
    assert settings["password_hash"] == "salt$digest"
    assert "unexpected" not in settings


def test_portal_page_escapes_admin_text_and_uses_nonce(tmp_path, monkeypatch):
    path = tmp_path / "captive-portal.json"
    path.write_text(
        '{"title":"<script>alert(1)</script>","welcome_text":"<img src=x>",'
        '"terms_text":"<b>terms</b>","auth_mode":"consent"}'
    )
    monkeypatch.setattr(captive_http, "SETTINGS", path)
    monkeypatch.setattr(captive_http, "NONCES", {})

    handler = SimpleNamespace(client_address=("192.168.88.10", 50000))
    page = captive_http.Handler.page(handler)

    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    assert "&lt;img" in page
    assert 'name="consent"' in page
    assert len(captive_http.NONCES) == 1


def test_guest_authorization_uses_ip_mac_tuple_and_session_timeout(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(captive_http.subprocess, "run", fake_run)

    success, message = captive_http.grant("192.168.88.10", "02:11:22:33:44:55", 90)

    assert success is True
    assert message == "authorized"
    assert calls[0][:6] == [
        "nft", "add", "element", "inet", "keyland_captive", "authorized_clients"
    ]
    assert "192.168.88.10" in calls[0]
    assert "02:11:22:33:44:55" in calls[0]
    assert "90m" in calls[0]
