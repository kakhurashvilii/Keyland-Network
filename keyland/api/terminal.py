"""Authenticated full Linux PTY terminal.

The shell runs as the Keyland service account (root on the reference device).
Only the administrator session cookie is accepted. WebSocket origins must match
the Host header to prevent cross-site WebSocket hijacking.
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
import pty
import re
import signal
import struct
import termios
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from keyland.auth import ACCESS_COOKIE_NAME, ALGORITHM, SECRET_KEY
from keyland.database import get_user_by_id

router = APIRouter(tags=["Terminal"])
ROOT = Path(__file__).resolve().parents[2]
AUDIT_FILE = ROOT / "data" / "terminal-audit.jsonl"
IDLE_TIMEOUT = 15 * 60
MAX_SESSION_SECONDS = 25 * 60


def _audit(event: str, username: str, peer: str) -> None:
    """Record session metadata only; never record commands or terminal output."""
    try:
        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "time": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "username": username,
            "peer": peer,
        }
        fd = os.open(str(AUDIT_FILE), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (json.dumps(record, ensure_ascii=False) + "\n").encode())
        finally:
            os.close(fd)
    except OSError:
        # A logging failure must not leak shell data or silently widen access.
        pass


def _authorized_user(websocket: WebSocket):
    token = websocket.cookies.get(ACCESS_COOKIE_NAME)
    if not token:
        return None
    try:
        payload = jwt.decode(
            token, SECRET_KEY, algorithms=[ALGORITHM],
            options={"require": ["sub", "iat", "exp"]},
        )
        user = get_user_by_id(int(payload["sub"]))
        if not user or not user.get("is_active") or user.get("role") != "admin":
            return None
        return user
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
        return None


def _same_origin(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    host = websocket.headers.get("host", "").lower()
    if not origin or not host:
        return False
    try:
        parsed = urlsplit(origin)
        return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == host
    except ValueError:
        return False



async def _read_pty(fd: int) -> bytes:
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def readable():
        if future.done():
            return
        try:
            data = os.read(fd, 8192)
        except OSError:
            data = b""
        future.set_result(data)

    loop.add_reader(fd, readable)
    try:
        return await future
    finally:
        loop.remove_reader(fd)


@router.websocket("/api/v1/terminal/ws")
async def terminal_socket(websocket: WebSocket):
    if not _same_origin(websocket):
        await websocket.close(code=1008, reason="Invalid origin")
        return

    user = _authorized_user(websocket)
    if not user:
        await websocket.close(code=1008, reason="Administrator authentication required")
        return

    await websocket.accept()
    username = str(user.get("username", "admin"))
    peer = websocket.client.host if websocket.client else "unknown"
    _audit("connect", username, peer)

    master_fd = slave_fd = None
    process = None
    started = time.monotonic()
    try:
        master_fd, slave_fd = pty.openpty()
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
        env = os.environ.copy()
        env.update({"TERM": "xterm-256color", "COLORTERM": "truecolor"})
        process = __import__("subprocess").Popen(
            ["/bin/bash", "--login", "-i"],
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            cwd=str(ROOT), env=env, close_fds=True,
            start_new_session=True,
        )
        os.close(slave_fd)
        slave_fd = None
        await websocket.send_text(json.dumps({
            "type": "ready",
            "username": username,
            "cwd": str(ROOT),
            "privilege": "root" if os.geteuid() == 0 else "service-user",
            "max_session_seconds": MAX_SESSION_SECONDS,
            "idle_timeout_seconds": IDLE_TIMEOUT,
        }))

        async def send_output():
            while True:
                data = await _read_pty(master_fd)
                if not data:
                    return
                await websocket.send_text(json.dumps({
                    "type": "output",
                    "data": data.decode("utf-8", errors="replace"),
                }))

        async def receive_input():
            while True:
                raw = await asyncio.wait_for(
                    websocket.receive_text(), timeout=IDLE_TIMEOUT
                )
                if len(raw) > 16384:
                    await websocket.send_text(json.dumps({
                        "type": "error", "message": "Input frame too large"
                    }))
                    continue
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                kind = message.get("type")
                if kind == "input":
                    data = message.get("data", "")
                    if isinstance(data, str):
                        os.write(master_fd, data.encode("utf-8", errors="replace"))
                elif kind == "resize":
                    cols = max(40, min(240, int(message.get("cols", 100))))
                    rows = max(10, min(80, int(message.get("rows", 30))))
                    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
                    os.killpg(process.pid, signal.SIGWINCH)
                elif kind == "close":
                    return

        output_task = asyncio.create_task(send_output())
        input_task = asyncio.create_task(receive_input())
        process_task = asyncio.create_task(asyncio.to_thread(process.wait))
        done, pending = await asyncio.wait(
            {output_task, input_task, process_task},
            timeout=MAX_SESSION_SECONDS,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            await websocket.send_text(json.dumps({
                "type": "error", "message": "Maximum terminal session time reached."
            }))
        elif input_task in done and isinstance(input_task.exception(), asyncio.TimeoutError):
            await websocket.send_text(json.dumps({
                "type": "error", "message": "Terminal disconnected after 15 minutes idle."
            }))
        await asyncio.gather(*done, return_exceptions=True)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGHUP)
            except ProcessLookupError:
                pass
    except asyncio.TimeoutError:
        try:
            await websocket.send_text(json.dumps({
                "type": "error", "message": "Terminal disconnected after 15 minutes idle."
            }))
        except Exception:
            pass
    except WebSocketDisconnect:
        pass
    except (OSError, RuntimeError):
        pass
    finally:
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=3)
            except Exception:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        for fd in (master_fd, slave_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        _audit("disconnect", username, peer)
        try:
            await websocket.close()
        except Exception:
            pass
