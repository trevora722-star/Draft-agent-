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
    monkeypatch.setenv("CR_KIOSK_KEY", "expo-key-1")
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
    res = client.post(f"{EVENT}/login", data={"password": "cake123", "email": "guest@example.com"}, follow_redirects=False)
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

    client.post(f"{EVENT}/login", data={"password": "cake123", "email": "guest@example.com"}, follow_redirects=False)
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
        data={"password": "fete789", "email": "guest@example.com"},
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
    assert "$25.00" in dashboard

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
    client.post(f"{EVENT}/login", data={"password": "cake123", "email": "guest@example.com"}, follow_redirects=False)
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
    assert "50%" in page.text and "badge-light.svg" in page.text
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


def test_live_stream(client):
    _signup(client)
    _create_event(client, slug="stream-party", guest_password="disco9")

    # anonymous viewers are sent to the event login
    client.cookies.clear()
    res = client.get("http://stream-party.confettiroll.test/stream", follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"] == "/login"

    # a logged-in guest (or the TV) gets the slideshow - no QR overlay
    client.post("http://stream-party.confettiroll.test/login",
                data={"password": "disco9", "email": "tv@example.com"},
                follow_redirects=False)
    page = client.get("http://stream-party.confettiroll.test/stream")
    assert page.status_code == 200
    assert "SCAN TO ADD YOUR PHOTOS" not in page.text
    assert "stream-qr" not in page.text

    # /stream on the main site just goes home
    assert client.get(f"{BASE}/stream", follow_redirects=False).status_code == 303


def test_keepsake_book(client, monkeypatch, tmp_path):
    import app as app_module
    monkeypatch.setattr(app_module.ai_agents, "ai_enabled", lambda: True)
    monkeypatch.setattr(app_module.ai_agents, "generate_recap",
                        lambda title, photos: "It was a beautiful day from start to finish.")

    _signup(client)
    _create_event(client)
    client.post(
        f"{EVENT}/api/upload",
        files=[("files", ("dance.jpg", _fake_jpeg(), "image/jpeg")),
               ("files", ("cake.jpg", _fake_jpeg(), "image/jpeg"))],
        data={"uploader": "Aunt May"},
    )
    import re as _re
    event_id = _re.search(r'data-event="([0-9a-f]{32})"',
                          client.get(f"{BASE}/dashboard").text).group(1)

    # owner composes the book
    res = client.post(f"{BASE}/api/events/{event_id}/book")
    assert res.status_code == 200
    body = res.json()
    assert body["pages"] == 2 and body["url"].endswith("/book.pdf")

    # guests can download it from the event host; anonymous visitors can't
    pdf = client.get(f"{EVENT}/book.pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")
    assert "keepsake book" in pdf.headers["content-disposition"]
    client.cookies.clear()
    assert client.get(f"{EVENT}/book.pdf").status_code == 401

    # gallery API advertises the book once it exists
    client.post(f"{EVENT}/login", data={"password": "cake123", "email": "guest@example.com"}, follow_redirects=False)
    assert client.get(f"{EVENT}/api/photos").json()["book"] is True

    # an event with no photos can't make a book
    client.post(f"{BASE}/login",
                data={"email": "host@example.com", "password": "hunter2hunter2"},
                follow_redirects=False)
    _create_event(client, slug="empty-event")
    empty_id = _re.search(r'data-event="([0-9a-f]{32})"[^>]*data-url="https://empty-event',
                          client.get(f"{BASE}/dashboard").text)
    ids = _re.findall(r'data-event="([0-9a-f]{32})"', client.get(f"{BASE}/dashboard").text)
    other = [i for i in ids if i != event_id][0]
    assert client.post(f"{BASE}/api/events/{other}/book").status_code == 400


def test_outreach_engine(client, monkeypatch):
    import app as app_module

    assert client.get(f"{BASE}/outreach", follow_redirects=False).status_code == 403
    res = client.get(f"{BASE}/outreach", params={"key": "expo-key-1"})
    assert res.status_code == 200 and "Partner outreach" in res.text

    # bulk add: two valid rows, one junk row
    client.post(f"{BASE}/api/outreach/prospects", data={"bulk": (
        "Jess Lee, Golden Hour Photography, photographer, jess@goldenhour.com, Toronto, 30 weddings/yr\n"
        "Sam Ortiz, Lakeview Manor, venue, events@lakeviewmanor.com, Muskoka,\n"
        "junk line without email"
    )}, follow_redirects=False)
    page = client.get(f"{BASE}/outreach").text
    assert "Jess Lee" in page and "Lakeview Manor" in page
    assert page.count('class="prospect"') == 2

    # AI writes a personalized pitch and status flips to pitched
    monkeypatch.setattr(app_module.ai_agents, "ai_enabled", lambda: True)
    monkeypatch.setattr(app_module.ai_agents, "write_pitch",
                        lambda p, rate, url: {
                            "subject": f"Guest photos for {p['business']}",
                            "body": f"Hi {p['name']}, partners earn {rate}. {url}",
                        })
    import re as _re
    pid = _re.search(r'data-id="(\d+)"', page).group(1)
    res = client.post(f"{BASE}/api/outreach/prospects/{pid}/pitch")
    assert res.status_code == 200
    assert "Guest photos for" in res.json()["subject"]
    page = client.get(f"{BASE}/outreach").text
    assert "Guest photos for" in page and "pitched" in page

    # status update + CSV export
    client.post(f"{BASE}/api/outreach/prospects/{pid}/status", data={"status": "joined"})
    csv = client.get(f"{BASE}/outreach/prospects.csv", params={"key": "expo-key-1"})
    assert csv.status_code == 200 and "joined" in csv.text
    assert client.get(f"{BASE}/outreach/prospects.csv").status_code == 403


def test_packages_and_landing(client):
    data = client.get(f"{BASE}/api/packages").json()
    assert data["packages"]["celebration"]["price"] == "$49"
    assert data["packages"]["wholesale10"]["kind"] == "wholesale"
    assert data["stripe"] is False

    assert data["packages"]["gala"]["price"] == "$499"

    # placeholder-marked social proof, clarified free week, and the footer
    landing = client.get(f"{BASE}/").text
    for expected in ("PLACEHOLDER TESTIMONIALS", "after the confetti settles",
                     "No credit card required to start",
                     "How does the free week work?",
                     "Privacy Policy", "Terms of Service",
                     "hello@confettiroll.com", "© 2026 ConfettiRoll"):
        assert expected in landing

    # legal pages exist and carry the photo-ownership promise
    assert "never ours" in client.get(f"{BASE}/privacy").text
    assert "free week" in client.get(f"{BASE}/terms").text
    assert "hello@confettiroll.com" in client.get(f"{BASE}/partners").text

    for expected in ("Celebration", "Heirloom", "$49", "$99", "$245",
                     "$199", "Most popular", "Founding beta",
                     "Gala Evening package", "$499", "Executive Edition",
                     "Request\n        a custom quote"):
        assert expected in landing


def test_executive_book_pricing():
    import billing

    assert billing.book_price_cents("executive", 30) == 19900
    assert billing.book_price_cents("executive", 80) == 24900
    assert billing.book_price_cents("executive", 999) == 29900
    assert billing.book_tier_label("executive", 30) == "up to 40 pages"


def test_checkout_and_webhook(client, monkeypatch):
    import app as app_module

    # checkout requires login; free tier short-circuits; beta mode without Stripe
    assert client.post(f"{BASE}/api/checkout/celebration").status_code == 401
    _signup(client)
    assert client.post(f"{BASE}/api/checkout/starter").json()["beta"] is True
    assert client.post(f"{BASE}/api/checkout/celebration").json()["beta"] is True
    assert client.post(f"{BASE}/api/checkout/nope").status_code == 404

    # with Stripe "configured", checkout returns a session URL
    monkeypatch.setattr(app_module.billing, "stripe_enabled", lambda: True)
    monkeypatch.setattr(app_module.billing, "create_checkout",
                        lambda pkg, uid, base, event_id="": f"https://stripe.test/{pkg}/{uid}")
    res = client.post(f"{BASE}/api/checkout/wholesale10")
    assert res.json()["url"].startswith("https://stripe.test/wholesale10/")
    user_id = int(res.json()["url"].rsplit("/", 1)[1])

    # webhook grants credits (idempotently) and records the purchase
    fake_event = {
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_test_1", "amount_total": 24500,
            "metadata": {"user_id": str(user_id), "package": "wholesale10", "event_id": ""},
        }},
    }
    monkeypatch.setattr(app_module.billing, "parse_webhook", lambda payload, sig: fake_event)
    for _ in range(2):  # Stripe retries deliveries; credits must not double
        res = client.post(f"{BASE}/stripe/webhook", json={},
                          headers={"stripe-signature": "sig"})
        assert res.status_code == 200
    dashboard = client.get(f"{BASE}/dashboard").text
    assert "Event credits: <strong>10</strong>" in dashboard


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


