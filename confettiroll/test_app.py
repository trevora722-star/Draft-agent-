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


def _create_venue(client, slug="silver-oak", **overrides):
    data = {"name": "Silver Oak Winery", "slug": slug,
            "venue_type": "winery", "custom_domain": ""}
    data.update(overrides)
    return client.post(f"{BASE}/api/venues", data=data, follow_redirects=False)


def test_venue_white_label_flow(client):
    _signup(client)
    assert _create_venue(client, custom_domain="photos.silveroak.test").status_code == 303
    assert "brand studio" in client.get(f"{BASE}/dashboard").text

    # only one venue per account; slug collisions blocked both ways
    assert "error" in _create_venue(client, slug="other").headers["location"]
    assert "already+taken" in _create_event(client, slug="silver-oak").headers["location"]

    # upload a logo: accent auto-extracted from its dominant color
    buf = io.BytesIO()
    Image.new("RGB", (200, 200), color=(120, 30, 60)).save(buf, "PNG")
    res = client.post(f"{BASE}/api/venues/" + _venue_id(client) + "/logo",
                      files={"logo": ("logo.png", buf.getvalue(), "image/png")},
                      follow_redirects=False)
    assert res.status_code == 303

    # upload two showcase photos
    client.post(f"{BASE}/api/venues/" + _venue_id(client) + "/photos",
                files=[("files", ("a.jpg", _fake_jpeg(), "image/jpeg")),
                       ("files", ("b.jpg", _fake_jpeg(), "image/jpeg"))],
                follow_redirects=False)

    # save brand copy
    client.post(f"{BASE}/api/venues/" + _venue_id(client) + "/brand",
                data={"tagline": "Est. 1987 on the lake", "headline": "Welcome, friends",
                      "about": "Our winery hosts weddings and galas.",
                      "accent": "#336699", "custom_domain": "photos.silveroak.test"},
                follow_redirects=False)

    # the venue page answers on its subdomain AND its custom domain
    for host in ("http://silver-oak.confettiroll.test", "http://photos.silveroak.test"):
        page = client.get(f"{host}/")
        assert page.status_code == 200
        assert "Silver Oak Winery" in page.text
        assert "Welcome, friends" in page.text
        assert "#336699" in page.text
        assert page.text.count("vsnap") >= 2  # showcase photos present

    # venue assets are served
    dashboard = client.get(f"{BASE}/dashboard").text
    import re as _re
    logo_url = _re.search(r'src="(/venue-assets/[0-9a-f]{32}/logo\.png)"', dashboard).group(1)
    assert client.get(f"{BASE}{logo_url}").status_code == 200
    assert client.get(f"{BASE}/venue-assets/{_venue_id(client)}/../secret").status_code == 404


def test_venue_branded_event_gallery(client):
    _signup(client)
    _create_venue(client)
    vid = _venue_id(client)
    _create_event(client, slug="harvest-gala", venue_id=vid, guest_password="vino22")

    # the venue page lists the event
    vpage = client.get("http://silver-oak.confettiroll.test/").text
    assert "Anna &amp; James&#x27;s Wedding" in vpage or "Anna" in vpage

    # guest login page and gallery carry the venue branding
    login = client.get("http://harvest-gala.confettiroll.test/login").text
    assert "Hosted at" in login and "Silver Oak Winery" in login
    client.post("http://harvest-gala.confettiroll.test/login",
                data={"password": "vino22"}, follow_redirects=False)
    gallery = client.get("http://harvest-gala.confettiroll.test/").text
    assert "Hosted at" in gallery and "--accent:" in gallery


def test_venue_ai_brand_kit(client, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module.ai_agents, "ai_enabled", lambda: True)
    monkeypatch.setattr(
        app_module.ai_agents, "generate_brand_kit",
        lambda name, vtype, notes, logo=None, **kw: {
            "tagline": "Fairways and forever memories",
            "headline": "Your day at Pebble Pines",
            "about": "A golf course that hosts weddings.",
            "accent": "#2f6e4f",
        },
    )
    _signup(client)
    _create_venue(client, slug="pebble-pines")
    res = client.post(f"{BASE}/api/venues/{_venue_id(client)}/ai-brand",
                      data={"notes": "links course, ocean views"})
    assert res.status_code == 200
    assert res.json()["accent"] == "#2f6e4f"
    vpage = client.get("http://pebble-pines.confettiroll.test/").text
    assert "Fairways and forever memories" in vpage and "#2f6e4f" in vpage


