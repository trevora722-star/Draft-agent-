"""Local JSON storage layer shared by all recovery_tracker modules.

All user data lives in flat JSON files under the package-local ``data/``
directory. Every function accepts an optional ``data_dir`` override so tests
can point at a temporary directory.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = PACKAGE_ROOT / "data"

INCIDENTS_FILE = "incidents.json"
DAILY_LOGS_FILE = "daily_logs.json"


class Incident(BaseModel):
    """One circuit-breaker activation."""

    timestamp: str
    trigger: str
    emotional_state: str
    impulse_passed: bool
    completed_protocol: bool = True
    duration_seconds: int = 600
    notes: str = ""


class DailyLog(BaseModel):
    """One daily check-in entry."""

    timestamp: str
    brain_fog: int = Field(ge=1, le=10)
    stress: int = Field(ge=1, le=10)
    boundaries_kept: bool
    impulses_occurred: bool
    impulse_notes: str = ""
    healthy_dopamine_activities: str = ""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ensure_data_dir(data_dir: Path | None = None) -> Path:
    """Return the data directory, creating it if needed."""
    directory = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def load_records(filename: str, data_dir: Path | None = None) -> list[dict]:
    """Load a list of records; missing, empty, or corrupt files yield []."""
    path = ensure_data_dir(data_dir) / filename
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return []
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def append_record(filename: str, record: dict, data_dir: Path | None = None) -> Path:
    """Append one record atomically (write to temp file, then replace)."""
    directory = ensure_data_dir(data_dir)
    path = directory / filename
    records = load_records(filename, data_dir)
    records.append(record)
    fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(records, fh, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    return path


def save_incident(incident: Incident, data_dir: Path | None = None) -> Path:
    return append_record(INCIDENTS_FILE, incident.model_dump(), data_dir)


def save_daily_log(entry: DailyLog, data_dir: Path | None = None) -> Path:
    return append_record(DAILY_LOGS_FILE, entry.model_dump(), data_dir)


def load_incidents(data_dir: Path | None = None) -> list[Incident]:
    out = []
    for rec in load_records(INCIDENTS_FILE, data_dir):
        try:
            out.append(Incident.model_validate(rec))
        except Exception:
            continue  # skip malformed rows rather than crashing the app
    return out


def load_daily_logs(data_dir: Path | None = None) -> list[DailyLog]:
    out = []
    for rec in load_records(DAILY_LOGS_FILE, data_dir):
        try:
            out.append(DailyLog.model_validate(rec))
        except Exception:
            continue
    return out