PROM = "http://grad-gala.confettiroll.test"


def test_prom_tagged_event_flow(client):
    """Prom mode: staff-only uploads with a private code, optional name
    tags, the album sealed until the school's one keepsake book is ready."""
    import re as _re

    _signup(client)
    _create_event(client, slug="grad-gala", title="Grad Gala 2026",
                  event_type="prom")

    dashboard = client.get(f"{BASE}/dashboard").text
    assert "Prom mode" in dashboard
    event_id = _re.search(r"/api/events/([0-9a-f]{32})/members", dashboard).group(1)
    staff_code = _re.search(r"upload code\s+<code>([a-z0-9]{6})</code>", dashboard).group(1)
    assert staff_code != "cake123"

    # optional roster of names for tagging - no codes involved
    client.post(f"{BASE}/api/events/{event_id}/members",
                data={"names": "Ava Martin\nNoah Chen\n\n"}, follow_redirects=False)

    # the Vice Principal signs in with the staff code (no email needed)
    client.cookies.clear()
    res = client.post(f"{PROM}/login", data={"password": staff_code},
                      follow_redirects=False)
    assert res.status_code == 303
    staff_listing = client.get(f"{PROM}/api/photos").json()
    assert staff_listing["is_staff"] is True and staff_listing["is_admin"] is False
    res = client.post(
        f"{PROM}/api/upload",
        files=[("files", ("ava.jpg", _fake_jpeg(), "image/jpeg"))],
        data={"uploader": "Vice Principal"},
    )
    ava_photo = res.json()["saved"][0]["id"]
    # staff tags Ava by name, but cannot delete anything
    ava_id = next(m["id"] for m in staff_listing["members"]
                  if m["name"] == "Ava Martin")
    res = client.post(f"{PROM}/api/photos/{ava_photo}/tags",
                      data={"members": str(ava_id)})
    assert res.status_code == 200 and res.json()["tagged"] == [ava_id]
    assert client.delete(f"{PROM}/api/photos/{ava_photo}").status_code == 403

    # a wrong password is rejected; the right one needs an email
    client.cookies.clear()
    assert "isn't right" in client.post(f"{PROM}/login",
                                        data={"password": "not-right"}).text
    assert "add your email" in client.post(f"{PROM}/login",
                                           data={"password": "cake123"}).text

    # a student signs in with the shared password - album still sealed
    res = client.post(f"{PROM}/login",
                      data={"password": "cake123", "email": "ava@example.com"},
                      follow_redirects=False)
    assert res.status_code == 303
    listing = client.get(f"{PROM}/api/photos").json()
    assert listing["photos"] == [] and listing["book_pending"] is True
    assert client.get(f"{PROM}/photos/{ava_photo}").status_code == 404
    assert client.get(f"{PROM}/thumbs/{ava_photo}").status_code == 404
    assert client.get(f"{PROM}/book.pdf").status_code == 404
    # students can't upload, can't build personal books
    res = client.post(f"{PROM}/api/upload",
                      files=[("files", ("selfie.jpg", _fake_jpeg(), "image/jpeg"))])
    assert res.status_code == 403
    assert client.post(f"{PROM}/api/my-book").status_code == 404

    # the school closes the album and builds the one keepsake book
    client.post(f"{BASE}/login",
                data={"email": "host@example.com", "password": "hunter2hunter2"},
                follow_redirects=False)
    client.post(f"{BASE}/api/events/{event_id}/lock", data={"locked": "1"},
                follow_redirects=False)
    res = client.post(f"{BASE}/api/events/{event_id}/book")
    assert res.status_code == 200 and res.json()["pages"] >= 1

    # now the student sees every photo and can order the book
    client.cookies.clear()
    client.post(f"{PROM}/login",
                data={"password": "cake123", "email": "ava@example.com"},
                follow_redirects=False)
    listing = client.get(f"{PROM}/api/photos").json()
    assert [p["id"] for p in listing["photos"]] == [ava_photo]
    assert listing["book"] is True
    assert client.get(f"{PROM}/photos/{ava_photo}").status_code == 200
    assert client.get(f"{PROM}/book.pdf").status_code == 200
    quote = client.get(f"{PROM}/api/book-quote").json()
    assert quote["pages"] >= 1 and "softcover" in quote["covers"]


