"""ConfettiRoll — multi-tenant photo & video sharing for events.

Organizers sign up at the main site (confettiroll.com), create events, and
get a shareable subdomain like sarah-and-tom.confettiroll.com (or their own
custom domain). Guests open the event site, enter the event password, and
can view and upload photos and videos. The organizer's own login doubles as
the event admin: they see delete buttons in their events' galleries.

Tenant routing is by Host header:
  - base domain (and localhost)      -> marketing site + organizer dashboard
  - <slug>.<base domain>             -> that event's gallery
  - any other host                   -> looked up as a custom domain

Run locally:
    pip install -r requirements.txt
    uvicorn app:app --reload
Main site: http://localhost:8000 - events resolve at http://<slug>.localhost:8000
"""

from __future__ import annotations

import hmac
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, Request, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from PIL import Image, ImageOps
import qrcode

import ai_agents

try:  # iPhone photos arrive as HEIC; convert them so browsers can show them.
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF_SUPPORTED = True
except ImportError:  # pragma: no cover - optional dependency
    HEIF_SUPPORTED = False

BASE_DIR = Path(__file__).resolve().parent

ORG_COOKIE = "cr_org"
GUEST_COOKIE = "cr_guest"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30
MAX_IMAGE_BYTES = 30 * 1024 * 1024
MAX_VIDEO_BYTES = 200 * 1024 * 1024
THUMB_MAX_DIM = 480
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif"}
HEIC_EXTENSIONS = {".heic", ".heif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
VIDEO_MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}

LOGIN_ATTEMPT_LIMIT = 20
LOGIN_ATTEMPT_WINDOW = 15 * 60

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,38}[a-z0-9])?$")
RESERVED_SLUGS = {
    "www", "api", "app", "mail", "admin", "dashboard", "blog", "help",
    "status", "docs", "support", "assets", "static", "cdn", "login",
    "signup", "billing", "demo",
}


# ---------------------------------------------------------------------------
# passwords & tokens


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        digest = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


class PhotoError(Exception):
    """Raised when a single uploaded file can't be accepted."""


def _load_secret(data_dir: Path) -> bytes:
    env_secret = os.environ.get("CR_SECRET_KEY")
    if env_secret:
        return env_secret.encode()
    secret_file = data_dir / "secret_key"
    if secret_file.exists():
        return secret_file.read_bytes()
    secret = secrets.token_bytes(32)
    secret_file.write_bytes(secret)
    return secret


# ---------------------------------------------------------------------------
# database


