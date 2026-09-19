"""SQLite storage: the current save, finished runs, and diary memories."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "instance" / "game.db"


@contextmanager
def _db():
    """Open a connection; commit on success, roll back on error, always close."""
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    with _db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS save (
                id         INTEGER PRIMARY KEY CHECK (id = 1),
                data       TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS runs (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                days     INTEGER NOT NULL,
                cause    TEXT NOT NULL,
                seed     INTEGER NOT NULL,
                path     TEXT NOT NULL,
                ended_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS memories (
                id          TEXT PRIMARY KEY,
                text        TEXT NOT NULL,
                unlocked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)


# ---- current run ----------------------------------------------------------

def load_save() -> dict | None:
    with _db() as conn:
        row = conn.execute("SELECT data FROM save WHERE id = 1").fetchone()
    return json.loads(row["data"]) if row else None


def write_save(data: dict) -> None:
    with _db() as conn:
        conn.execute(
            """INSERT INTO save (id, data) VALUES (1, ?)
               ON CONFLICT(id) DO UPDATE
               SET data = excluded.data, updated_at = CURRENT_TIMESTAMP""",
            (json.dumps(data),),
        )


def delete_save() -> None:
    with _db() as conn:
        conn.execute("DELETE FROM save WHERE id = 1")


# ---- finished runs ----------------------------------------------------------

def record_run(days: int, cause: str, seed: int, path: list[str]) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT INTO runs (days, cause, seed, path) VALUES (?, ?, ?, ?)",
            (days, cause, seed, json.dumps(path)),
        )


def best_days() -> int:
    with _db() as conn:
        row = conn.execute("SELECT MAX(days) AS best FROM runs").fetchone()
    return row["best"] or 0


def run_count() -> int:
    with _db() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]


def scene_history(last_runs: int = 3) -> dict[str, int]:
    """How many of the last few runs visited each scene. The engine uses this
    to lower the odds of drawing the same scenes again."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT path FROM runs ORDER BY id DESC LIMIT ?", (last_runs,)
        ).fetchall()
    counts: Counter = Counter()
    for row in rows:
        counts.update(set(json.loads(row["path"])))   # once per run
    return dict(counts)


# ---- diary memories (survive death) -------------------------------------------

def add_memory(memory_id: str, text: str) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO memories (id, text) VALUES (?, ?)", (memory_id, text)
        )


def load_memories() -> dict[str, str]:
    with _db() as conn:
        rows = conn.execute("SELECT id, text FROM memories ORDER BY unlocked_at, id").fetchall()
    return {row["id"]: row["text"] for row in rows}
