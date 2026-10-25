"""Tiny persistent key-value cache backed by SQLite.

Raw SerpApi responses are stored forever (keyed by engine + params, never the
API key), so a building only costs credits the first time anyone inspects it.
"""

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


def make_key(namespace: str, params: dict[str, Any]) -> str:
    clean = {k: v for k, v in params.items() if k != "api_key" and v is not None}
    blob = json.dumps(clean, sort_keys=True, separators=(",", ":"))
    return f"{namespace}:{hashlib.sha256(blob.encode()).hexdigest()}"


class Cache:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, created REAL NOT NULL)"
            )
            self._conn.commit()

    def get(self, key: str) -> Any | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def scan(self, prefix: str) -> list[Any]:
        """Every value whose key starts with `prefix`."""
        pattern = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self._lock:
            rows = self._conn.execute("SELECT value FROM kv WHERE key LIKE ? ESCAPE '\\'", (pattern,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO kv (key, value, created) VALUES (?, ?, ?)",
                (key, json.dumps(value), time.time()),
            )
            self._conn.commit()