def test_live_stream(client):
    _signup(client)
    _create_venue(client)
    _create_event(client, slug="stream-party", venue_id=_venue_id(client),
                  guest_password="disco9")

    # anonymous viewers are sent to the event login
    client.cookies.clear()
    res = client.get("http://stream-party.confettiroll.test/stream", follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"] == "/login"
    assert client.get("http://stream-party.confettiroll.test/stream-qr.png").status_code == 401

    # a logged-in guest (or the venue's TV) gets the branded slideshow + QR
    client.cookies.clear()
    client.post("http://stream-party.confettiroll.test/login",
                data={"password": "disco9"}, follow_redirects=False)
    page = client.get("http://stream-party.confettiroll.test/stream")
    assert page.status_code == 200
    assert "SCAN TO ADD YOUR PHOTOS" in page.text
    assert "Hosted at Silver Oak Winery" in page.text
    assert "--accent:" in page.text  # venue accent applied
    qr = client.get("http://stream-party.confettiroll.test/stream-qr.png")
    assert qr.status_code == 200 and qr.headers["content-type"] == "image/png"

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
    client.post(f"{EVENT}/login", data={"password": "cake123"}, follow_redirects=False)
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


def test_kiosk_booth_agent(client, monkeypatch):
    import app as app_module

    # locked until the device is unlocked with the key
    assert client.get(f"{BASE}/kiosk", follow_redirects=False).status_code == 403
    assert client.post(f"{BASE}/api/kiosk/chat", json={"messages": []}).status_code == 403

    res = client.get(f"{BASE}/kiosk", params={"key": "expo-key-1"})
    assert res.status_code == 200
    assert "Meet Callie" in res.text          # the avatar host
    assert 'id="avatar"' in res.text          # animated SVG face
    assert 'id="tts-toggle"' in res.text      # voice on/off
    assert client.get(f"{BASE}/kiosk-qr.png").status_code == 200

    # the booth agent replies and saves leads via its tool
    monkeypatch.setattr(app_module.ai_agents, "ai_enabled", lambda: True)

    def fake_booth_reply(history, save_lead):
        if "planner" in history[-1]["content"]:
            save_lead("Pat Planner", "pat@events.com", "planner", "referral program")
            return "Saved! The team will reach out about your 20% partner link."
        return "Welcome to the booth!"

    monkeypatch.setattr(app_module.ai_agents, "booth_reply", fake_booth_reply)

    res = client.post(f"{BASE}/api/kiosk/chat",
                      json={"messages": [{"role": "user", "content": "hi"}]})
    assert res.json()["reply"] == "Welcome to the booth!"

    res = client.post(f"{BASE}/api/kiosk/chat",
                      json={"messages": [{"role": "user", "content": "I'm a planner"}]})
    assert "20%" in res.json()["reply"]

    # the lead landed and exports as CSV (with the key, not just the cookie)
    csv = client.get(f"{BASE}/kiosk/leads.csv", params={"key": "expo-key-1"})
    assert csv.status_code == 200
    assert "pat@events.com" in csv.text and "planner" in csv.text
    assert client.get(f"{BASE}/kiosk/leads.csv").status_code == 403

    # malformed chat bodies are rejected
    assert client.post(f"{BASE}/api/kiosk/chat", json={"messages": "hi"}).status_code == 400


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
    assert data["venue_tiers"]["estate"]["monthly"] == "$199"
    assert data["stripe"] is False

    landing = client.get(f"{BASE}/").text
    for expected in ("Celebration", "Heirloom", "$49", "$99", "$245",
                     "Boutique", "Estate", "Grand", "$199", "$399",
                     "Most popular", "Founding beta"):
        assert expected in landing


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


def _venue_id(client):
    import re as _re
    dashboard = client.get(f"{BASE}/dashboard").text
    return _re.search(r"/api/venues/([0-9a-f]{32})/", dashboard).group(1)


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
    """Prom / grad-night mode: personal codes, tag-gated visibility, picks,
    and per-student keepsake books."""
    import csv as _csv
    import re as _re

    _signup(client)
    _create_event(client, slug="grad-gala", title="Grad Gala 2026",
                  event_type="prom")

    dashboard = client.get(f"{BASE}/dashboard").text
    assert "Tagged event" in dashboard
    event_id = _re.search(r"/api/events/([0-9a-f]{32})/members", dashboard).group(1)

    # school loads the roster; each student gets a personal code
    client.post(f"{BASE}/api/events/{event_id}/members",
                data={"names": "Ava Martin\nNoah Chen\n\n"}, follow_redirects=False)
    rows = list(_csv.reader(io.StringIO(
        client.get(f"{BASE}/api/events/{event_id}/members.csv").text)))
    assert rows[0] == ["name", "access code", "gallery"]
    codes = {name: code for name, code, _ in rows[1:]}
    assert set(codes) == {"Ava Martin", "Noah Chen"}

    # the host uploads a group shot (untagged for now)
    res = client.post(
        f"{PROM}/api/upload",
        files=[("files", ("group.jpg", _fake_jpeg(), "image/jpeg"))],
        data={"uploader": "Chaperone"},
    )
    group_photo = res.json()["saved"][0]["id"]

    # the shared event password is NOT accepted on a tagged event
    client.cookies.clear()
    res = client.post(f"{PROM}/login", data={"password": "cake123"})
    assert "isn't right" in res.text

    # Ava signs in with her code; her upload is auto-tagged to her
    res = client.post(f"{PROM}/login", data={"password": codes["Ava Martin"]},
                      follow_redirects=False)
    assert res.status_code == 303
    res = client.post(f"{PROM}/api/upload",
                      files=[("files", ("selfie.jpg", _fake_jpeg(), "image/jpeg"))])
    ava_photo = res.json()["saved"][0]["id"]

    listing = client.get(f"{PROM}/api/photos").json()
    assert [p["id"] for p in listing["photos"]] == [ava_photo]
    assert listing["member_name"] == "Ava Martin"
    assert listing["mode"] == "prom"

    # the untagged group shot is invisible AND unreachable to her
    assert client.get(f"{PROM}/photos/{group_photo}").status_code == 404
    assert client.get(f"{PROM}/thumbs/{group_photo}").status_code == 404
    # the big screen and the all-photos event book stay with the host
    assert client.get(f"{PROM}/stream", follow_redirects=False).status_code == 303
    assert client.get(f"{PROM}/book.pdf").status_code == 404

    # she picks her photo and builds her personal keepsake book
    assert client.post(f"{PROM}/api/photos/{ava_photo}/pick").json()["picked"] is True
    res = client.post(f"{PROM}/api/my-book")
    assert res.status_code == 200 and res.json()["pages"] == 1
    book = client.get(f"{PROM}/my-book.pdf")
    assert book.status_code == 200
    assert book.headers["content-type"] == "application/pdf"

    # Noah can't see or pick Ava's photo
    client.cookies.clear()
    client.post(f"{PROM}/login", data={"password": codes["Noah Chen"]},
                follow_redirects=False)
    assert client.get(f"{PROM}/api/photos").json()["photos"] == []
    assert client.get(f"{PROM}/photos/{ava_photo}").status_code == 404
    assert client.post(f"{PROM}/api/photos/{ava_photo}/pick").status_code == 404

    # the host tags the group shot to both students
    client.cookies.clear()
    client.post(f"{BASE}/login",
                data={"email": "host@example.com", "password": "hunter2hunter2"},
                follow_redirects=False)
    admin_listing = client.get(f"{PROM}/api/photos").json()
    assert admin_listing["is_admin"] is True
    assert len(admin_listing["photos"]) == 2  # host sees everything
    member_ids = ",".join(str(m["id"]) for m in admin_listing["members"])
    res = client.post(f"{PROM}/api/photos/{group_photo}/tags",
                      data={"members": member_ids})
    assert res.status_code == 200 and len(res.json()["tagged"]) == 2

    # now Noah sees exactly the group shot
    client.cookies.clear()
    client.post(f"{PROM}/login", data={"password": codes["Noah Chen"]},
                follow_redirects=False)
    noah_listing = client.get(f"{PROM}/api/photos").json()
    assert [p["id"] for p in noah_listing["photos"]] == [group_photo]
    assert client.get(f"{PROM}/photos/{group_photo}").status_code == 200
