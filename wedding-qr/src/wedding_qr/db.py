from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id                TEXT PRIMARY KEY,
    couple_names      TEXT NOT NULL,
    event_date        TEXT,
    guest_token       TEXT NOT NULL UNIQUE,
    moderator_token   TEXT NOT NULL UNIQUE,
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS photos (
    id                TEXT PRIMARY KEY,
    event_id          TEXT NOT NULL,
    guest_name        TEXT,
    guest_caption     TEXT,
    storage_path      TEXT NOT NULL,
    thumbnail_path    TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending',
    ai_caption        TEXT,
    ai_tags           TEXT,
    moment            TEXT,
    quality_score     INTEGER,
    is_appropriate    INTEGER,
    is_blurry         INTEGER,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_photos_event_status
    ON photos (event_id, status);
"""


def init_db(db_path: Path | None = None) -> None:
    path = db_path or get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    path = db_path or get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
