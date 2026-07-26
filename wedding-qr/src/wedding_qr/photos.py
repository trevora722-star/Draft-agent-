from __future__ import annotations

import json
import uuid

from .agents.analysis import PhotoAnalysis
from .db import connect

REVIEW_DECISIONS = {"approve": "approved", "reject": "rejected"}


def create_photo(
    *,
    event_id: str,
    guest_name: str | None,
    guest_caption: str | None,
    storage_path: str,
    thumbnail_path: str,
) -> str:
    photo_id = uuid.uuid4().hex
    with connect() as conn:
        conn.execute(
            """INSERT INTO photos
               (id, event_id, guest_name, guest_caption, storage_path, thumbnail_path, status)
               VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
            (photo_id, event_id, guest_name, guest_caption, storage_path, thumbnail_path),
        )
    return photo_id


def apply_analysis(photo_id: str, analysis: PhotoAnalysis) -> None:
    with connect() as conn:
        conn.execute(
            """UPDATE photos SET
                 status=?, ai_caption=?, ai_tags=?, moment=?, quality_score=?,
                 is_appropriate=?, is_blurry=?
               WHERE id=?""",
            (
                analysis.status,
                analysis.caption,
                json.dumps(analysis.tags),
                analysis.moment,
                analysis.quality_score,
                int(analysis.is_appropriate),
                int(analysis.is_blurry),
                photo_id,
            ),
        )


def _row_to_dict(row) -> dict:
    d = dict(row)
    if d.get("ai_tags"):
        d["ai_tags"] = json.loads(d["ai_tags"])
    else:
        d["ai_tags"] = []
    d["is_appropriate"] = bool(d.get("is_appropriate"))
    d["is_blurry"] = bool(d.get("is_blurry"))
    return d


def get_photo(photo_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_gallery(event_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM photos WHERE event_id = ? AND status = 'approved' "
            "ORDER BY created_at DESC",
            (event_id,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_review_queue(event_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM photos WHERE event_id = ? AND status = 'flagged' "
            "ORDER BY created_at ASC",
            (event_id,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def review_decision(photo_id: str, decision: str) -> None:
    if decision not in REVIEW_DECISIONS:
        raise ValueError(f"decision must be one of {sorted(REVIEW_DECISIONS)}")
    with connect() as conn:
        conn.execute(
            "UPDATE photos SET status = ? WHERE id = ?",
            (REVIEW_DECISIONS[decision], photo_id),
        )


def curation_candidates(event_id: str) -> list[dict]:
    """Approved photos formatted for CurationAgent — metadata only, no image bytes."""
    photos = list_gallery(event_id)
    return [
        {
            "id": p["id"],
            "caption": p["ai_caption"],
            "tags": p["ai_tags"],
            "moment": p["moment"],
            "quality_score": p["quality_score"],
            "guest_name": p["guest_name"],
        }
        for p in photos
    ]
