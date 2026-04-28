"""Shared bootstrap for Netlify Functions.

Each Function is its own Lambda. They share this module to:
  - point SQLite at /tmp (the only writable dir in Lambda; survives across
    warm invocations of the same instance, re-seeds cleanly on cold start)
  - turn on demo mode + the BCSS-flavored auto-seed
  - provide a uniform JSON response shape with proper CORS headers

The first import in each cold start runs `boot()`, which initializes the
SQLite schema and seeds the BCSS demo tenant if it isn't present yet.
This is idempotent — warm starts skip straight past the seed.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

# Make the bundled npo_agent source tree importable. Netlify's Zip-It-And-
# Ship-It packs files declared in netlify.toml's `included_files` into
# /var/task/<repo-relative-path>. The function file itself lives at
# /var/task/netlify/functions/_shared.py, so the project root is two parents
# up; src/ sits alongside it.
_HERE = Path(__file__).resolve().parent
for candidate in (_HERE.parent.parent / "src", _HERE / "src", _HERE):
    if (candidate / "npo_agent").is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

# ---- environment must be set BEFORE importing npo_agent ------------------
# (Settings is read at import time via lru_cache; we don't want defaults baked in.)
os.environ.setdefault("NPO_DB_PATH", "/tmp/npo.sqlite")
os.environ.setdefault("NPO_DEMO_MODE", "1")
os.environ.setdefault("NPO_DATA_RESIDENCY", "ca-central")

_BOOTED = False


def boot() -> None:
    """Initialize DB + seed the demo tenant once per Lambda instance."""
    global _BOOTED
    if _BOOTED:
        return
    from npo_agent import db, demo

    db.init_db()
    demo.ensure_demo_tenant()
    _BOOTED = True


# ---- response helpers ----------------------------------------------------

# CORS so the Netlify static site can call the function from any origin.
# In a tighter setup you'd pin Access-Control-Allow-Origin to your site's URL.
_CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


def ok(body: Any) -> dict:
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json", **_CORS},
        "body": json.dumps(body),
    }


def err(status: int, message: str) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", **_CORS},
        "body": json.dumps({"error": message}),
    }


def cors_preflight() -> dict:
    return {"statusCode": 204, "headers": _CORS, "body": ""}


def parse_json(event: dict) -> dict:
    """Read the request body whether the runtime decoded it or not."""
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64

        raw = base64.b64decode(raw).decode("utf-8")
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return {}


def safe_handler(handler):
    """Wrap a handler with CORS + uniform error reporting."""

    def wrapped(event, context):
        if event.get("httpMethod") == "OPTIONS":
            return cors_preflight()
        try:
            boot()
            return handler(event, context)
        except Exception as exc:  # pragma: no cover - error path
            traceback.print_exc()
            return err(500, f"{type(exc).__name__}: {exc}")

    return wrapped