def test_google_signin_flow(client, monkeypatch):
    import app as app_module
    from urllib.parse import parse_qs, urlparse

    # hidden when unconfigured
    assert "Continue with Google" not in client.get(f"{BASE}/login").text
    assert client.get(f"{BASE}/auth/google", follow_redirects=False).headers["location"] == "/login"

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret")
    assert "Continue with Google" in client.get(f"{BASE}/login").text
    assert "never your Google Photos" in client.get(f"{BASE}/signup").text

    # start: redirects to Google with our signed state and identity-only scopes
    res = client.get(f"{BASE}/auth/google", follow_redirects=False)
    assert res.status_code == 303
    location = res.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    q = parse_qs(urlparse(location).query)
    assert q["scope"] == ["openid email profile"]
    state = q["state"][0]

    # callback: identity comes back, account is created, session starts
    monkeypatch.setattr(
        app_module.google_auth, "exchange",
        lambda code, redirect_uri: {"email": "gina@example.com", "name": "Gina Google"},
    )
    res = client.get(f"{BASE}/auth/google/callback?code=abc&state={state}",
                     follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"] == "/dashboard"
    assert "Gina Google" in client.get(f"{BASE}/dashboard").text

    # the Google account has no usable password
    client.cookies.clear()
    res = client.post(f"{BASE}/login",
                      data={"email": "gina@example.com", "password": ""})
    assert "Wrong email or password" in res.text

    # same email signs in again -> same account, no duplicate
    res = client.get(f"{BASE}/auth/google", follow_redirects=False)
    state2 = parse_qs(urlparse(res.headers["location"]).query)["state"][0]
    res = client.get(f"{BASE}/auth/google/callback?code=xyz&state={state2}",
                     follow_redirects=False)
    assert res.headers["location"] == "/dashboard"

    # a forged state is rejected
    client.cookies.clear()
    res = client.get(f"{BASE}/auth/google/callback?code=abc&state=ga.99999999999.-.bad")
    assert "didn't complete" in res.text


def test_delete_event_forever(client, tmp_path):
    _signup(client)
    _create_event(client)
    client.post(
        f"{EVENT}/api/upload",
        files=[("files", ("dance.jpg", _fake_jpeg(), "image/jpeg"))],
        data={"uploader": "Uncle Bob"},
    )
    import re as _re
    dashboard = client.get(f"{BASE}/dashboard").text
    assert "Delete forever" in dashboard
    event_id = _re.search(r"/api/events/([0-9a-f]{32})/delete", dashboard).group(1)
    event_dir = tmp_path / "events" / event_id
    assert event_dir.exists()

    # someone else's session can't delete it
    client.cookies.clear()
    res = client.post(f"{BASE}/api/events/{event_id}/delete", follow_redirects=False)
    assert res.headers["location"] == "/login"
    assert event_dir.exists()

    # the owner can — media directory and all
    client.post(f"{BASE}/login",
                data={"email": "host@example.com", "password": "hunter2hunter2"},
                follow_redirects=False)
    res = client.post(f"{BASE}/api/events/{event_id}/delete", follow_redirects=False)
    assert res.headers["location"] == "/dashboard"
    assert not event_dir.exists()
    assert "anna-and-james.confettiroll.test" not in client.get(f"{BASE}/dashboard").text
    client.cookies.clear()
    assert client.get(f"{EVENT}/api/photos").status_code == 404


def test_close_album_and_book_announcement(client, monkeypatch):
    import app as app_module
    import re as _re

    _signup(client)
    _create_event(client)

    # a guest signs in and leaves an email for book news
    client.cookies.clear()
    client.post(f"{EVENT}/login",
                data={"password": "cake123", "email": "Aunt.Carol@example.com"},
                follow_redirects=False)
    res = client.post(f"{EVENT}/api/upload",
                      files=[("files", ("dance.jpg", _fake_jpeg(), "image/jpeg"))],
                      data={"uploader": "Aunt Carol"})
    assert res.status_code == 200 and len(res.json()["saved"]) == 1

    # host closes the album
    client.post(f"{BASE}/login",
                data={"email": "host@example.com", "password": "hunter2hunter2"},
                follow_redirects=False)
    dashboard = client.get(f"{BASE}/dashboard").text
    assert "Email the book (1 signed up)" in dashboard
    event_id = _re.search(r"/api/events/([0-9a-f]{32})/lock", dashboard).group(1)
    client.post(f"{BASE}/api/events/{event_id}/lock", data={"locked": "1"},
                follow_redirects=False)
    assert "album closed" in client.get(f"{BASE}/dashboard").text

    # admin can still upload; a guest can't any more
    assert client.post(
        f"{EVENT}/api/upload",
        files=[("files", ("late.jpg", _fake_jpeg(), "image/jpeg"))],
    ).status_code == 200
    client.cookies.clear()
    client.post(f"{EVENT}/login", data={"password": "cake123", "email": "Aunt.Carol@example.com"},
                follow_redirects=False)  # duplicate email is ignored
    listing = client.get(f"{EVENT}/api/photos").json()
    assert listing["locked"] is True
    res = client.post(f"{EVENT}/api/upload",
                      files=[("files", ("extra.jpg", _fake_jpeg(), "image/jpeg"))])
    assert res.status_code == 403

    # announcement requires the book to exist
    client.post(f"{BASE}/login",
                data={"email": "host@example.com", "password": "hunter2hunter2"},
                follow_redirects=False)
    res = client.post(f"{BASE}/api/events/{event_id}/announce-book")
    assert res.status_code == 400
    assert client.post(f"{BASE}/api/events/{event_id}/book").status_code == 200

    # no mail provider -> nothing sent, host gets the copy + waiting count
    res = client.post(f"{BASE}/api/events/{event_id}/announce-book").json()
    assert res["email_configured"] is False and res["pending"] == 1
    assert "keepsake book" in res["subject"]

    # with a provider, everyone waiting is emailed exactly once
    sent = []
    monkeypatch.setattr(app_module.mailer, "enabled", lambda: True)
    monkeypatch.setattr(app_module.mailer, "send",
                        lambda to, subject, text: sent.append(to) or True)
    res = client.post(f"{BASE}/api/events/{event_id}/announce-book").json()
    assert res["sent"] == 1 and sent == ["aunt.carol@example.com"]
    res = client.post(f"{BASE}/api/events/{event_id}/announce-book").json()
    assert res["sent"] == 0 and len(sent) == 1  # no double emails


def test_guest_book_order(client, monkeypatch):
    import app as app_module

    _signup(client)
    _create_event(client)
    client.post(f"{BASE}/api/events", data={
        "title": "x", "slug": "x-e", "guest_password": "cake123"})
    client.cookies.clear()
    client.post(f"{EVENT}/login", data={"password": "cake123", "email": "guest@example.com"}, follow_redirects=False)

    # the quote prices both covers by page count
    client.post(f"{EVENT}/api/upload",
                files=[("files", ("p.jpg", _fake_jpeg(), "image/jpeg"))])
    quote = client.get(f"{EVENT}/api/book-quote").json()
    assert quote["pages"] == 1 and quote["discount"] is False
    assert quote["covers"]["softcover"]["price_cents"] == 3900
    assert quote["covers"]["hardcover"]["price_cents"] == 5900

    # beta mode without Stripe (cover still required)
    assert client.post(f"{EVENT}/api/book-order").status_code == 422
    res = client.post(f"{EVENT}/api/book-order", data={"cover": "hardcover"}).json()
    assert res.get("beta") is True

    # with Stripe configured, guests get a checkout URL for their cover
    monkeypatch.setattr(app_module.billing, "stripe_enabled", lambda: True)
    seen = {}
    def fake_checkout(cover, pages, price, user_id, base, event_id,
                      success_url=None, cancel_url=None):
        seen.update(cover=cover, pages=pages, price=price, success_url=success_url)
        return "https://checkout.stripe.com/pay/cs_test_book"
    monkeypatch.setattr(app_module.billing, "create_book_checkout", fake_checkout)
    res = client.post(f"{EVENT}/api/book-order", data={"cover": "hardcover"}).json()
    assert res["url"].startswith("https://checkout.stripe.com/")
    assert seen["cover"] == "hardcover" and seen["price"] == 5900
    assert seen["success_url"].endswith("/?ordered=1")
    # nonsense covers are rejected; signed-out visitors are rejected
    assert client.post(f"{EVENT}/api/book-order",
                       data={"cover": "leather"}).status_code == 400
    client.cookies.clear()
    assert client.post(f"{EVENT}/api/book-order",
                       data={"cover": "hardcover"}).status_code == 401


def test_heirloom_first_book_discount(client, monkeypatch):
    import re as _re
    import app as app_module

    _signup(client)
    _create_event(client)
    client.post(f"{EVENT}/api/upload",
                files=[("files", ("p.jpg", _fake_jpeg(), "image/jpeg"))])
    dashboard = client.get(f"{BASE}/dashboard").text
    event_id = _re.search(r"/api/events/([0-9a-f]{32})/lock", dashboard).group(1)

    monkeypatch.setattr(app_module.billing, "stripe_enabled", lambda: True)

    def deliver(session_id, package):
        fake = {"type": "checkout.session.completed",
                "data": {"object": {"id": session_id, "amount_total": 9900,
                                    "metadata": {"user_id": "1", "package": package,
                                                 "event_id": event_id}}}}
        monkeypatch.setattr(app_module.billing, "parse_webhook", lambda p, s: fake)
        assert client.post(f"{BASE}/stripe/webhook", json={},
                           headers={"stripe-signature": "s"}).status_code == 200

    # buying Heirloom unlocks the event AND earns 50% off the first book
    deliver("cs_heirloom_1", "heirloom")
    quote = client.get(f"{EVENT}/api/book-quote").json()
    assert quote["discount"] is True
    assert quote["covers"]["hardcover"]["final_cents"] == 5900 // 2

    # once a book is bought for the event, the discount is used up
    deliver("cs_book_1", "book_hardcover")
    quote = client.get(f"{EVENT}/api/book-quote").json()
    assert quote["discount"] is False
    assert quote["covers"]["hardcover"]["final_cents"] == 5900


def test_promo_code_redemption(client):
    _signup(client)
    # invalid code
    assert client.post(f"{BASE}/api/redeem", data={"code": "nope"}).status_code == 404
    # the family & friends code grants a free Celebration once
    res = client.post(f"{BASE}/api/redeem", data={"code": "Armstrong"})
    assert res.status_code == 200
    body = res.json()
    assert body["granted"] == "Celebration" and body["credits"] == 1
    assert "Event credits: <strong>1</strong>" in client.get(f"{BASE}/dashboard").text
    # no double-dipping
    assert client.post(f"{BASE}/api/redeem", data={"code": "armstrong"}).status_code == 400
    # a different account can still use it
    client.post(f"{BASE}/logout", follow_redirects=False)
    _signup(client, email="cousin@example.com")
    assert client.post(f"{BASE}/api/redeem", data={"code": "armstrong"}).status_code == 200


def test_celebrations_page(client):
    res = client.get(f"{BASE}/celebrations")
    assert res.status_code == 200
    assert "reunion" in res.text.lower()
    assert "birthday" in res.text.lower()


def test_photo_editing(client):
    _signup(client)
    _create_event(client)
    res = client.post(
        f"{EVENT}/api/upload",
        files=[
            ("files", ("dance.jpg", _fake_jpeg(), "image/jpeg")),
            ("files", ("toast.mp4", b"\x00" * 1024, "video/mp4")),
        ],
    )
    saved = res.json()["saved"]
    photo_id = next(p["id"] for p in saved if p["type"] == "photo")
    video_id = next(p["id"] for p in saved if p["type"] == "video")

    # rotate swaps dimensions (fixture photo is 640x480)
    meta = client.post(f"{EVENT}/api/photos/{photo_id}/edit",
                       data={"op": "rotate_left"}).json()
    assert (meta["width"], meta["height"]) == (480, 640)
    assert meta["edited_at"] > 0
    # enhance succeeds; unknown op and video edits are rejected
    assert client.post(f"{EVENT}/api/photos/{photo_id}/edit",
                       data={"op": "enhance"}).status_code == 200
    assert client.post(f"{EVENT}/api/photos/{photo_id}/edit",
                       data={"op": "sepia"}).status_code == 400
    assert client.post(f"{EVENT}/api/photos/{video_id}/edit",
                       data={"op": "rotate_left"}).status_code == 400
    # caption/credit editing works for videos too
    meta = client.post(f"{EVENT}/api/photos/{video_id}/meta",
                       data={"caption": "The toast!", "uploader": "Aunt Carol"}).json()
    assert meta["caption"] == "The toast!" and meta["uploader"] == "Aunt Carol"

    # guests can't edit
    client.cookies.clear()
    client.post(f"{EVENT}/login", data={"password": "cake123", "email": "guest@example.com"}, follow_redirects=False)
    assert client.post(f"{EVENT}/api/photos/{photo_id}/edit",
                       data={"op": "enhance"}).status_code == 403


def test_book_picks_after_close(client, tmp_path):
    import re as _re
    _signup(client)
    _create_event(client)
    ids = []
    for i in range(3):
        res = client.post(f"{EVENT}/api/upload",
                          files=[("files", (f"p{i}.jpg", _fake_jpeg(), "image/jpeg"))])
        ids.append(res.json()["saved"][0]["id"])

    # before the album closes, guests can't pick for the book
    client.cookies.clear()
    client.post(f"{EVENT}/login", data={"password": "cake123", "email": "guest@example.com"}, follow_redirects=False)
    res = client.post(f"{EVENT}/api/photos/{ids[0]}/book-pick", data={"voter": "guest-aaa-111"})
    assert res.status_code == 400

    # host closes the album
    client.post(f"{BASE}/login",
                data={"email": "host@example.com", "password": "hunter2hunter2"},
                follow_redirects=False)
    dashboard = client.get(f"{BASE}/dashboard").text
    event_id = _re.search(r"/api/events/([0-9a-f]{32})/lock", dashboard).group(1)
    client.post(f"{BASE}/api/events/{event_id}/lock", data={"locked": "1"},
                follow_redirects=False)

    # now the guest stars two photos into the book
    client.cookies.clear()
    client.post(f"{EVENT}/login", data={"password": "cake123", "email": "guest@example.com"}, follow_redirects=False)
    assert client.post(f"{EVENT}/api/photos/{ids[0]}/book-pick",
                       data={"voter": "guest-aaa-111"}).json()["picked"] is True
    assert client.post(f"{EVENT}/api/photos/{ids[1]}/book-pick",
                       data={"voter": "guest-aaa-111"}).json()["picked"] is True
    listing = client.get(f"{EVENT}/api/photos?voter=guest-aaa-111").json()
    assert set(listing["my_book_picks"]) == {ids[0], ids[1]}
    assert listing["book_picks"][ids[0]] == 1

    # the book builds from exactly the starred set
    client.post(f"{BASE}/login",
                data={"email": "host@example.com", "password": "hunter2hunter2"},
                follow_redirects=False)
    res = client.post(f"{BASE}/api/events/{event_id}/book")
    assert res.status_code == 200 and res.json()["pages"] == 2


def test_sample_flipbook_route(client):
    # real manifests generated into static/samples/ power the viewer
    res = client.get(f"{BASE}/samples/vineyard-wedding")
    assert res.status_code == 200
    assert "Turn the page" in res.text
    assert "Anna &amp; James" in res.text or "Anna & James" in res.text
    # unknown or invalid slugs 404
    assert client.get(f"{BASE}/samples/not-a-book").status_code == 404
    assert client.get(f"{BASE}/samples/..%2Fsecrets").status_code in (404, 400)


def test_free_week_trial_hook(client, tmp_path, monkeypatch):
    import sqlite3 as _sq
    import app as app_module

    _signup(client)
    _create_event(client)

    # a fresh event is on its free week
    listing = client.get(f"{EVENT}/api/photos").json()
    assert listing["trial"]["paid"] is False
    assert listing["trial"]["expired"] is False
    assert listing["trial"]["days_left"] == 7
    dashboard = client.get(f"{BASE}/dashboard").text
    assert "free week: 7 days left" in dashboard

    # eight days later the trial has ended: uploads stop, photos stay
    with _sq.connect(tmp_path / "confettiroll.sqlite") as conn:
        conn.execute("UPDATE events SET created_at = created_at - 8*86400")
    res = client.post(f"{EVENT}/api/upload",
                      files=[("files", ("late.jpg", _fake_jpeg(), "image/jpeg"))])
    assert res.status_code == 403 and "free week" in res.json()["error"]
    assert client.get(f"{EVENT}/api/photos").json()["trial"]["expired"] is True
    dashboard = client.get(f"{BASE}/dashboard").text
    assert "free week ended" in dashboard
    assert "Unlock forever" in dashboard  # no credits yet -> buy button

    # the ending-soon email goes out once when a mailer is configured
    sent = []
    monkeypatch.setattr(app_module.mailer, "enabled", lambda: True)
    monkeypatch.setattr(app_module.mailer, "send",
                        lambda to, subject, text: sent.append(to) or True)
    client.get(f"{BASE}/dashboard")
    client.get(f"{BASE}/dashboard")
    assert sent == ["host@example.com"]  # once, not on every visit

    # redeeming a code gives a credit; applying it unlocks the album forever
    assert client.post(f"{BASE}/api/redeem", data={"code": "armstrong"}).status_code == 200
    dashboard = client.get(f"{BASE}/dashboard").text
    assert "Use 1 credit to unlock" in dashboard
    import re as _re
    event_id = _re.search(r"/api/events/([0-9a-f]{32})/apply-credit", dashboard).group(1)
    res = client.post(f"{BASE}/api/events/{event_id}/apply-credit", follow_redirects=False)
    assert res.headers["location"] == "/dashboard"
    listing = client.get(f"{EVENT}/api/photos").json()
    assert listing["trial"]["paid"] is True
    assert client.post(
        f"{EVENT}/api/upload",
        files=[("files", ("back.jpg", _fake_jpeg(), "image/jpeg"))],
    ).status_code == 200
    # credit is spent
    assert "Event credits: <strong>0</strong>" in client.get(f"{BASE}/dashboard").text


def test_checkout_for_event_unlocks_it(client, monkeypatch):
    import re as _re
    import app as app_module

    _signup(client)
    _create_event(client)
    dashboard = client.get(f"{BASE}/dashboard").text
    event_id = _re.search(r"/api/events/([0-9a-f]{32})/lock", dashboard).group(1)

    # a paid checkout tied to the event unlocks it via the webhook
    monkeypatch.setattr(app_module.billing, "stripe_enabled", lambda: True)
    fake_event = {
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_test_unlock", "amount_total": 4900,
            "metadata": {"user_id": "1", "package": "celebration",
                         "event_id": event_id},
        }},
    }
    monkeypatch.setattr(app_module.billing, "parse_webhook", lambda p, s: fake_event)
    res = client.post(f"{BASE}/stripe/webhook", json={},
                      headers={"stripe-signature": "sig"})
    assert res.status_code == 200
    listing = client.get(f"{EVENT}/api/photos").json()
    assert listing["trial"]["paid"] is True
    # the single celebration credit was consumed by the unlock
    assert "Event credits: <strong>0</strong>" in client.get(f"{BASE}/dashboard").text


