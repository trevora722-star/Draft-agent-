"""Smoke tests for the wedding photo platform.

Run from this directory:  python -m pytest test_app.py
"""

import io
import os

import pytest
from PIL import Image


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WEDDING_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WEDDING_USERNAME", "guest")
    monkeypatch.setenv("WEDDING_PASSWORD", "sunflower")
    import app as app_module
    from fastapi.testclient import TestClient

    return TestClient(app_module.create_app())


def _login(client):
    return client.post(
        "/login",
        data={"username": "guest", "password": "sunflower"},
        follow_redirects=False,
    )


def _fake_jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), color=(180, 120, 90)).save(buf, "JPEG")
    return buf.getvalue()


def test_pages_require_login(client):
    assert client.get("/", follow_redirects=False).status_code == 303
    assert client.get("/api/photos").status_code == 401
    assert client.post("/api/upload", files={"files": ("a.jpg", b"x", "image/jpeg")}).status_code == 401


def test_wrong_password_rejected(client):
    res = client.post(
        "/login",
        data={"username": "guest", "password": "wrong"},
        follow_redirects=False,
    )
    assert res.status_code == 401
    assert "wedding_session" not in res.cookies


def test_login_upload_view_download_flow(client):
    res = _login(client)
    assert res.status_code == 303
    assert "wedding_session" in res.cookies

    # gallery page now loads
    assert client.get("/").status_code == 200

    # upload two photos, one with an unsupported extension
    res = client.post(
        "/api/upload",
        files=[
            ("files", ("dance.jpg", _fake_jpeg(), "image/jpeg")),
            ("files", ("notes.txt", b"not a photo", "text/plain")),
        ],
        data={"uploader": "Sam"},
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body["saved"]) == 1
    assert body["saved"][0]["uploader"] == "Sam"
    assert len(body["errors"]) == 1

    # it shows up in the gallery listing
    photos = client.get("/api/photos").json()["photos"]
    assert len(photos) == 1
    photo_id = photos[0]["id"]

    # full image, thumbnail, and download all work
    assert client.get(f"/photos/{photo_id}").status_code == 200
    assert client.get(f"/thumbs/{photo_id}").status_code == 200
    dl = client.get(f"/photos/{photo_id}", params={"download": 1})
    assert dl.status_code == 200
    assert "attachment" in dl.headers["content-disposition"]

    # path traversal / bogus ids are rejected
    assert client.get("/photos/../secret").status_code == 404
    assert client.get("/photos/deadbeef").status_code == 404


def test_corrupt_image_rejected(client):
    _login(client)
    res = client.post(
        "/api/upload",
        files={"files": ("broken.jpg", b"definitely not jpeg bytes", "image/jpeg")},
    )
    body = res.json()
    assert body["saved"] == []
    assert len(body["errors"]) == 1
