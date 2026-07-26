import json

from wedding_qr import llm
from wedding_qr.agents.curator import CurationAgent
from wedding_qr.agents.notifier import NotifierAgent


PHOTOS = [
    {"id": "a", "caption": "Ceremony kiss", "tags": ["ceremony"], "moment": "ceremony",
     "quality_score": 9, "guest_name": "Sam"},
    {"id": "b", "caption": "Blurry hallway", "tags": ["candid"], "moment": "reception",
     "quality_score": 2, "guest_name": "Alex"},
    {"id": "c", "caption": "First dance", "tags": ["dancing"], "moment": "dancing",
     "quality_score": 8, "guest_name": None},
]


def test_curator_returns_narrative_and_filters_unknown_ids(monkeypatch):
    monkeypatch.setattr(
        llm,
        "curate",
        lambda prompt: json.dumps(
            {
                # "z" is not in the candidate set and must be dropped.
                "highlight_photo_ids": ["a", "z", "c"],
                "narrative": "A beautiful day from vows to the dance floor.",
            }
        ),
    )
    result = CurationAgent().curate(PHOTOS)
    assert result.highlight_photo_ids == ["a", "c"]
    assert "beautiful day" in result.narrative


def test_curator_prompt_includes_metadata_not_images(monkeypatch):
    captured = {}

    def fake_curate(prompt):
        captured["prompt"] = prompt
        return json.dumps({"highlight_photo_ids": [], "narrative": ""})

    monkeypatch.setattr(llm, "curate", fake_curate)
    CurationAgent().curate(PHOTOS)
    assert "Ceremony kiss" in captured["prompt"]
    assert "quality=9" in captured["prompt"]


def test_curator_short_circuits_on_empty_collection():
    result = CurationAgent().curate([])
    assert result.highlight_photo_ids == []
    assert "No photos" in result.narrative


def test_notifier_composes_digest_with_review_count():
    from wedding_qr.agents.curator import CurationResult

    curation = CurationResult(highlight_photo_ids=["a", "c"], narrative="Lovely day.")
    digest = NotifierAgent().compose(
        couple_names="Alex & Jordan",
        total_photos=3,
        pending_review_count=1,
        curation=curation,
    )
    assert "Alex & Jordan" in digest.subject
    assert "3 photos" in digest.subject
    assert "1 photo(s) need a quick look" in digest.body
    assert "Lovely day." in digest.body
