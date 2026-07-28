"""Tests for the ConfettiRoll multi-tenant platform.

Run from this directory:  python -m pytest test_app.py
"""

import io

import pytest
from PIL import Image

BASE = "http://confettiroll.test"
EVENT = "http://anna-and-james.confettiroll.test"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CR_BASE_DOMAIN", "confettiroll.test")
    import app as app_module
    from fastapi.testclient import TestClient

    return TestClient(app_module.create_app(), base_url=BASE)


def _signup(client, email="host@example.com"):
    return client.post(
        f"{BASE}/signup",
        data={"name": "Sam Host", "email": email, "password": "hunter2hunter2"},
        follow_redirects=False,
    )


def _create_event(client, slug="anna-and-james", **overrides):
    data = {
        "title": "Anna & James's Wedding",
        "slug": slug,
        "guest_password": "cake123",
        "event_date": "",
        "custom_domain": "",
    }
    data.update(overrides)
    return client.post(f"{BASE}/api/events", data=data, follow_redirects=False)


def _fake_jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), color=(200, 130, 100)).save(buf, "JPEG")
    return buf.getvalue()


def test_signup_login_and_dashboard(client):
    res = _signup(client)
    assert res.status_code == 303 and res.headers["location"] == "/dashboard"
    assert client.get(f"{BASE}/dashboard").status_code == 200

    # sign out, wrong then right password
    client.post(f"{BASE}/logout", follow_redirects=False)
    res = client.post(f"{BASE}/login", data={"email": "host@example.com", "password": "wrong"})
    assert "Wrong email or password" in res.text
    res = client.post(
        f"{BASE}/login",
        data={"email": "host@example.com", "password": "hunter2hunter2"},
        follow_redirects=False,
    )
    assert res.status_code == 303

    # duplicate email rejected
    res = _signup(client)
    assert "already exists" in res.text


def test_event_creation_and_validation(client):
    _signup(client)
    assert _create_event(client).status_code == 303
    assert "anna-and-james.confettiroll.test" in client.get(f"{BASE}/dashboard").text

    # duplicate slug, reserved slug, bad slug
    assert "already+taken" in _create_event(client).headers["location"]
    assert "error" in _create_event(client, slug="www").headers["location"]
    assert "error" in _create_event(client, slug="Bad Slug!").headers["location"]


def test_guest_flow_and_owner_admin(client, tmp_path):
    _signup(client)
    _create_event(client)

    # unknown subdomain 404s the API
    assert client.get("http://nope.confettiroll.test/api/photos").status_code == 404

    # owner is admin on the event host without a guest login (org cookie
    # is scoped to .confettiroll.test so it rides along to subdomains)
    listing = client.get(f"{EVENT}/api/photos")
    assert listing.status_code == 200
    assert listing.json()["is_admin"] is True

    # a fresh guest must log in with the event password
    client.cookies.clear()
    assert client.get(f"{EVENT}/", follow_redirects=False).status_code == 303
    res = client.post(f"{EVENT}/login", data={"password": "wrong"})
    assert "isn't right" in res.text
    res = client.post(f"{EVENT}/login", data={"password": "cake123"}, follow_redirects=False)
    assert res.status_code == 303

    # guest uploads a photo and a video
    res = client.post(
        f"{EVENT}/api/upload",
        files=[
            ("files", ("dance.jpg", _fake_jpeg(), "image/jpeg")),
            ("files", ("toast.mp4", b"\x00" * 1024, "video/mp4")),
        ],
        data={"uploader": "Uncle Bob"},
    )
    body = res.json()
    assert len(body["saved"]) == 2 and body["errors"] == []
    photo_id = next(p["id"] for p in body["saved"] if p["type"] == "photo")

    listing = client.get(f"{EVENT}/api/photos").json()
    assert len(listing["photos"]) == 2
    assert listing["is_admin"] is False

    # media serving works, and the other event's guests can't reach it
    assert client.get(f"{EVENT}/photos/{photo_id}").status_code == 200
    assert client.get(f"{EVENT}/thumbs/{photo_id}").status_code == 200

    # guest cannot delete; owner can
    assert client.delete(f"{EVENT}/api/photos/{photo_id}").status_code == 403
    client.post(
        f"{BASE}/login",
        data={"email": "host@example.com", "password": "hunter2hunter2"},
        follow_redirects=False,
    )
    assert client.delete(f"{EVENT}/api/photos/{photo_id}").status_code == 200
    event_id = None
    for d in (tmp_path / "events").iterdir():
        if (d / "trash" / "photos").exists():
            event_id = d.name
    assert event_id is not None


def test_guest_session_is_scoped_to_its_event(client):
    _signup(client)
    _create_event(client, slug="anna-and-james")
    _create_event(client, slug="smith-reunion", guest_password="beer456")
    client.cookies.clear()

    client.post(f"{EVENT}/login", data={"password": "cake123"}, follow_redirects=False)
    assert client.get(f"{EVENT}/api/photos").status_code == 200
    # the same cookie must not unlock a different event
    assert client.get("http://smith-reunion.confettiroll.test/api/photos").status_code == 401


