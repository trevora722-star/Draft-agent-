"""Test fixtures.

Each test gets a fresh SQLite DB at a tmp path. We drive Settings via env
vars and clear the lru_cache so every module that imported get_settings
sees the new values without per-module monkeypatching.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from npo_agent import config, db


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setenv("NPO_DB_PATH", str(db_path))
    monkeypatch.setenv("NPO_ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-used-in-tests")
    config.get_settings.cache_clear()
    db.init_db(db_path)
    return db_path