def test_host_set_guest_upload_limit(client):
    import re as _re

    _signup(client)
    _create_event(client)
    dashboard = client.get(f"{BASE}/dashboard").text
    assert "Photos per guest" in dashboard
    event_id = _re.search(r"/api/events/([0-9a-f]{32})/settings", dashboard).group(1)

    # host sets a 2-photo allowance per guest
    client.post(f"{BASE}/api/events/{event_id}/settings",
                data={"guest_upload_limit": "2"}, follow_redirects=False)

    # a guest device gets exactly 2 photos through
    client.cookies.clear()
    client.post(f"{EVENT}/login", data={"password": "cake123", "email": "guest@example.com"}, follow_redirects=False)
    res = client.post(
        f"{EVENT}/api/upload",
        files=[("files", (f"g{i}.jpg", _fake_jpeg(), "image/jpeg")) for i in range(3)],
        data={"uploader": "Aunt Carol", "device": "device-carol-1"},
    ).json()
    assert len(res["saved"]) == 2
    assert "limit is 2" in res["errors"][0]["reason"]
    # further uploads from the same device are refused outright
    res = client.post(
        f"{EVENT}/api/upload",
        files=[("files", ("extra.jpg", _fake_jpeg(), "image/jpeg"))],
        data={"device": "device-carol-1"},
    )
    assert res.status_code == 403 and "thank you" in res.json()["error"]
    # the listing tells the guest where they stand
    listing = client.get(f"{EVENT}/api/photos?voter=device-carol-1").json()
    assert listing["upload_limit"] == 2 and listing["my_upload_count"] == 2

    # another guest's device has its own allowance
    res = client.post(
        f"{EVENT}/api/upload",
        files=[("files", ("other.jpg", _fake_jpeg(), "image/jpeg"))],
        data={"uploader": "Uncle Bob", "device": "device-bob-22"},
    ).json()
    assert len(res["saved"]) == 1

    # the host is never limited
    client.post(f"{BASE}/login",
                data={"email": "host@example.com", "password": "hunter2hunter2"},
                follow_redirects=False)
    res = client.post(
        f"{EVENT}/api/upload",
        files=[("files", (f"h{i}.jpg", _fake_jpeg(), "image/jpeg")) for i in range(4)],
    ).json()
    assert len(res["saved"]) == 4

    # back to unlimited
    client.post(f"{BASE}/api/events/{event_id}/settings",
                data={"guest_upload_limit": "0"}, follow_redirects=False)
    client.cookies.clear()
    client.post(f"{EVENT}/login", data={"password": "cake123", "email": "guest@example.com"}, follow_redirects=False)
    res = client.post(
        f"{EVENT}/api/upload",
        files=[("files", ("free.jpg", _fake_jpeg(), "image/jpeg"))],
        data={"device": "device-carol-1"},
    ).json()
    assert len(res["saved"]) == 1