def init_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                password_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                owner_id INTEGER NOT NULL REFERENCES users(id),
                slug TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                event_date TEXT NOT NULL DEFAULT '',
                guest_password TEXT NOT NULL,
                custom_domain TEXT UNIQUE,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS venues (
                id TEXT PRIMARY KEY,
                owner_id INTEGER UNIQUE NOT NULL REFERENCES users(id),
                name TEXT NOT NULL,
                slug TEXT UNIQUE NOT NULL,
                venue_type TEXT NOT NULL DEFAULT '',
                custom_domain TEXT UNIQUE,
                tagline TEXT NOT NULL DEFAULT '',
                headline TEXT NOT NULL DEFAULT '',
                about TEXT NOT NULL DEFAULT '',
                accent TEXT NOT NULL DEFAULT '#e85d8a',
                logo TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL
            );
            """
        )
        # Lightweight migration: referral columns for the partner program.
        existing = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "referral_code" not in existing:
            conn.execute("ALTER TABLE users ADD COLUMN referral_code TEXT")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_referral_code"
                " ON users(referral_code)"
            )
        if "referred_by" not in existing:
            conn.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER")
        if "charity" not in existing:
            conn.execute("ALTER TABLE users ADD COLUMN charity TEXT NOT NULL DEFAULT ''")
        event_cols = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
        if "venue_id" not in event_cols:
            conn.execute("ALTER TABLE events ADD COLUMN venue_id TEXT")


def new_referral_code() -> str:
    # Short, human-friendly, unambiguous (no 0/O/1/l).
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


def venue_dir(data_dir: Path, venue_id: str) -> Path:
    d = data_dir / "venues" / venue_id
    (d / "photos").mkdir(parents=True, exist_ok=True)
    return d


def dominant_color(image_bytes: bytes) -> str | None:
    """Pull the strongest non-grayscale color from a logo for the accent."""
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        img.thumbnail((100, 100))
        counts: dict[tuple[int, int, int], int] = {}
        for r, g, b, a in img.getdata():
            if a < 128:
                continue
            if max(r, g, b) - min(r, g, b) < 30:  # skip grays/whites/blacks
                continue
            key = (r // 24 * 24, g // 24 * 24, b // 24 * 24)
            counts[key] = counts.get(key, 0) + 1
        if not counts:
            return None
        r, g, b = max(counts, key=counts.get)
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return None


def darken(hex_color: str, factor: float = 0.82) -> str:
    r = int(int(hex_color[1:3], 16) * factor)
    g = int(int(hex_color[3:5], 16) * factor)
    b = int(int(hex_color[5:7], 16) * factor)
    return f"#{r:02x}{g:02x}{b:02x}"


# ---------------------------------------------------------------------------
# media helpers (per-event directories)


def event_dirs(data_dir: Path, event_id: str) -> dict[str, Path]:
    root = data_dir / "events" / event_id
    dirs = {name: root / name for name in ("photos", "thumbs", "meta", "trash")}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def _find_media_file(photos_dir: Path, photo_id: str) -> Path | None:
    if not re.fullmatch(r"[0-9a-f]{32}", photo_id):
        return None
    for path in photos_dir.glob(f"{photo_id}.*"):
        return path
    return None


async def _save_photo(upload_file, original_name, uploader, ext, dirs) -> dict:
    if ext not in IMAGE_EXTENSIONS:
        raise PhotoError("not a supported photo or video type")
    if ext in HEIC_EXTENSIONS and not HEIF_SUPPORTED:
        raise PhotoError("HEIC support isn't installed on this server")

    data = await upload_file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise PhotoError("larger than 30 MB")
    if not data:
        raise PhotoError("empty file")

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:
        raise PhotoError("couldn't be read as an image") from exc
    image = ImageOps.exif_transpose(image)

    photo_id = uuid.uuid4().hex
    if ext in HEIC_EXTENSIONS:
        stored = dirs["photos"] / f"{photo_id}.jpg"
        image.convert("RGB").save(stored, "JPEG", quality=92)
        original_name = Path(original_name).stem + ".jpg"
    else:
        stored = dirs["photos"] / f"{photo_id}{ext}"
        stored.write_bytes(data)

    thumbnail = image.convert("RGB")
    thumbnail.thumbnail((THUMB_MAX_DIM, THUMB_MAX_DIM))
    thumbnail.save(dirs["thumbs"] / f"{photo_id}.jpg", "JPEG", quality=80)

    meta = {
        "id": photo_id,
        "type": "photo",
        "original_name": original_name,
        "uploader": uploader,
        "uploaded_at": int(time.time()),
        "width": image.width,
        "height": image.height,
    }
    (dirs["meta"] / f"{photo_id}.json").write_text(json.dumps(meta))
    return meta


async def _save_video(upload_file, original_name, uploader, ext, dirs) -> dict:
    photo_id = uuid.uuid4().hex
    dest = dirs["photos"] / f"{photo_id}{ext}"
    size = 0
    with dest.open("wb") as out:
        while chunk := await upload_file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_VIDEO_BYTES:
                out.close()
                dest.unlink(missing_ok=True)
                raise PhotoError("larger than 200 MB")
            out.write(chunk)
    if size == 0:
        dest.unlink(missing_ok=True)
        raise PhotoError("empty file")

    meta = {
        "id": photo_id,
        "type": "video",
        "original_name": original_name,
        "uploader": uploader,
        "uploaded_at": int(time.time()),
        "size": size,
    }
    (dirs["meta"] / f"{photo_id}.json").write_text(json.dumps(meta))
    return meta


# ---------------------------------------------------------------------------
# app factory


def create_app() -> FastAPI:
    base_domain = os.environ.get("CR_BASE_DOMAIN", "confettiroll.com").lower()
    # Estimated planner commission per referred event (20% of a ~$50 package),
    # tracked as pending and payable once billing launches.
    referral_fee = float(os.environ.get("CR_REFERRAL_FEE", "10"))
    data_dir = Path(os.environ.get("CR_DATA_DIR", BASE_DIR / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "confettiroll.sqlite"
    init_db(db_path)
    secret = _load_secret(data_dir)

    tpl = {
        name: (BASE_DIR / "templates" / f"{name}.html").read_text()
        for name in ("landing", "signup", "login", "dashboard", "guest_login",
                     "gallery", "partners", "venue")
    }

    app = FastAPI(title="ConfettiRoll", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    login_attempts: dict[str, list[float]] = {}

    def db() -> sqlite3.Connection:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ---- tokens ------------------------------------------------------------

    def sign(payload: str) -> str:
        return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()

    def make_org_token(user_id: int) -> str:
        payload = f"o.{user_id}.{int(time.time()) + SESSION_TTL_SECONDS}"
        return f"{payload}.{sign(payload)}"

    def make_guest_token(event_id: str) -> str:
        payload = f"g.{event_id}.{int(time.time()) + SESSION_TTL_SECONDS}"
        return f"{payload}.{sign(payload)}"

    def parse_token(token: str | None, kind: str) -> str | None:
        if not token:
            return None
        parts = token.split(".")
        if len(parts) != 4 or parts[0] != kind:
            return None
        payload = ".".join(parts[:3])
        if not hmac.compare_digest(parts[3], sign(payload)):
            return None
        if not parts[2].isdigit() or int(parts[2]) <= time.time():
            return None
        return parts[1]

    def current_user(request: Request) -> sqlite3.Row | None:
        user_id = parse_token(request.cookies.get(ORG_COOKIE), "o")
        if user_id is None:
            return None
        with db() as conn:
            return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    def org_cookie_kwargs(request: Request) -> dict:
        # Scope the organizer cookie to .<base_domain> so it is also sent to
        # event subdomains (that's how owners get admin rights in galleries).
        host = request.url.hostname or ""
        kwargs = dict(httponly=True, samesite="lax", max_age=SESSION_TTL_SECONDS)
        if host == base_domain or host.endswith("." + base_domain):
            kwargs["domain"] = "." + base_domain
        return kwargs

    # ---- tenant resolution -------------------------------------------------

    def resolve_event(request: Request) -> sqlite3.Row | None:
        host = (request.url.hostname or "").lower()
        main_hosts = {base_domain, "www." + base_domain, "localhost", "127.0.0.1"}
        if host in main_hosts:
            return None
        slug = None
        for suffix in ("." + base_domain, ".localhost"):
            if host.endswith(suffix):
                slug = host[: -len(suffix)]
                break
        with db() as conn:
            if slug is not None:
                if slug == "www" or "." in slug:
                    return None
                return conn.execute("SELECT * FROM events WHERE slug = ?", (slug,)).fetchone()
            return conn.execute(
                "SELECT * FROM events WHERE custom_domain = ?", (host,)
            ).fetchone()

    def resolve_venue(request: Request) -> sqlite3.Row | None:
        host = (request.url.hostname or "").lower()
        slug = None
        for suffix in ("." + base_domain, ".localhost"):
            if host.endswith(suffix):
                slug = host[: -len(suffix)]
                break
        with db() as conn:
            if slug is not None:
                if "." in slug:
                    return None
                return conn.execute("SELECT * FROM venues WHERE slug = ?", (slug,)).fetchone()
            return conn.execute(
                "SELECT * FROM venues WHERE custom_domain = ?", (host,)
            ).fetchone()

    def slug_in_use(conn: sqlite3.Connection, slug: str) -> bool:
        return (
            conn.execute("SELECT 1 FROM events WHERE slug = ?", (slug,)).fetchone() is not None
            or conn.execute("SELECT 1 FROM venues WHERE slug = ?", (slug,)).fetchone() is not None
        )

    def user_venue(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
        return conn.execute("SELECT * FROM venues WHERE owner_id = ?", (user_id,)).fetchone()

    def venue_url(venue: sqlite3.Row) -> str:
        if venue["custom_domain"]:
            return f"https://{venue['custom_domain']}"
        return f"https://{venue['slug']}.{base_domain}"

    def event_brand(event: sqlite3.Row) -> dict:
        """Branding substitutions for an event's gallery/login pages."""
        subs = {"brand_css": "", "brand_badge": ""}
        if not event["venue_id"]:
            return subs
        with db() as conn:
            venue = conn.execute(
                "SELECT * FROM venues WHERE id = ?", (event["venue_id"],)
            ).fetchone()
        if venue is None:
            return subs
        accent = venue["accent"] or "#e85d8a"
        subs["brand_css"] = (
            f"<style>:root{{--accent:{accent};--accent-dark:{darken(accent)}}}</style>"
        )
        logo_html = (
            f'<img src="/venue-assets/{venue["id"]}/{esc(venue["logo"])}" alt="" '
            'style="height:26px; vertical-align:middle; margin-right:8px; border-radius:4px">'
            if venue["logo"] else ""
        )
        subs["brand_badge"] = (
            f'<p style="text-align:center; font-size:13px; color:var(--soft); padding:6px 0">'
            f'{logo_html}Hosted at <a href="{esc(venue_url(venue))}" '
            f'style="color:var(--accent)">{esc(venue["name"])}</a></p>'
        )
        return subs

    def gallery_role(request: Request, event: sqlite3.Row) -> str | None:
        user = current_user(request)
        if user is not None and user["id"] == event["owner_id"]:
            return "admin"
        event_id = parse_token(request.cookies.get(GUEST_COOKIE), "g")
        if event_id == event["id"]:
            return "guest"
        return None

    def too_many_attempts(key: str) -> bool:
        now = time.time()
        attempts = [t for t in login_attempts.get(key, []) if now - t < LOGIN_ATTEMPT_WINDOW]
        login_attempts[key] = attempts
        return len(attempts) >= LOGIN_ATTEMPT_LIMIT

    def record_attempt(key: str) -> None:
        login_attempts.setdefault(key, []).append(time.time())

    def event_url(event: sqlite3.Row) -> str:
        if event["custom_domain"]:
            return f"https://{event['custom_domain']}"
        return f"https://{event['slug']}.{base_domain}"

    def page(template: str, **subs: str) -> HTMLResponse:
        html = tpl[template]
        for key, value in subs.items():
            html = html.replace("{{" + key.upper() + "}}", value)
        return HTMLResponse(html)

    def esc(text: str) -> str:
        return (
            str(text)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    def err_html(message: str) -> str:
        return f'<p class="error">{esc(message)}</p>' if message else ""

    # =======================================================================
    # main site (no tenant)
    # =======================================================================

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        event = resolve_event(request)
        if event is not None:
            return tenant_gallery(request, event)
        venue = resolve_venue(request)
        if venue is not None:
            return venue_page(venue)
        if current_user(request) is not None:
            return RedirectResponse("/dashboard", status_code=303)
        return page("landing", base=base_domain)

    def venue_page(venue: sqlite3.Row) -> HTMLResponse:
        vdir = venue_dir(data_dir, venue["id"])
        photo_tiles = "".join(
            f'<div class="vsnap"><img src="/venue-assets/{venue["id"]}/photos/{esc(p.name)}" alt=""></div>'
            for p in sorted((vdir / "photos").glob("*.jpg"))
        )
        with db() as conn:
            events = conn.execute(
                "SELECT * FROM events WHERE venue_id = ? ORDER BY created_at DESC",
                (venue["id"],),
            ).fetchall()
        event_rows = "".join(
            f'<a class="vevent" href="{esc(event_url(ev))}">'
            f'<span>{esc(ev["title"])}</span>'
            f'<span class="vdate">{esc(ev["event_date"] or "")}</span></a>'
            for ev in events
        ) or '<p class="vempty">Galleries appear here as events are hosted.</p>'
        accent = venue["accent"] or "#e85d8a"
        logo_html = (
            f'<img class="vlogo" src="/venue-assets/{venue["id"]}/{esc(venue["logo"])}" '
            f'alt="{esc(venue["name"])} logo">' if venue["logo"] else ""
        )
        return page(
            "venue",
            name=esc(venue["name"]),
            logo_html=logo_html,
            tagline=esc(venue["tagline"] or "Every event's photos, in one place."),
            headline=esc(venue["headline"] or f'Welcome to {venue["name"]}'),
            about=esc(venue["about"] or ""),
            accent=accent,
            accent_dark=darken(accent),
            photos_html=photo_tiles,
            events_html=event_rows,
        )

    @app.get("/venue-assets/{venue_id}/{path:path}")
    def venue_asset(venue_id: str, path: str):
        if not re.fullmatch(r"[0-9a-f]{32}", venue_id) or not re.fullmatch(
            r"(photos/)?[\w.-]+\.(png|jpg)", path
        ):
            return JSONResponse({"error": "not found"}, status_code=404)
        file = data_dir / "venues" / venue_id / path
        if not file.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(file)

    @app.get("/signup", response_class=HTMLResponse)
    def signup_form(request: Request, ref: str = ""):
        if resolve_event(request) is not None:
            return RedirectResponse("/", status_code=303)
        return page("signup", error="", ref=esc(ref.strip()[:16]))

    @app.post("/signup")
    def signup(request: Request, name: str = Form(""), email: str = Form(...),
               password: str = Form(...), ref: str = Form("")):
        email = email.strip().lower()
        name = re.sub(r"\s+", " ", name).strip()[:80]
        ref = ref.strip()[:16]
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            return page("signup", error=err_html("That doesn't look like an email address."), ref=esc(ref))
        if len(password) < 8:
            return page("signup", error=err_html("Password must be at least 8 characters."), ref=esc(ref))
        with db() as conn:
            referrer = conn.execute(
                "SELECT id FROM users WHERE referral_code = ?", (ref,)
            ).fetchone() if ref else None
            try:
                cur = conn.execute(
                    "INSERT INTO users (email, name, password_hash, created_at, referral_code, referred_by)"
                    " VALUES (?,?,?,?,?,?)",
                    (email, name, hash_password(password), int(time.time()),
                     new_referral_code(), referrer["id"] if referrer else None),
                )
                user_id = cur.lastrowid
            except sqlite3.IntegrityError:
                return page("signup", error=err_html("An account with that email already exists."), ref=esc(ref))
        response = RedirectResponse("/dashboard", status_code=303)
        response.set_cookie(ORG_COOKIE, make_org_token(user_id), **org_cookie_kwargs(request))
        return response

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        event = resolve_event(request)
        if event is not None:
            return page("guest_login", title=esc(event["title"]), error="",
                        **event_brand(event))
        return page("login", error="")

    @app.post("/login")
    def login(request: Request, email: str = Form(""), password: str = Form("")):
        event = resolve_event(request)
        if event is not None:
            return tenant_login(request, event, password)
        ip = request.client.host if request.client else "unknown"
        if too_many_attempts(f"org:{ip}"):
            return page("login", error=err_html("Too many attempts - please wait a few minutes."))
        with db() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)
            ).fetchone()
        if user is None or not verify_password(password, user["password_hash"]):
            record_attempt(f"org:{ip}")
            return page("login", error=err_html("Wrong email or password."))
        response = RedirectResponse("/dashboard", status_code=303)
        response.set_cookie(ORG_COOKIE, make_org_token(user["id"]), **org_cookie_kwargs(request))
        return response

    @app.post("/logout")
    def logout(request: Request):
        response = RedirectResponse("/login", status_code=303)
        if resolve_event(request) is not None:
            response.delete_cookie(GUEST_COOKIE)
        else:
            kwargs = org_cookie_kwargs(request)
            response.delete_cookie(ORG_COOKIE, domain=kwargs.get("domain"))
        return response

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard(request: Request, error: str = ""):
        user = current_user(request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        referral_code = user["referral_code"]
        with db() as conn:
            if not referral_code:  # accounts created before the referral migration
                referral_code = new_referral_code()
                conn.execute("UPDATE users SET referral_code = ? WHERE id = ?",
                             (referral_code, user["id"]))
            events = conn.execute(
                "SELECT * FROM events WHERE owner_id = ? ORDER BY created_at DESC",
                (user["id"],),
            ).fetchall()
            ref_signups = conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE referred_by = ?", (user["id"],)
            ).fetchone()["n"]
            ref_events = conn.execute(
                "SELECT COUNT(*) AS n FROM events WHERE owner_id IN"
                " (SELECT id FROM users WHERE referred_by = ?)", (user["id"],)
            ).fetchone()["n"]
            venue = user_venue(conn, user["id"])
        rows = []
        for ev in events:
            count = len(list((data_dir / "events" / ev["id"] / "meta").glob("*.json"))) \
                if (data_dir / "events" / ev["id"] / "meta").exists() else 0
            url = event_url(ev)
            rows.append(f"""
            <div class="event">
              <div class="event-head">
                <h3>{esc(ev['title'])}</h3>
                <span class="count">{count} item{'' if count == 1 else 's'}</span>
              </div>
              <p class="event-link"><a href="{esc(url)}" target="_blank">{esc(url.replace('https://', ''))}</a></p>
              <p class="event-cred">Guest password: <code>{esc(ev['guest_password'])}</code></p>
              <p class="event-actions">
                <a class="mini" href="{esc(url)}" target="_blank">Open gallery</a>
                <a class="mini" href="/api/events/{ev['id']}/qr.png" download="{esc(ev['slug'])}-qr.png">Download QR code</a>
                <button class="mini recap-btn" data-event="{ev['id']}" type="button">✨ AI recap</button>
              </p>
              <div class="recap" id="recap-{ev['id']}" hidden></div>
            </div>""")
        error_html = f'<p class="error">{esc(error)}</p>' if error else ""
        pending = ref_events * referral_fee

        if venue is None:
            venue_select = ""
            venue_panel = f"""
    <div class="panel">
      <h2>Your venue — white-label branding</h2>
      <p style="color:var(--soft); font-size:14.5px; line-height:1.6; margin-bottom:14px">
        Run a winery, golf course, wedding venue, or corporate event space?
        Create your own branded photo-sharing page: your logo, your photos,
        your colors, on your own domain. Every event you host gets a gallery
        carrying your brand.</p>
      <form method="post" action="/api/venues">
        <div class="form-grid">
          <div><label>Venue name</label><input name="name" placeholder="Silver Oak Winery" required></div>
          <div><label>Web address</label><input name="slug" placeholder="silver-oak" required
               pattern="[a-z0-9][a-z0-9-]{{1,38}}[a-z0-9]">
               <div class="hint">your-venue.{esc(base_domain)}</div></div>
          <div><label>Venue type</label>
            <select name="venue_type" style="width:100%; padding:11px 12px; font-size:15px; border:1px solid var(--line); border-radius:8px; background:#fdfcfa; font-family:inherit">
              <option>winery</option><option>golf course</option>
              <option>wedding venue</option><option>corporate event space</option>
              <option>restaurant</option><option>other</option>
            </select></div>
          <div><label>Your domain <span style="text-transform:none">(optional)</span></label>
            <input name="custom_domain" placeholder="photos.silveroak.com"></div>
        </div>
        <button class="create" type="submit">Create my venue page</button>
      </form>
    </div>"""
        else:
            venue_select = f"""
          <div>
            <label for="venue_id">Part of your venue?</label>
            <select id="venue_id" name="venue_id" style="width:100%; padding:11px 12px; font-size:15px; border:1px solid var(--line); border-radius:8px; background:#fdfcfa; font-family:inherit">
              <option value="">Standalone event</option>
              <option value="{venue['id']}" selected>{esc(venue['name'])}</option>
            </select>
          </div>"""
            vdir = venue_dir(data_dir, venue["id"])
            logo_html = (
                f'<img src="/venue-assets/{venue["id"]}/{esc(venue["logo"])}" alt="logo" style="height:44px; border-radius:6px; vertical-align:middle">'
                if venue["logo"] else '<span style="color:var(--soft); font-size:14px">No logo yet</span>'
            )
            photo_thumbs = "".join(
                f'''<span style="position:relative; display:inline-block">
                    <img src="/venue-assets/{venue["id"]}/photos/{p.name}" style="height:64px; border-radius:6px">
                    <form method="post" action="/api/venues/{venue["id"]}/photos/delete" style="position:absolute; top:2px; right:2px">
                      <input type="hidden" name="filename" value="{p.name}">
                      <button type="submit" style="border:none; background:rgba(30,26,22,.55); color:#fff; border-radius:50%; width:20px; height:20px; font-size:11px; cursor:pointer">✕</button>
                    </form></span>'''
                for p in sorted((vdir / "photos").glob("*.jpg"))
            ) or '<span style="color:var(--soft); font-size:14px">No photos yet — add a few showcase shots.</span>'
            venue_panel = f"""
    <div class="panel">
      <h2>{esc(venue['name'])} — brand studio</h2>
      <p style="margin-bottom:14px"><a class="mini" href="{esc(venue_url(venue))}" target="_blank">View your page</a>
        <span style="color:var(--soft); font-size:13.5px; margin-left:8px">{esc(venue_url(venue).replace('https://',''))}</span></p>

      <div style="display:flex; gap:26px; flex-wrap:wrap; align-items:center; margin-bottom:18px">
        <div>{logo_html}</div>
        <form method="post" action="/api/venues/{venue['id']}/logo" enctype="multipart/form-data" style="display:flex; gap:8px; align-items:center">
          <input type="file" name="logo" accept="image/*" required style="font-size:13px">
          <button class="mini" type="submit" style="cursor:pointer; background:none">Upload logo</button>
        </form>
      </div>

      <div style="margin-bottom:18px">
        <label>Showcase photos</label>
        <div style="display:flex; gap:8px; flex-wrap:wrap; margin:8px 0">{photo_thumbs}</div>
        <form method="post" action="/api/venues/{venue['id']}/photos" enctype="multipart/form-data" style="display:flex; gap:8px; align-items:center">
          <input type="file" name="files" accept="image/*" multiple required style="font-size:13px">
          <button class="mini" type="submit" style="cursor:pointer; background:none">Add photos</button>
        </form>
      </div>

      <form method="post" action="/api/venues/{venue['id']}/brand">
        <div class="form-grid">
          <div><label>Tagline</label><input name="tagline" value="{esc(venue['tagline'])}" placeholder="Where great days become great memories"></div>
          <div><label>Guest headline</label><input name="headline" value="{esc(venue['headline'])}" placeholder="Welcome — share your photos!"></div>
          <div><label>Accent color</label><input name="accent" type="color" value="{esc(venue['accent'])}" style="height:44px; padding:4px"></div>
          <div><label>Your domain</label><input name="custom_domain" value="{esc(venue['custom_domain'] or '')}" placeholder="photos.yourvenue.com"></div>
        </div>
        <div style="margin-top:12px"><label>About</label>
          <textarea name="about" rows="3" style="width:100%; padding:11px 12px; font-size:15px; border:1px solid var(--line); border-radius:8px; background:#fdfcfa; font-family:inherit">{esc(venue['about'])}</textarea></div>
        <button class="create" type="submit">Save branding</button>
      </form>

      <div style="margin-top:16px; padding-top:14px; border-top:1px solid var(--line)">
        <label>✨ Let the brand agent write it</label>
        <div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:6px">
          <input id="ai-notes" placeholder="Anything it should know? (est. 1987, lakefront, rustic-modern…)"
                 style="flex:1; min-width:240px; padding:9px 12px; font-size:14px; border:1px solid var(--line); border-radius:8px; background:#fdfcfa; font-family:inherit">
          <button class="mini" type="button" id="ai-brand-btn" data-venue="{venue['id']}" style="cursor:pointer; background:none">Generate brand kit</button>
        </div>
        <p id="ai-brand-status" style="color:var(--soft); font-size:13.5px; margin-top:6px"></p>
      </div>

      <p style="color:var(--soft); font-size:13.5px; margin-top:14px">
        Your domain: point a CNAME from <code>{esc(venue['custom_domain'] or 'photos.yourvenue.com')}</code>
        to <code>{esc(base_domain)}</code> and your page answers there.</p>
    </div>"""

        return page(
            "dashboard",
            user_name=esc(user["name"] or user["email"]),
            events="\n".join(rows) or '<p class="empty">No events yet — create your first one above.</p>',
            base_domain=esc(base_domain),
            error=error_html,
            venue_panel=venue_panel,
            venue_select=venue_select,
            ref_code=esc(referral_code),
            ref_link=esc(f"https://{base_domain}/signup?ref={referral_code}"),
            ref_signups=str(ref_signups),
            ref_events=str(ref_events),
            ref_pending=f"${pending:,.2f}",
            charity=esc(user["charity"] or ""),
            charity_note=(
                f'Commissions currently donated to <strong>{esc(user["charity"])}</strong>.'
                if user["charity"] else
                "Commissions are paid to you. Prefer to give back? Name a charity below."
            ),
        )

    @app.post("/api/events")
    def create_event(request: Request, title: str = Form(...), slug: str = Form(...),
                     guest_password: str = Form(...), event_date: str = Form(""),
                     custom_domain: str = Form(""), venue_id: str = Form("")):
        user = current_user(request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if venue_id:
            with db() as conn:
                owned = conn.execute(
                    "SELECT 1 FROM venues WHERE id = ? AND owner_id = ?",
                    (venue_id, user["id"]),
                ).fetchone()
            if owned is None:
                venue_id = ""
        title = re.sub(r"\s+", " ", title).strip()[:80]
        slug = slug.strip().lower()
        custom_domain = custom_domain.strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
        if not title:
            return RedirectResponse("/dashboard?error=Please+give+the+event+a+title.", status_code=303)
        if not SLUG_RE.fullmatch(slug) or slug in RESERVED_SLUGS:
            return RedirectResponse(
                "/dashboard?error=Web+address+must+be+3-40+letters,+numbers+or+dashes.",
                status_code=303,
            )
        if len(guest_password) < 4:
            return RedirectResponse(
                "/dashboard?error=Guest+password+must+be+at+least+4+characters.",
                status_code=303,
            )
        if custom_domain and not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", custom_domain):
            return RedirectResponse("/dashboard?error=That+custom+domain+doesn't+look+valid.", status_code=303)
        event_id = uuid.uuid4().hex
        try:
            with db() as conn:
                if slug_in_use(conn, slug):
                    return RedirectResponse("/dashboard?error=That+web+address+is+already+taken.", status_code=303)
                conn.execute(
                    "INSERT INTO events (id, owner_id, slug, title, event_date, guest_password, custom_domain, created_at, venue_id)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (event_id, user["id"], slug, title, event_date.strip()[:40],
                     guest_password, custom_domain or None, int(time.time()),
                     venue_id or None),
                )
        except sqlite3.IntegrityError:
            return RedirectResponse("/dashboard?error=That+web+address+is+already+taken.", status_code=303)
        event_dirs(data_dir, event_id)
        return RedirectResponse("/dashboard", status_code=303)

    @app.get("/api/events/{event_id}/qr.png")
    def event_qr(request: Request, event_id: str):
        user = current_user(request)
        if user is None:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        with db() as conn:
            event = conn.execute(
                "SELECT * FROM events WHERE id = ? AND owner_id = ?", (event_id, user["id"])
            ).fetchone()
        if event is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        img = qrcode.make(event_url(event))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(buf.getvalue(), media_type="image/png")

    # ---- venue (white-label) management ------------------------------------

    def owned_venue(request: Request, venue_id: str) -> sqlite3.Row | None:
        user = current_user(request)
        if user is None:
            return None
        with db() as conn:
            return conn.execute(
                "SELECT * FROM venues WHERE id = ? AND owner_id = ?",
                (venue_id, user["id"]),
            ).fetchone()

    @app.post("/api/venues")
    def create_venue(request: Request, name: str = Form(...), slug: str = Form(...),
                     venue_type: str = Form(""), custom_domain: str = Form("")):
        user = current_user(request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        name = re.sub(r"\s+", " ", name).strip()[:80]
        slug = slug.strip().lower()
        custom_domain = custom_domain.strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
        if not name or not SLUG_RE.fullmatch(slug) or slug in RESERVED_SLUGS:
            return RedirectResponse("/dashboard?error=Venue+needs+a+name+and+a+valid+web+address.", status_code=303)
        if custom_domain and not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", custom_domain):
            return RedirectResponse("/dashboard?error=That+venue+domain+doesn't+look+valid.", status_code=303)
        with db() as conn:
            if user_venue(conn, user["id"]) is not None:
                return RedirectResponse("/dashboard?error=You+already+have+a+venue.", status_code=303)
            if slug_in_use(conn, slug):
                return RedirectResponse("/dashboard?error=That+web+address+is+already+taken.", status_code=303)
            venue_id = uuid.uuid4().hex
            try:
                conn.execute(
                    "INSERT INTO venues (id, owner_id, name, slug, venue_type, custom_domain, created_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (venue_id, user["id"], name, slug, venue_type.strip()[:40],
                     custom_domain or None, int(time.time())),
                )
            except sqlite3.IntegrityError:
                return RedirectResponse("/dashboard?error=That+web+address+or+domain+is+already+taken.", status_code=303)
        venue_dir(data_dir, venue_id)
        return RedirectResponse("/dashboard", status_code=303)

    @app.post("/api/venues/{venue_id}/brand")
    def venue_brand(request: Request, venue_id: str, tagline: str = Form(""),
                    headline: str = Form(""), about: str = Form(""),
                    accent: str = Form(""), custom_domain: str = Form("")):
        venue = owned_venue(request, venue_id)
        if venue is None:
            return RedirectResponse("/login", status_code=303)
        if accent and not re.fullmatch(r"#[0-9a-fA-F]{6}", accent):
            accent = venue["accent"]
        custom_domain = custom_domain.strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
        if custom_domain and not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", custom_domain):
            return RedirectResponse("/dashboard?error=That+venue+domain+doesn't+look+valid.", status_code=303)
        with db() as conn:
            try:
                conn.execute(
                    "UPDATE venues SET tagline=?, headline=?, about=?, accent=?, custom_domain=? WHERE id=?",
                    (tagline.strip()[:120], headline.strip()[:120], about.strip()[:1200],
                     accent or venue["accent"], custom_domain or None, venue_id),
                )
            except sqlite3.IntegrityError:
                return RedirectResponse("/dashboard?error=That+domain+is+already+in+use.", status_code=303)
        return RedirectResponse("/dashboard", status_code=303)

    @app.post("/api/venues/{venue_id}/logo")
    async def venue_logo(request: Request, venue_id: str, logo: UploadFile = File(...)):
        venue = owned_venue(request, venue_id)
        if venue is None:
            return RedirectResponse("/login", status_code=303)
        data = await logo.read(8 * 1024 * 1024)
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
            img = img.convert("RGBA")
            img.thumbnail((600, 600))
        except Exception:
            return RedirectResponse("/dashboard?error=That+logo+couldn't+be+read+as+an+image.", status_code=303)
        vdir = venue_dir(data_dir, venue_id)
        img.save(vdir / "logo.png", "PNG")
        updates = {"logo": "logo.png"}
        if venue["accent"] == "#e85d8a":  # untouched default: adopt the logo's color
            extracted = dominant_color(data)
            if extracted:
                updates["accent"] = extracted
        with db() as conn:
            conn.execute(
                f"UPDATE venues SET {', '.join(f'{k}=?' for k in updates)} WHERE id=?",
                (*updates.values(), venue_id),
            )
        return RedirectResponse("/dashboard", status_code=303)

    @app.post("/api/venues/{venue_id}/photos")
    async def venue_photos(request: Request, venue_id: str,
                           files: list[UploadFile] = File(...)):
        venue = owned_venue(request, venue_id)
        if venue is None:
            return RedirectResponse("/login", status_code=303)
        photos_path = venue_dir(data_dir, venue_id) / "photos"
        existing = len(list(photos_path.glob("*.jpg")))
        for upload_file in files[: max(0, 12 - existing)]:
            data = await upload_file.read(MAX_IMAGE_BYTES)
            try:
                img = Image.open(io.BytesIO(data))
                img.load()
                img = ImageOps.exif_transpose(img).convert("RGB")
                img.thumbnail((1600, 1600))
                img.save(photos_path / f"{uuid.uuid4().hex}.jpg", "JPEG", quality=88)
            except Exception:
                continue
        return RedirectResponse("/dashboard", status_code=303)

    @app.post("/api/venues/{venue_id}/photos/delete")
    def venue_photo_delete(request: Request, venue_id: str, filename: str = Form(...)):
        venue = owned_venue(request, venue_id)
        if venue is None:
            return RedirectResponse("/login", status_code=303)
        if re.fullmatch(r"[0-9a-f]{32}\.jpg", filename):
            (venue_dir(data_dir, venue_id) / "photos" / filename).unlink(missing_ok=True)
        return RedirectResponse("/dashboard", status_code=303)

    @app.post("/api/venues/{venue_id}/ai-brand")
    def venue_ai_brand(request: Request, venue_id: str, notes: str = Form("")):
        venue = owned_venue(request, venue_id)
        if venue is None:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        if not ai_agents.ai_enabled():
            return JSONResponse({"error": "AI features aren't enabled on this server"}, status_code=503)
        logo_bytes = None
        logo_file = venue_dir(data_dir, venue_id) / "logo.png"
        if venue["logo"] and logo_file.exists():
            logo_bytes = logo_file.read_bytes()
        kit = ai_agents.generate_brand_kit(
            venue["name"], venue["venue_type"], notes.strip()[:600], logo_bytes
        )
        if kit is None:
            return JSONResponse({"error": "couldn't generate a brand kit"}, status_code=502)
        with db() as conn:
            conn.execute(
                "UPDATE venues SET tagline=?, headline=?, about=?, accent=? WHERE id=?",
                (kit["tagline"][:120], kit["headline"][:120], kit["about"][:1200],
                 kit["accent"], venue_id),
            )
        return kit

    @app.get("/partners", response_class=HTMLResponse)
    def partners(request: Request):
        if resolve_event(request) is not None:
            return RedirectResponse("/", status_code=303)
        return page("partners", base=esc(base_domain))

    @app.get("/api/referral-qr.png")
    def referral_qr(request: Request):
        user = current_user(request)
        if user is None:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        code = user["referral_code"]
        if not code:
            with db() as conn:
                code = new_referral_code()
                conn.execute("UPDATE users SET referral_code = ? WHERE id = ?",
                             (code, user["id"]))
        img = qrcode.make(f"https://{base_domain}/signup?ref={code}")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(
            buf.getvalue(), media_type="image/png",
            headers={"Content-Disposition": 'attachment; filename="my-referral-qr.png"'},
        )

    @app.post("/api/referral-charity")
    def set_charity(request: Request, charity: str = Form("")):
        user = current_user(request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        charity = re.sub(r"\s+", " ", charity).strip()[:120]
        with db() as conn:
            conn.execute("UPDATE users SET charity = ? WHERE id = ?", (charity, user["id"]))
        return RedirectResponse("/dashboard", status_code=303)

    @app.get("/health")
    def health():
        return {"ok": True}

    # =======================================================================
    # tenant (event gallery) handlers
    # =======================================================================

    def tenant_gallery(request: Request, event: sqlite3.Row):
        if gallery_role(request, event) is None:
            return RedirectResponse("/login", status_code=303)
        return page("gallery", title=esc(event["title"]), **event_brand(event))

    def tenant_login(request: Request, event: sqlite3.Row, password: str):
        ip = request.client.host if request.client else "unknown"
        key = f"guest:{event['id']}:{ip}"
        brand = event_brand(event)
        if too_many_attempts(key):
            return page("guest_login", title=esc(event["title"]), **brand,
                        error=err_html("Too many attempts - please wait a few minutes."))
        if not hmac.compare_digest(password, event["guest_password"]):
            record_attempt(key)
            return page("guest_login", title=esc(event["title"]), **brand,
                        error=err_html("That password isn't right."))
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            GUEST_COOKIE, make_guest_token(event["id"]),
            max_age=SESSION_TTL_SECONDS, httponly=True, samesite="lax",
        )
        return response

    @app.get("/api/photos")
    def list_photos(request: Request, q: str = "", highlights: bool = False):
        event = resolve_event(request)
        if event is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        role = gallery_role(request, event)
        if role is None:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        dirs = event_dirs(data_dir, event["id"])
        photos = []
        for meta_file in dirs["meta"].glob("*.json"):
            try:
                photos.append(json.loads(meta_file.read_text()))
            except (OSError, json.JSONDecodeError):
                continue
        q = q.strip().lower()
        if q:
            def matches(p):
                haystack = " ".join([
                    p.get("caption", ""), " ".join(p.get("tags", [])),
                    p.get("uploader", ""), p.get("original_name", ""),
                ]).lower()
                return all(term in haystack for term in q.split())
            photos = [p for p in photos if matches(p)]
        if highlights:
            photos = [p for p in photos if p.get("quality", 0) >= 8]
        photos.sort(key=lambda p: p.get("uploaded_at", 0), reverse=True)
        return {
            "photos": photos,
            "is_admin": role == "admin",
            "ai_enabled": ai_agents.ai_enabled(),
        }

    def _caption_task(event_id: str, photo_id: str) -> None:
        """Background: run the curator agent on one photo, merge into its meta."""
        dirs = event_dirs(data_dir, event_id)
        thumb = dirs["thumbs"] / f"{photo_id}.jpg"
        meta_file = dirs["meta"] / f"{photo_id}.json"
        if not thumb.exists() or not meta_file.exists():
            return
        result = ai_agents.caption_photo(thumb)
        if not result:
            return
        try:
            meta = json.loads(meta_file.read_text())
            meta.update(caption=result["caption"], tags=result["tags"],
                        quality=result["quality"])
            meta_file.write_text(json.dumps(meta))
        except (OSError, json.JSONDecodeError, KeyError):
            pass

    @app.post("/api/upload")
    async def upload(request: Request, background: BackgroundTasks,
                     files: list[UploadFile] = File(...), uploader: str = Form("")):
        event = resolve_event(request)
        if event is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if gallery_role(request, event) is None:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        dirs = event_dirs(data_dir, event["id"])
        uploader = re.sub(r"\s+", " ", uploader).strip()[:60]
        saved, errors = [], []
        for upload_file in files:
            original_name = upload_file.filename or "photo"
            ext = Path(original_name).suffix.lower()
            try:
                if ext in VIDEO_EXTENSIONS:
                    saved.append(await _save_video(upload_file, original_name, uploader, ext, dirs))
                else:
                    meta = await _save_photo(upload_file, original_name, uploader, ext, dirs)
                    saved.append(meta)
                    if ai_agents.ai_enabled():
                        background.add_task(_caption_task, event["id"], meta["id"])
            except PhotoError as exc:
                errors.append({"file": original_name, "reason": str(exc)})
        return {"saved": saved, "errors": errors}

    @app.post("/api/events/{event_id}/recap")
    def event_recap(request: Request, event_id: str):
        user = current_user(request)
        if user is None:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        with db() as conn:
            event = conn.execute(
                "SELECT * FROM events WHERE id = ? AND owner_id = ?", (event_id, user["id"])
            ).fetchone()
        if event is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if not ai_agents.ai_enabled():
            return JSONResponse(
                {"error": "AI features aren't enabled on this server"}, status_code=503
            )
        dirs = event_dirs(data_dir, event["id"])
        photos = []
        for meta_file in dirs["meta"].glob("*.json"):
            try:
                photos.append(json.loads(meta_file.read_text()))
            except (OSError, json.JSONDecodeError):
                continue
        recap = ai_agents.generate_recap(event["title"], photos)
        if recap is None:
            return JSONResponse({"error": "couldn't generate a recap"}, status_code=502)
        (data_dir / "events" / event["id"] / "recap.txt").write_text(recap)
        return {"recap": recap}

    @app.delete("/api/photos/{photo_id}")
    def delete_photo(request: Request, photo_id: str):
        event = resolve_event(request)
        if event is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        role = gallery_role(request, event)
        if role is None:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        if role != "admin":
            return JSONResponse({"error": "admin only"}, status_code=403)
        dirs = event_dirs(data_dir, event["id"])
        path = _find_media_file(dirs["photos"], photo_id)
        if path is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        moves = [
            (path, "photos"),
            (dirs["thumbs"] / f"{photo_id}.jpg", "thumbs"),
            (dirs["meta"] / f"{photo_id}.json", "meta"),
        ]
        for src, sub in moves:
            if src.exists():
                dest = dirs["trash"] / sub
                dest.mkdir(exist_ok=True)
                shutil.move(str(src), dest / src.name)
        return {"deleted": photo_id}

    @app.get("/photos/{photo_id}")
    def photo(request: Request, photo_id: str, download: bool = False):
        event = resolve_event(request)
        if event is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if gallery_role(request, event) is None:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        dirs = event_dirs(data_dir, event["id"])
        path = _find_media_file(dirs["photos"], photo_id)
        if path is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        headers = {}
        if download:
            name = photo_id
            meta_file = dirs["meta"] / f"{photo_id}.json"
            if meta_file.exists():
                try:
                    name = json.loads(meta_file.read_text()).get("original_name", name)
                except (OSError, json.JSONDecodeError):
                    pass
            safe = re.sub(r'[^\w.\- ]', "_", name) or photo_id
            headers["Content-Disposition"] = f'attachment; filename="{safe}"'
        media_type = VIDEO_MEDIA_TYPES.get(path.suffix.lower())
        return FileResponse(path, headers=headers, media_type=media_type)

    @app.get("/thumbs/{photo_id}")
    def thumb(request: Request, photo_id: str):
        event = resolve_event(request)
        if event is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if gallery_role(request, event) is None:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        dirs = event_dirs(data_dir, event["id"])
        path = dirs["thumbs"] / f"{photo_id}.jpg"
        if path.exists():
            return FileResponse(path, media_type="image/jpeg")
        full = _find_media_file(dirs["photos"], photo_id)
        if full is not None:
            return FileResponse(full)
        return JSONResponse({"error": "not found"}, status_code=404)

    return app


app = create_app()
