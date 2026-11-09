"""Small HTTP captive-portal listener bound only to the Keyland AP address."""
from __future__ import annotations

import argparse
import html
import hashlib
import ipaddress
import json
import re
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

SETTINGS = Path(__file__).resolve().parents[1] / "data" / "captive-portal.json"
NONCES: dict[str, tuple[str, float]] = {}
NONCE_LOCK = threading.Lock()
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
MAC = re.compile(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}")


def read_settings() -> dict:
    defaults = {
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
    try:
        raw = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    defaults.update({key: value for key, value in raw.items() if key in defaults or key == "password_hash"})
    return defaults


def escape(value: object, default: str = "") -> str:
    return html.escape(str(value if value is not None else default), quote=True)


def client_mac(ip: str, ap: str) -> str | None:
    neighbor = subprocess.run(
        ["ip", "neigh", "show", "to", ip, "dev", ap],
        capture_output=True, text=True, timeout=3, check=False,
    )
    match = re.search(r"\blladdr\s+(" + MAC.pattern + r")\b", neighbor.stdout, re.I)
    if neighbor.returncode != 0 or not match:
        return None
    mac = match.group(1).lower()
    if mac == "ff:ff:ff:ff:ff:ff" or int(mac.split(":")[0], 16) & 1:
        return None

    stations = subprocess.run(
        ["iw", "dev", ap, "station", "dump"],
        capture_output=True, text=True, timeout=4, check=False,
    )
    if stations.returncode:
        return None
    connected = {
        line.split()[1].lower()
        for line in stations.stdout.splitlines()
        if line.strip().startswith("Station ") and len(line.split()) > 1
    }
    return mac if mac in connected else None


def grant(ip: str, mac: str, minutes: int) -> tuple[bool, str]:
    result = subprocess.run(
        ["nft", "add", "element", "inet", "keyland_captive",
         "authorized_clients", "{", ip, ".", mac, "timeout", f"{minutes}m", "}"],
        capture_output=True, text=True, timeout=5, check=False,
    )
    if result.returncode and "File exists" not in (result.stderr or ""):
        return False, (result.stderr or result.stdout or "Firewall rejected the authorization.").strip()
    return True, "authorized"


class PortalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


class Handler(BaseHTTPRequestHandler):
    server_version = "KeylandPortal/1.0"
    sys_version = ""

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def send_page(self, body: str, status: int = 200) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(encoded)

    def page(self, message: str = "") -> str:
        cfg = read_settings()
        bg = str(cfg.get("background_color", "#101827"))
        accent = str(cfg.get("accent_color", "#2878e8"))
        if not COLOR.fullmatch(bg):
            bg = "#101827"
        if not COLOR.fullmatch(accent):
            accent = "#2878e8"
        mode = cfg.get("auth_mode", "consent")
        needs_password = mode in ("password", "both")
        needs_consent = mode in ("consent", "both")
        token = secrets.token_urlsafe(32)
        ip = self.client_address[0]
        with NONCE_LOCK:
            now = time.monotonic()
            for key, (_, expiry) in list(NONCES.items()):
                if expiry < now:
                    NONCES.pop(key, None)
            if len(NONCES) >= 4096:
                oldest = min(NONCES, key=lambda key: NONCES[key][1])
                NONCES.pop(oldest, None)
            NONCES[token] = (ip, now + 600)
        consent = (
            '<label class="check"><input type="checkbox" name="consent" value="yes" required> ვეთანხმები გამოყენების პირობებს</label>'
            if needs_consent else ""
        )
        password = (
            '<label>სტუმრის პაროლი<input type="password" name="password" minlength="8" maxlength="128" autocomplete="current-password" required></label>'
            if needs_password else ""
        )
        error = f'<div class="error" role="alert">{escape(message)}</div>' if message else ""
        return f"""<!doctype html><html lang="ka"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>{escape(cfg.get("title"), "Keyland")}</title>
<style>
:root{{--bg:{bg};--accent:{accent}}}*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:22px;background:var(--bg);color:#eef4ff;font:16px system-ui,sans-serif}}
main{{width:min(100%,430px);padding:28px;border:1px solid #ffffff22;border-radius:20px;background:#ffffff0c;box-shadow:0 20px 70px #0006}}
.brand{{font-size:12px;letter-spacing:.18em;color:#a9c3e8;font-weight:800}}h1{{font-size:27px;margin:22px 0 10px}}p{{line-height:1.65;color:#c5d2e4}}.terms{{max-height:190px;overflow:auto;padding:14px;border-radius:12px;background:#0003;white-space:pre-wrap;font-size:13px;line-height:1.6}}
label{{display:block;margin:17px 0 8px;font-size:14px}}input[type=password]{{display:block;width:100%;margin-top:8px;padding:13px;border:1px solid #ffffff35;border-radius:10px;background:#0b1320;color:white;font:inherit}}
.check{{display:flex;gap:10px;align-items:flex-start}}.check input{{margin-top:5px}}button{{width:100%;margin-top:20px;padding:14px;border:0;border-radius:11px;background:var(--accent);color:white;font-weight:800;font-size:15px}}small{{display:block;text-align:center;color:#91a3bb;margin-top:20px}}.error{{padding:10px;border-radius:9px;background:#7d2525;color:#fff;margin-top:14px}}
</style></head><body><main><div class="brand">{escape(cfg.get("logo_text"), "KEYLAND")}</div>
<h1>{escape(cfg.get("title"), "მოგესალმებით!")}</h1><p>{escape(cfg.get("welcome_text"))}</p>
<div class="terms">{escape(cfg.get("terms_text"))}</div>{error}
<form method="post" action="/authorize"><input type="hidden" name="nonce" value="{token}">
{consent}{password}<button type="submit">{escape(cfg.get("button_text"), "გაგრძელება")}</button></form>
<small>Keyland Network</small></main></body></html>"""

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"keyland-captive-ready")
            return
        self.send_page(self.page())

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/authorize":
            self.send_page(self.page("მოთხოვნა არასწორია."), 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 1 <= length <= 8192:
            self.send_page(self.page("ფორმის მონაცემები არასწორია."), 400)
            return
        raw = self.rfile.read(length).decode("utf-8", "replace")
        form = parse_qs(raw, keep_blank_values=True, max_num_fields=10)
        ip_raw = self.client_address[0]
        try:
            ip = ipaddress.ip_address(ip_raw)
            network = ipaddress.ip_network(self.server.network, strict=False)
            if ip.version != 4 or ip not in network or ip == ipaddress.ip_address(self.server.gateway):
                raise ValueError("outside AP network")
        except ValueError:
            self.send_page(self.page("მოწყობილობის მისამართის დადასტურება ვერ მოხერხდა."), 403)
            return

        nonce = form.get("nonce", [""])[0]
        with NONCE_LOCK:
            saved = NONCES.pop(nonce, None)
        if not saved or saved[0] != ip_raw or saved[1] < time.monotonic():
            self.send_page(self.page("ფორმა ვადაგასულია. თავიდან სცადე."), 403)
            return

        cfg = read_settings()
        mode = cfg.get("auth_mode", "consent")
        if mode in ("consent", "both") and form.get("consent", [""])[0] != "yes":
            self.send_page(self.page("პირობებზე თანხმობა აუცილებელია."), 400)
            return
        if mode in ("password", "both"):
            supplied = form.get("password", [""])[0]
            encoded = str(cfg.get("password_hash") or "")
            try:
                salt_hex, digest_hex = encoded.split("$", 1)
                salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
                actual = hashlib.pbkdf2_hmac("sha256", supplied.encode("utf-8"), salt, 310000)
            except (ValueError, TypeError):
                self.send_page(self.page("სტუმრის პაროლი ჯერ არ არის სწორად დაყენებული."), 503)
                return
            if not secrets.compare_digest(actual, expected):
                self.send_page(self.page("პაროლი არასწორია. სცადე თავიდან."), 401)
                return

        ap = self.server.ap
        mac = client_mac(ip_raw, ap)
        if not mac:
            self.send_page(self.page("მოწყობილობა Wi-Fi ქსელში ვერ დადასტურდა. ხელახლა დაუკავშირდი და სცადე."), 403)
            return
        try:
            minutes = max(5, min(10080, int(cfg.get("session_minutes", 1440))))
        except (ValueError, TypeError):
            minutes = 1440
        ok, detail = grant(ip_raw, mac, minutes)
        if not ok:
            self.send_page(self.page("წვდომის მინიჭება ვერ მოხერხდა: " + detail), 503)
            return
        self.send_response(303)
        self.send_header("Location", "http://example.com/")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--ap", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--gateway", required=True)
    args = parser.parse_args()
    server = PortalServer((args.host, args.port), Handler)
    server.ap = args.ap
    server.network = args.network
    server.gateway = args.gateway
    server.serve_forever(poll_interval=0.5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