GALA = "http://autumn-benefit.confettiroll.test"


def test_gala_table_host_flow(client):
    """Private events: guests view with the password, only table hosts
    upload, and everyone sees the whole album."""
    import re as _re

    _signup(client)
    _create_event(client, slug="autumn-benefit", title="Autumn Benefit",
                  event_type="gala", guest_password="orsay25")
    dashboard = client.get(f"{BASE}/dashboard").text
    assert "Private event" in dashboard and "Table hosts" in dashboard
    event_id = _re.search(r"/api/events/([0-9a-f]{32})/members", dashboard).group(1)

    # add two table hosts; each gets a personal host code
    client.post(f"{BASE}/api/events/{event_id}/members",
                data={"names": "Table 1 - Smith party\nTable 2 - Chen family"},
                follow_redirects=False)
    import csv as _csv, io as _io
    rows = list(_csv.reader(_io.StringIO(
        client.get(f"{BASE}/api/events/{event_id}/members.csv").text)))
    assert rows[0] == ["name", "email", "access code", "personal link", "gallery"]
    codes = {name: code for name, _, code, _, _ in rows[1:]}

    # an attendee with the shared password can view but not upload
    client.cookies.clear()
    res = client.post(f"{GALA}/login", data={"password": "orsay25", "email": "guest@example.com"},
                      follow_redirects=False)
    assert res.status_code == 303
    assert client.get(f"{GALA}/api/photos").status_code == 200
    res = client.post(f"{GALA}/api/upload",
                      files=[("files", ("x.jpg", _fake_jpeg(), "image/jpeg"))])
    assert res.status_code == 403 and "table hosts" in res.json()["error"]

    # a table host signs in with their code and uploads — credited to the table
    client.cookies.clear()
    res = client.post(f"{GALA}/login",
                      data={"password": codes["Table 1 - Smith party"]},
                      follow_redirects=False)
    assert res.status_code == 303
    res = client.post(f"{GALA}/api/upload",
                      files=[("files", ("t1.jpg", _fake_jpeg(), "image/jpeg"))]).json()
    assert len(res["saved"]) == 1
    assert res["saved"][0]["uploader"] == "Table 1 - Smith party"
    listing = client.get(f"{GALA}/api/photos").json()
    assert listing["member_name"] == "Table 1 - Smith party"
    assert listing["mode"] == "gala"
    # table hosts see the whole album and can star photos for their own book
    assert len(listing["photos"]) == 1
    photo_id = listing["photos"][0]["id"]
    assert client.post(f"{GALA}/api/photos/{photo_id}/pick").json()["picked"] is True
    book = client.post(f"{GALA}/api/my-book").json()
    assert book["pages"] >= 1 and book["url"] == "/my-book.pdf"
    assert client.get(f"{GALA}/my-book.pdf").status_code == 200
    # ...and print the card for their own table
    card = client.get(f"{GALA}/my-table-card.pdf")
    assert card.status_code == 200
    assert card.headers["content-type"] == "application/pdf"

    # the attendee sees the host's photo too (whole-album visibility)
    client.cookies.clear()
    client.post(f"{GALA}/login", data={"password": "orsay25", "email": "guest@example.com"}, follow_redirects=False)
    listing = client.get(f"{GALA}/api/photos").json()
    assert len(listing["photos"]) == 1
    assert client.get(f"{GALA}/photos/{photo_id}").status_code == 200