def test_custom_domain_routing(client):
    _signup(client)
    _create_event(client, slug="gala", guest_password="fete789",
                  custom_domain="photos.smithwedding.test")
    client.cookies.clear()
    res = client.post(
        "http://photos.smithwedding.test/login",
        data={"password": "fete789"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert client.get("http://photos.smithwedding.test/api/photos").status_code == 200


def test_referral_attribution(client):
    import re as _re
    _signup(client, email="planner@example.com")
    dashboard = client.get(f"{BASE}/dashboard").text
    code = _re.search(r"/signup\?ref=([a-z0-9]+)", dashboard).group(1)
    client.post(f"{BASE}/logout", follow_redirects=False)

    # a couple signs up through the planner's link and creates an event
    res = client.post(
        f"{BASE}/signup",
        data={"name": "Couple", "email": "couple@example.com",
              "password": "longpassword1", "ref": code},
        follow_redirects=False,
    )
    assert res.status_code == 303
    _create_event(client, slug="referred-wedding")
    client.post(f"{BASE}/logout", follow_redirects=False)

    # the planner's dashboard now shows the attribution and pending commission
    client.post(f"{BASE}/login",
                data={"email": "planner@example.com", "password": "hunter2hunter2"},
                follow_redirects=False)
    dashboard = client.get(f"{BASE}/dashboard").text
    assert "<strong>1</strong> referred signup(s)" in dashboard
    assert "<strong>1</strong> event(s) created by your referrals" in dashboard
    assert "$10.00" in dashboard

    # a bogus ref code doesn't break signup
    res = client.post(
        f"{BASE}/signup",
        data={"email": "nobody@example.com", "password": "longpassword1", "ref": "zzzzzzzz"},
        follow_redirects=False,
    )
    assert res.status_code == 303


def test_ai_captioning_search_and_highlights(client, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module.ai_agents, "ai_enabled", lambda: True)
    captions = iter([
        {"caption": "The couple cutting the cake", "tags": ["cake", "couple"], "quality": 9},
        {"caption": "Guests dancing at night", "tags": ["dancing", "night"], "quality": 5},
    ])
    monkeypatch.setattr(app_module.ai_agents, "caption_photo", lambda path: next(captions))

    _signup(client)
    _create_event(client)
    client.post(f"{EVENT}/login", data={"password": "cake123"}, follow_redirects=False)
    for name in ("cake.jpg", "dance.jpg"):
        client.post(f"{EVENT}/api/upload", files={"files": (name, _fake_jpeg(), "image/jpeg")})

    all_photos = client.get(f"{EVENT}/api/photos").json()
    assert all_photos["ai_enabled"] is True
    assert sorted(p["caption"] for p in all_photos["photos"]) == [
        "Guests dancing at night", "The couple cutting the cake",
    ]

    # search hits captions and tags
    hits = client.get(f"{EVENT}/api/photos", params={"q": "cake"}).json()["photos"]
    assert len(hits) == 1 and hits[0]["caption"] == "The couple cutting the cake"

    # highlights = quality >= 8
    best = client.get(f"{EVENT}/api/photos", params={"highlights": 1}).json()["photos"]
    assert len(best) == 1 and best[0]["quality"] == 9


def test_recap_owner_only(client, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module.ai_agents, "ai_enabled", lambda: True)
    monkeypatch.setattr(app_module.ai_agents, "generate_recap",
                        lambda title, photos: f"What a day at {title}!")

    _signup(client)
    _create_event(client)
    import re as _re
    dashboard = client.get(f"{BASE}/dashboard").text
    event_id = _re.search(r'data-event="([0-9a-f]{32})"', dashboard).group(1)

    res = client.post(f"{BASE}/api/events/{event_id}/recap")
    assert res.status_code == 200
    assert "What a day" in res.json()["recap"]

    client.cookies.clear()
    assert client.post(f"{BASE}/api/events/{event_id}/recap").status_code == 401


def test_partner_page_and_badges(client):
    page = client.get(f"{BASE}/partners")
    assert page.status_code == 200
    assert "20%" in page.text and "badge-light.svg" in page.text
    assert client.get(f"{BASE}/static/badge-light.svg").status_code == 200
    assert client.get(f"{BASE}/static/badge-dark.svg").status_code == 200


def test_referral_qr_and_charity(client):
    # QR requires login
    assert client.get(f"{BASE}/api/referral-qr.png").status_code == 401
    _signup(client)
    res = client.get(f"{BASE}/api/referral-qr.png")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert "attachment" in res.headers["content-disposition"]

    # charity opt-in shows on the dashboard and can be cleared
    res = client.post(f"{BASE}/api/referral-charity",
                      data={"charity": "  Local Food  Bank "}, follow_redirects=False)
    assert res.status_code == 303
    assert "Local Food Bank" in client.get(f"{BASE}/dashboard").text
    client.post(f"{BASE}/api/referral-charity", data={"charity": ""}, follow_redirects=False)
    assert "Local Food Bank" not in client.get(f"{BASE}/dashboard").text


def test_qr_code_owner_only(client):
    _signup(client)
    _create_event(client)
    dashboard = client.get(f"{BASE}/dashboard").text
    import re
    event_id = re.search(r"/api/events/([0-9a-f]{32})/qr\.png", dashboard).group(1)
    res = client.get(f"{BASE}/api/events/{event_id}/qr.png")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    client.cookies.clear()
    assert client.get(f"{BASE}/api/events/{event_id}/qr.png").status_code == 401
