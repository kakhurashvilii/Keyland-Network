import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager


ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("KEYLAND_DB_PATH", ROOT / "data" / "keyland.db"))


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def get_db():
    connection = get_connection()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user'
                    CHECK(role IN ('admin', 'user')),
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)


def create_user(username: str, password_hash: str, role: str = "user"):
    if role not in ("admin", "user"):
        raise ValueError("Invalid role")

    with get_db() as db:
        cursor = db.execute(
            """
            INSERT INTO users (username, password_hash, role)
            VALUES (?, ?, ?)
            """,
            (username, password_hash, role),
        )
        user_id = cursor.lastrowid

    return get_user_by_id(user_id)


def get_user_by_username(username: str):
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,),
        ).fetchone()

    return dict(row) if row else None


def get_user_by_id(user_id: int):
    with get_db() as db:
        row = db.execute(
            """
            SELECT id, username, role, is_active, created_at
            FROM users WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

    return dict(row) if row else None
