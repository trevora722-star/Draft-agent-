import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    api_key_hash  TEXT NOT NULL UNIQUE,
    persona       TEXT NOT NULL DEFAULT 'clinical-empathetic',
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS documents (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL,
    namespace   TEXT NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_documents_tenant_ns
    ON documents (tenant_id, namespace);

CREATE TABLE IF NOT EXISTS grant_drafts (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL,
    funder      TEXT NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    metadata    TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

-- ── FitCoach (fitness) tables ──────────────────────────────────────────────
-- One ownership group = one tenant; its physical gyms are locations. Equipment,
-- class schedules, and policies live in the vault keyed to a location namespace
-- (see fitness.location_namespace) so the Coach only ever prescribes gear a
-- site actually has.

CREATE TABLE IF NOT EXISTS locations (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL,
    name        TEXT NOT NULL,
    address     TEXT,
    timezone    TEXT NOT NULL DEFAULT 'America/Vancouver',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_locations_tenant ON locations (tenant_id);

CREATE TABLE IF NOT EXISTS members (
    id                      TEXT PRIMARY KEY,
    tenant_id               TEXT NOT NULL,
    home_location_id        TEXT,
    name                    TEXT NOT NULL,
    email                   TEXT,
    goals                   TEXT,
    experience              TEXT NOT NULL DEFAULT 'beginner',
    injuries                TEXT,
    constraints             TEXT,
    target_visits_per_week  INTEGER NOT NULL DEFAULT 3,
    -- PIPEDA / BC PIPA consent flags. Proactive nudges require consent_contact.
    consent_coaching        INTEGER NOT NULL DEFAULT 0,
    consent_contact         INTEGER NOT NULL DEFAULT 0,
    consent_retention       INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_members_tenant ON members (tenant_id);

CREATE TABLE IF NOT EXISTS programs (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL,
    member_id   TEXT NOT NULL,
    body        TEXT NOT NULL,
    metadata    TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_programs_member ON programs (tenant_id, member_id);

-- The accountability signal. In production this is fed from the club's
-- key-fob / access-control system; for a pilot it's CSV import.
CREATE TABLE IF NOT EXISTS checkin_events (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL,
    member_id   TEXT NOT NULL,
    location_id TEXT,
    ts          TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_checkins_member ON checkin_events (tenant_id, member_id, ts);

CREATE TABLE IF NOT EXISTS nudge_log (
    id            TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL,
    member_id     TEXT NOT NULL,
    channel       TEXT NOT NULL DEFAULT 'app',
    risk_at_send  REAL,
    body          TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_nudges_member ON nudge_log (tenant_id, member_id);

CREATE TABLE IF NOT EXISTS escalations (
    id           TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL,
    member_id    TEXT NOT NULL,
    reason       TEXT NOT NULL,
    detail       TEXT,
    resolved_by  TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_escalations_tenant ON escalations (tenant_id, created_at);
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