def test_gala_host_invites(client, monkeypatch):
    """Creating a gala asks for the table count, pre-creates a host slot per
    table, and each host can be emailed their personal access link."""
    import re as _re
    import app as app_module

    _signup(client)
    _create_event(client, slug="winter-gala", title="Winter Gala",
                  event_type="gala", guest_password="frost25", num_hosts="3")
    dashboard = client.get(f"{BASE}/dashboard").text
    assert "Table 1" in dashboard and "Table 3" in dashboard
    assert "Email all hosts their access" in dashboard
    event_id = _re.search(r"/api/events/([0-9a-f]{32})/members", dashboard).group(1)

    # roster line with an email attaches the address to the host
    client.post(f"{BASE}/api/events/{event_id}/members",
                data={"names": "Table 4 - Armstrong party, armstrong@example.com"},
                follow_redirects=False)
    import csv as _csv, io as _io
    rows = list(_csv.reader(_io.StringIO(
        client.get(f"{BASE}/api/events/{event_id}/members.csv").text)))
    by_name = {r[0]: r for r in rows[1:]}
    assert len(by_name) == 4
    assert by_name["Table 4 - Armstrong party"][1] == "armstrong@example.com"
    code = by_name["Table 4 - Armstrong party"][2]
    assert by_name["Table 4 - Armstrong party"][3].endswith(f"/host/{code}")
    member_id = _re.search(
        r"/api/events/%s/members/(\d+)/invite[^-]" % event_id,
        client.get(f"{BASE}/dashboard").text).group(1)

    # with no mail provider, the invite hands the organizer the link instead
    res = client.post(f"{BASE}/api/events/{event_id}/members/{member_id}/invite",
                      follow_redirects=False)
    assert res.status_code == 303

    # with mail "configured", invites are sent to hosts with addresses
    sent = []
    monkeypatch.setattr(app_module.mailer, "enabled", lambda: True)
    monkeypatch.setattr(app_module.mailer, "send",
                        lambda to, subject, body: sent.append((to, subject, body)) or True)
    res = client.post(f"{BASE}/api/events/{event_id}/members/invite-all",
                      follow_redirects=False)
    assert res.status_code == 303
    assert "1+invite" in res.headers["location"] or "1%20invite" in res.headers["location"]
    assert len(sent) == 1
    to, subject, body = sent[0]
    assert to == "armstrong@example.com"
    assert f"/host/{code}" in body
    assert "table card" in body and "keepsake book" in body

    # the emailed magic link signs the host straight in
    winter = "http://winter-gala.confettiroll.test"
    client.cookies.clear()
    res = client.get(f"{winter}/host/{code}", follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"] == "/"
    listing = client.get(f"{winter}/api/photos").json()
    assert listing["member_name"] == "Table 4 - Armstrong party"
    # a wrong code just bounces to the login page
    client.cookies.clear()
    res = client.get(f"{winter}/host/nope99", follow_redirects=False)
    assert res.headers["location"] == "/login"


def test_table_cards_pdf(client, tmp_path):
    import re as _re

    _signup(client)
    _create_event(client)
    dashboard = client.get(f"{BASE}/dashboard").text
    assert "Table cards (PDF)" in dashboard
    event_id = _re.search(r"/api/events/([0-9a-f]{32})/table-cards\.pdf", dashboard).group(1)

    # cards render without a custom photo
    res = client.get(f"{BASE}/api/events/{event_id}/table-cards.pdf")
    assert res.status_code == 200
    assert res.content.startswith(b"%PDF")
    assert res.headers["content-type"] == "application/pdf"

    # upload a couple photo / logo, cards still render (with the photo)
    res = client.post(f"{BASE}/api/events/{event_id}/card-photo",
                      files={"photo": ("us.jpg", _fake_jpeg(), "image/jpeg")},
                      follow_redirects=False)
    assert res.status_code == 303
    assert (tmp_path / "events" / event_id / "card.jpg").exists()
    assert "✓ set" in client.get(f"{BASE}/dashboard").text
    res = client.get(f"{BASE}/api/events/{event_id}/table-cards.pdf")
    assert res.status_code == 200 and res.content.startswith(b"%PDF")

    # only the owner can fetch cards or set the photo
    client.cookies.clear()
    assert client.get(f"{BASE}/api/events/{event_id}/table-cards.pdf").status_code == 404
    res = client.post(f"{BASE}/api/events/{event_id}/card-photo",
                      files={"photo": ("x.jpg", _fake_jpeg(), "image/jpeg")},
                      follow_redirects=False)
    assert res.headers["location"] == "/login"


def test_howto_page_and_site_preview(client):
    # the tutorial gallery has a typeable address
    res = client.get(f"{BASE}/howto")
    assert res.status_code == 200
    assert "How-to videos" in res.text

    # signed-in hosts can still see the public landing page
    _signup(client)
    res = client.get(f"{BASE}/", follow_redirects=False)
    assert res.status_code == 303  # normally straight to the dashboard
    res = client.get(f"{BASE}/?preview=1")
    assert res.status_code == 200
    assert "Private events" in res.text  # the gala section is visible
    assert 'href="/howto"' in res.text
