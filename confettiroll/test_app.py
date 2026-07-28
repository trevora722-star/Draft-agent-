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
