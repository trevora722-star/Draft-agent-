import json

from fastapi.testclient import TestClient

from wedding_qr import llm
from wedding_qr.api import app


def _approved_analysis(**overrides):
    payload = {
        "is_appropriate": True,
        "is_blurry": False,
        "caption": "Guests dancing under fairy lights",
        "tags": ["dancing"],
        "moment": "dancing",
        "quality_score": 8,
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_full_guest_flow(isolated_settings, sample_jpeg_bytes, monkeypatch):
    monkeypatch.setattr(llm, "analyze_photo", lambda *a, **k: _approved_analysis())

    with TestClient(app) as client:
        created = client.post(
            "/events", json={"couple_names": "Alex & Jordan", "event_date": "2026-09-12"}
        ).json()
        event_id = created["event_id"]
        guest_token = created["guest_token"]
        moderator_token = created["moderator_token"]
        assert "static/index.html" in created["guest_upload_url"]
        assert "static/dashboard.html" in created["dashboard_url"]

        guest_info = client.get(
            f"/events/{event_id}/info", params={"token": guest_token}
        ).json()
        assert guest_info["couple_names"] == "Alex & Jordan"
        assert "guest_upload_url" not in guest_info  # guests don't get moderator-only fields

        mod_info = client.get(
            f"/events/{event_id}/info", params={"token": moderator_token}
        ).json()
        assert mod_info["guest_upload_url"] == created["guest_upload_url"]
        assert mod_info["pending_review_count"] == 0

        bad_info = client.get(f"/events/{event_id}/info", params={"token": "wrong"})
        assert bad_info.status_code == 403

        qr_resp = client.get(f"/events/{event_id}/qr.png")
        assert qr_resp.status_code == 200
        assert qr_resp.headers["content-type"] == "image/png"

        upload_resp = client.post(
            f"/events/{event_id}/photos",
            data={"token": guest_token, "guest_name": "Sam", "caption": "First dance"},
            files={"file": ("photo.jpg", sample_jpeg_bytes, "image/jpeg")},
        )
        assert upload_resp.status_code == 200
        # Guest response never reveals the moderation outcome.
        assert "moderation" not in upload_resp.json()["message"].lower()

        gallery_resp = client.get(f"/events/{event_id}/gallery", params={"token": guest_token})
        gallery = gallery_resp.json()
        assert len(gallery) == 1
        assert gallery[0]["guest_name"] == "Sam"
        assert gallery[0]["moment"] == "dancing"

        thumb_resp = client.get(gallery[0]["thumbnail_url"])
        assert thumb_resp.status_code == 200
        assert thumb_resp.headers["content-type"] == "image/jpeg"

        # Wrong guest token is rejected.
        bad_resp = client.get(f"/events/{event_id}/gallery", params={"token": "wrong"})
        assert bad_resp.status_code == 403

        # Moderator-only endpoints reject the guest token.
        review_bad = client.get(f"/events/{event_id}/review", params={"token": guest_token})
        assert review_bad.status_code == 403
        review_ok = client.get(f"/events/{event_id}/review", params={"token": moderator_token})
        assert review_ok.json() == []


def test_inappropriate_upload_is_hidden_then_can_be_overridden(
    isolated_settings, sample_jpeg_bytes, monkeypatch
):
    monkeypatch.setattr(
        llm, "analyze_photo", lambda *a, **k: _approved_analysis(is_appropriate=False)
    )

    with TestClient(app) as client:
        created = client.post("/events", json={"couple_names": "Robin & Casey"}).json()
        event_id = created["event_id"]
        guest_token = created["guest_token"]
        moderator_token = created["moderator_token"]

        client.post(
            f"/events/{event_id}/photos",
            data={"token": guest_token},
            files={"file": ("photo.jpg", sample_jpeg_bytes, "image/jpeg")},
        )

        # Hidden from the public gallery...
        gallery = client.get(f"/events/{event_id}/gallery", params={"token": guest_token}).json()
        assert gallery == []

        # ...but visible in the moderator review queue, not silently deleted.
        queue = client.get(f"/events/{event_id}/review", params={"token": moderator_token}).json()
        assert len(queue) == 1
        photo_id = queue[0]["id"]

        approve = client.post(
            f"/events/{event_id}/photos/{photo_id}/review",
            params={"token": moderator_token},
            json={"decision": "approve"},
        )
        assert approve.status_code == 200

        gallery_after = client.get(
            f"/events/{event_id}/gallery", params={"token": guest_token}
        ).json()
        assert len(gallery_after) == 1


def test_digest_runs_curation_and_notifier(isolated_settings, sample_jpeg_bytes, monkeypatch):
    monkeypatch.setattr(llm, "analyze_photo", lambda *a, **k: _approved_analysis())

    with TestClient(app) as client:
        created = client.post("/events", json={"couple_names": "Alex & Jordan"}).json()
        event_id = created["event_id"]
        guest_token = created["guest_token"]
        moderator_token = created["moderator_token"]

        client.post(
            f"/events/{event_id}/photos",
            data={"token": guest_token},
            files={"file": ("photo.jpg", sample_jpeg_bytes, "image/jpeg")},
        )

        def fake_curate(prompt):
            assert "quality=8" in prompt
            return json.dumps(
                {"highlight_photo_ids": [], "narrative": "A joyful celebration."}
            )

        monkeypatch.setattr(llm, "curate", fake_curate)

        digest_resp = client.get(f"/events/{event_id}/digest", params={"token": moderator_token})
        assert digest_resp.status_code == 200
        body = digest_resp.json()
        assert "Alex & Jordan" in body["subject"]
        assert "A joyful celebration." in body["body"]
