import json

from wedding_qr import llm
from wedding_qr.agents.analysis import PhotoAnalysisAgent


def test_appropriate_photo_is_approved(monkeypatch):
    monkeypatch.setattr(
        llm,
        "analyze_photo",
        lambda image_bytes, media_type="image/jpeg": json.dumps(
            {
                "is_appropriate": True,
                "is_blurry": False,
                "caption": "First dance under the string lights",
                "tags": ["dancing", "candid"],
                "moment": "dancing",
                "quality_score": 8,
            }
        ),
    )
    analysis = PhotoAnalysisAgent().analyze(b"fake-bytes")
    assert analysis.status == "approved"
    assert analysis.moment == "dancing"
    assert analysis.quality_score == 8


def test_inappropriate_photo_is_flagged_not_rejected(monkeypatch):
    """Auto-moderation must never hard-reject — it flags for human review."""
    monkeypatch.setattr(
        llm,
        "analyze_photo",
        lambda image_bytes, media_type="image/jpeg": json.dumps(
            {
                "is_appropriate": False,
                "is_blurry": False,
                "caption": "N/A",
                "tags": [],
                "moment": "other",
                "quality_score": 3,
            }
        ),
    )
    analysis = PhotoAnalysisAgent().analyze(b"fake-bytes")
    assert analysis.status == "flagged"


def test_blurry_photo_is_still_approved_just_low_quality(monkeypatch):
    """Blur affects curation ranking, never gallery visibility."""
    monkeypatch.setattr(
        llm,
        "analyze_photo",
        lambda image_bytes, media_type="image/jpeg": json.dumps(
            {
                "is_appropriate": True,
                "is_blurry": True,
                "caption": "Blurry candid",
                "tags": ["candid"],
                "moment": "reception",
                "quality_score": 2,
            }
        ),
    )
    analysis = PhotoAnalysisAgent().analyze(b"fake-bytes")
    assert analysis.status == "approved"
    assert analysis.is_blurry is True
    assert analysis.quality_score == 2


def test_handles_markdown_fenced_json(monkeypatch):
    monkeypatch.setattr(
        llm,
        "analyze_photo",
        lambda image_bytes, media_type="image/jpeg": "```json\n"
        + json.dumps(
            {
                "is_appropriate": True,
                "is_blurry": False,
                "caption": "Cake cutting",
                "tags": ["cake"],
                "moment": "reception",
                "quality_score": 7,
            }
        )
        + "\n```",
    )
    analysis = PhotoAnalysisAgent().analyze(b"fake-bytes")
    assert analysis.caption == "Cake cutting"


def test_unknown_moment_falls_back_to_other(monkeypatch):
    monkeypatch.setattr(
        llm,
        "analyze_photo",
        lambda image_bytes, media_type="image/jpeg": json.dumps(
            {
                "is_appropriate": True,
                "is_blurry": False,
                "caption": "x",
                "tags": [],
                "moment": "afterparty",
                "quality_score": 5,
            }
        ),
    )
    analysis = PhotoAnalysisAgent().analyze(b"fake-bytes")
    assert analysis.moment == "other"


def test_quality_score_is_clamped(monkeypatch):
    monkeypatch.setattr(
        llm,
        "analyze_photo",
        lambda image_bytes, media_type="image/jpeg": json.dumps(
            {
                "is_appropriate": True,
                "is_blurry": False,
                "caption": "x",
                "tags": [],
                "moment": "other",
                "quality_score": 99,
            }
        ),
    )
    analysis = PhotoAnalysisAgent().analyze(b"fake-bytes")
    assert analysis.quality_score == 10
