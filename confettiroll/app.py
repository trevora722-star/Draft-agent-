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

import csv
import hmac
import hashlib
import io
import json
import math
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
from PIL import Image, ImageEnhance, ImageOps
import qrcode

import ai_agents
import billing
import book as book_maker
import cards as card_maker
import google_auth
import mailer

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
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'other',
                interest TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'tradeshow',
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                package TEXT NOT NULL,
                amount_cents INTEGER NOT NULL,
                stripe_session TEXT NOT NULL DEFAULT '',
                event_id TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS credits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                delta INTEGER NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS prospects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                business TEXT NOT NULL DEFAULT '',
                type TEXT NOT NULL DEFAULT 'planner',
                email TEXT NOT NULL,
                city TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'new',
                pitch_subject TEXT NOT NULL DEFAULT '',
                pitch_body TEXT NOT NULL DEFAULT '',
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
            CREATE TABLE IF NOT EXISTS members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL REFERENCES events(id),
                name TEXT NOT NULL,
                code TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE(event_id, code)
            );
            CREATE TABLE IF NOT EXISTS selections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                member_id INTEGER NOT NULL REFERENCES members(id),
                photo_id TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE(member_id, photo_id)
            );
            CREATE TABLE IF NOT EXISTS book_picks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                photo_id TEXT NOT NULL,
                voter TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE(event_id, photo_id, voter)
            );
            CREATE TABLE IF NOT EXISTS subscribers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL REFERENCES events(id),
                email TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL,
                notified_at INTEGER,
                UNIQUE(event_id, email)
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
        if "event_type" not in event_cols:
            conn.execute(
                "ALTER TABLE events ADD COLUMN event_type TEXT NOT NULL DEFAULT 'party'"
            )
        if "uploads_locked" not in event_cols:
            conn.execute(
                "ALTER TABLE events ADD COLUMN uploads_locked INTEGER NOT NULL DEFAULT 0"
            )
        if "paid" not in event_cols:
            conn.execute("ALTER TABLE events ADD COLUMN paid INTEGER NOT NULL DEFAULT 0")
            # events created before the free-week model keep their access
            conn.execute("UPDATE events SET paid = 1")
        if "trial_notified" not in event_cols:
            conn.execute(
                "ALTER TABLE events ADD COLUMN trial_notified INTEGER NOT NULL DEFAULT 0"
            )
        if "guest_upload_limit" not in event_cols:
            conn.execute(
                "ALTER TABLE events ADD COLUMN guest_upload_limit INTEGER NOT NULL DEFAULT 0"
            )


def new_referral_code() -> str:
    # Short, human-friendly, unambiguous (no 0/O/1/l).
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


def new_member_code() -> str:
    # Personal access code a student types on their phone at the dance.
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(6))


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
    # Estimated commission per referred event (50% of a ~$49 first package),
    # tracked as pending and payable once billing launches.
    referral_fee = float(os.environ.get("CR_REFERRAL_FEE", "25"))
    data_dir = Path(os.environ.get("CR_DATA_DIR", BASE_DIR / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "confettiroll.sqlite"
    init_db(db_path)
    secret = _load_secret(data_dir)

    tpl = {
        name: (BASE_DIR / "templates" / f"{name}.html").read_text()
        for name in ("landing", "signup", "login", "dashboard", "guest_login",
                     "gallery", "partners", "venue", "stream", "kiosk", "outreach",
                     "celebrations", "flipbook")
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

    def make_member_token(event_id: str, member_id: int) -> str:
        # Personal (per-student) session on a tagged event — the payload keeps
        # both ids in one dot-free segment so parse_token stays unchanged.
        payload = f"m.{event_id}:{member_id}.{int(time.time()) + SESSION_TTL_SECONDS}"
        return f"{payload}.{sign(payload)}"

    def make_staff_token(event_id: str) -> str:
        # Staff uploader (e.g. the Vice Principal) on a tagged event.
        payload = f"s.{event_id}.{int(time.time()) + SESSION_TTL_SECONDS}"
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

    TRIAL_DAYS = 7

    def trial_state(event: sqlite3.Row) -> dict:
        """Every event starts with a free week; a credit or purchase unlocks
        it for good. Photos are never deleted - an expired trial just stops
        new uploads until the host upgrades."""
        if event["paid"]:
            return {"paid": True, "days_left": None, "expired": False}
        elapsed = time.time() - event["created_at"]
        left_days = TRIAL_DAYS - elapsed / 86400
        return {
            "paid": False,
            "days_left": max(0, math.ceil(left_days)),
            "expired": left_days <= 0,
        }

    def is_tagged_event(event: sqlite3.Row) -> bool:
        return event["event_type"] == "prom"

    def is_gala(event: sqlite3.Row) -> bool:
        """Private events & galas: guests view with the shared password;
        only table hosts (roster members) add photos, credited to their
        table. Everyone sees the whole album."""
        return event["event_type"] == "gala"

    def current_member(request: Request, event: sqlite3.Row) -> sqlite3.Row | None:
        payload = parse_token(request.cookies.get(GUEST_COOKIE), "m")
        if payload is None or ":" not in payload:
            return None
        event_id, _, member_id = payload.partition(":")
        if event_id != event["id"] or not member_id.isdigit():
            return None
        with db() as conn:
            return conn.execute(
                "SELECT * FROM members WHERE id = ? AND event_id = ?",
                (int(member_id), event["id"]),
            ).fetchone()

    def gallery_role(request: Request, event: sqlite3.Row) -> str | None:
        user = current_user(request)
        if user is not None and user["id"] == event["owner_id"]:
            return "admin"
        if is_tagged_event(event):
            # Tagged events (proms, grad nights): students sign in with their
            # own code; the designated staff uploader (e.g. the Vice
            # Principal) signs in with the event's upload code.
            if current_member(request, event) is not None:
                return "member"
            if parse_token(request.cookies.get(GUEST_COOKIE), "s") == event["id"]:
                return "staff"
            return None
        if is_gala(event):
            if current_member(request, event) is not None:
                return "member"  # a table host
            event_id = parse_token(request.cookies.get(GUEST_COOKIE), "g")
            if event_id == event["id"]:
                return "guest"  # view-only attendee
            return None
        event_id = parse_token(request.cookies.get(GUEST_COOKIE), "g")
        if event_id == event["id"]:
            return "guest"
        return None

    def too_many_attempts(key: str, limit: int = LOGIN_ATTEMPT_LIMIT) -> bool:
        now = time.time()
        attempts = [t for t in login_attempts.get(key, []) if now - t < LOGIN_ATTEMPT_WINDOW]
        login_attempts[key] = attempts
        return len(attempts) >= limit

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
        sample_labels = {
            "vineyard-wedding": "📖 A vineyard wedding",
            "fiftieth-birthday": "📖 A 50th by the pool",
            "grad-gala-prom": "📖 A high school prom",
        }
        sample_links = "".join(
            f'<a href="/samples/{slug}" style="display:inline-block;'
            ' margin:6px 8px; padding:10px 20px; border:1px solid var(--line); border-radius:999px;'
            ' color:var(--violet); text-decoration:none; font-size:14px;'
            f' font-family:\'Helvetica Neue\', Arial, sans-serif">{label}</a>'
            for slug, label in sample_labels.items()
            if (BASE_DIR / "static" / "samples" / f"{slug}.json").exists()
        )
        samples_html = (
            '<p style="text-align:center; margin-top:18px; color:var(--soft); font-size:14.5px">'
            "Flip through a sample book:</p>"
            f'<p style="text-align:center">{sample_links}</p>'
        ) if sample_links else ""
        return page(
            "landing", base=base_domain,
            samples=samples_html,
            p_celebration=billing.price_label("celebration"),
            p_heirloom=billing.price_label("heirloom"),
            p_pack=billing.price_label("wholesale10"),
            p_prom=billing.price_label("prom"),
            p_venue_boutique=f"${billing.VENUE_TIERS['boutique']['monthly_cents'] // 100}",
            p_venue_estate=f"${billing.VENUE_TIERS['estate']['monthly_cents'] // 100}",
            p_venue_grand=f"${billing.VENUE_TIERS['grand']['monthly_cents'] // 100}",
        )

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
        return page("signup", error="", ref=esc(ref.strip()[:16]), google_btn=google_button(ref.strip()[:16]))

    @app.post("/signup")
    def signup(request: Request, name: str = Form(""), email: str = Form(...),
               password: str = Form(...), ref: str = Form("")):
        email = email.strip().lower()
        name = re.sub(r"\s+", " ", name).strip()[:80]
        ref = ref.strip()[:16]
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            return page("signup", error=err_html("That doesn't look like an email address."), ref=esc(ref), google_btn=google_button(ref))
        if len(password) < 8:
            return page("signup", error=err_html("Password must be at least 8 characters."), ref=esc(ref), google_btn=google_button(ref))
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
                return page("signup", error=err_html("An account with that email already exists."), ref=esc(ref), google_btn=google_button(ref))
        response = RedirectResponse("/dashboard", status_code=303)
        response.set_cookie(ORG_COOKIE, make_org_token(user_id), **org_cookie_kwargs(request))
        return response

    # ---- Sign in with Google (identity only — name + email, never photos) --

    def google_button(ref: str = "") -> str:
        if not google_auth.enabled():
            return ""
        href = "/auth/google" + (f"?ref={esc(ref)}" if ref else "")
        return (
            f'<a href="{href}" style="display:flex; align-items:center; justify-content:center;'
            ' gap:10px; margin-top:14px; padding:12px; font-size:14.5px; border:1px solid var(--line);'
            ' border-radius:8px; background:#fff; color:var(--ink); text-decoration:none;'
            ' font-family:\'Helvetica Neue\', Arial, sans-serif">'
            '<svg width="18" height="18" viewBox="0 0 48 48"><path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9 3.5l6.7-6.7C35.6 2.4 30.1 0 24 0 14.6 0 6.5 5.4 2.6 13.2l7.8 6.1C12.3 13.4 17.7 9.5 24 9.5z"/><path fill="#4285F4" d="M46.5 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.7c-.6 3-2.3 5.5-4.8 7.2l7.4 5.8c4.4-4.1 7.2-10.1 7.2-17.5z"/><path fill="#FBBC05" d="M10.4 28.7a14.5 14.5 0 0 1 0-9.4l-7.8-6.1a24 24 0 0 0 0 21.6l7.8-6.1z"/><path fill="#34A853" d="M24 48c6.1 0 11.2-2 15-5.5l-7.4-5.8c-2 1.4-4.6 2.2-7.6 2.2-6.3 0-11.7-3.9-13.6-9.4l-7.8 6.1C6.5 42.6 14.6 48 24 48z"/></svg>'
            "Continue with Google</a>"
            '<p style="margin-top:8px; font-size:12.5px; color:var(--soft); text-align:center;'
            ' font-family:\'Helvetica Neue\', Arial, sans-serif">Shares only your name and email'
            " — never your Google Photos.</p>"
        )

    def google_redirect_uri(request: Request) -> str:
        return str(request.base_url).rstrip("/") + "/auth/google/callback"

    def make_google_state(ref: str) -> str:
        payload = f"ga.{int(time.time()) + 600}.{ref or '-'}"
        return f"{payload}.{sign(payload)}"

    def parse_google_state(state: str) -> str | None:
        """Returns the referral code ('' if none) or None if invalid."""
        parts = (state or "").split(".")
        if len(parts) != 4 or parts[0] != "ga":
            return None
        payload = ".".join(parts[:3])
        if not hmac.compare_digest(parts[3], sign(payload)):
            return None
        if not parts[1].isdigit() or int(parts[1]) <= time.time():
            return None
        return "" if parts[2] == "-" else parts[2]

    @app.get("/auth/google")
    def google_start(request: Request, ref: str = ""):
        if not google_auth.enabled():
            return RedirectResponse("/login", status_code=303)
        return RedirectResponse(google_auth.auth_url(
            google_redirect_uri(request), make_google_state(ref.strip()[:16])
        ), status_code=303)

    @app.get("/auth/google/callback")
    def google_callback(request: Request, code: str = "", state: str = ""):
        if not google_auth.enabled():
            return RedirectResponse("/login", status_code=303)
        ref = parse_google_state(state)
        if ref is None or not code:
            return page("login", error=err_html("Google sign-in didn't complete - please try again."),
                        google_btn=google_button())
        identity = google_auth.exchange(code, google_redirect_uri(request))
        if identity is None:
            return page("login", error=err_html("Google sign-in didn't complete - please try again."),
                        google_btn=google_button())
        with db() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE email = ?", (identity["email"],)
            ).fetchone()
            if user is None:
                referrer = conn.execute(
                    "SELECT id FROM users WHERE referral_code = ?", (ref,)
                ).fetchone() if ref else None
                cur = conn.execute(
                    "INSERT INTO users (email, name, password_hash, created_at, referral_code, referred_by)"
                    " VALUES (?,?,?,?,?,?)",
                    (identity["email"], identity["name"], "", int(time.time()),
                     new_referral_code(), referrer["id"] if referrer else None),
                )
                user_id = cur.lastrowid
            else:
                user_id = user["id"]
        response = RedirectResponse("/dashboard", status_code=303)
        response.set_cookie(ORG_COOKIE, make_org_token(user_id), **org_cookie_kwargs(request))
        return response

    def guest_login_page(event: sqlite3.Row, error: str = "") -> HTMLResponse:
        if is_tagged_event(event):
            sub = ("Students: enter your personal access code to see your "
                   "photos. Event staff sign in with the upload code.")
            label = "Your access code"
        elif is_gala(event):
            sub = ("Enter the event password to watch the album. Table hosts "
                   "sign in with their personal host code to add photos.")
            label = "Password or host code"
        else:
            sub = "Enter the event password to see and share photos."
            label = "Event password"
        return page("guest_login", title=esc(event["title"]), error=err_html(error),
                    sub=sub, label=label, **event_brand(event))

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        event = resolve_event(request)
        if event is not None:
            return guest_login_page(event)
        return page("login", error="", google_btn=google_button())

    @app.post("/login")
    def login(request: Request, email: str = Form(""), password: str = Form("")):
        event = resolve_event(request)
        if event is not None:
            return tenant_login(request, event, password, email)
        ip = request.client.host if request.client else "unknown"
        if too_many_attempts(f"org:{ip}"):
            return page("login", error=err_html("Too many attempts - please wait a few minutes."), google_btn=google_button())
        with db() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)
            ).fetchone()
        if user is None or not verify_password(password, user["password_hash"]):
            record_attempt(f"org:{ip}")
            return page("login", error=err_html("Wrong email or password."), google_btn=google_button())
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
            balance = credit_balance(conn, user["id"])
            sub_counts = dict(conn.execute(
                "SELECT event_id, COUNT(*) FROM subscribers GROUP BY event_id"
            ).fetchall())
        rows = []
        for ev in events:
            ts = trial_state(ev)
            if ts["paid"]:
                trial_badge = ""
                unlock_html = ""
            else:
                trial_badge = (
                    " · ⏰ free week ended" if ts["expired"]
                    else f" · 🕐 free week: {ts['days_left']} day{'' if ts['days_left'] == 1 else 's'} left"
                )
                unlock_now = ("Your free week has ended - photos are safe, but uploads are paused. "
                              if ts["expired"] else "")
                credit_btn = (
                    f'<form method="post" action="/api/events/{ev["id"]}/apply-credit" style="display:inline">'
                    f'<button class="mini" type="submit" style="cursor:pointer; background:var(--pink); color:#fff; border-color:var(--pink)">Use 1 credit to unlock</button></form>'
                    if balance > 0 else
                    f'<button class="mini buy-btn" data-package="celebration" data-event="{ev["id"]}" type="button" style="background:var(--pink); color:#fff; border-color:var(--pink)">Unlock forever - $49</button>'
                )
                unlock_html = (
                    f'<p style="margin-top:10px; font-size:14px; color:var(--soft)">{unlock_now}'
                    f'Unlock this album to keep it forever - unlimited photos, the live wall, and the keepsake book. {credit_btn}</p>'
                )
            # gentle nudge as the free week runs out (once, if email works)
            if (not ts["paid"] and not ev["trial_notified"]
                    and (ts["expired"] or (ts["days_left"] or 0) <= 2)
                    and mailer.enabled()):
                when = ("has ended" if ts["expired"]
                        else f"ends in {ts['days_left']} day(s)")
                body = (
                    f"Hi {user['name'] or 'there'},\n\n"
                    f"The free week for \"{ev['title']}\" {when}. All the photos "
                    f"your guests shared are safe - unlock the album to keep it "
                    f"forever and keep the uploads coming:\n\n"
                    f"https://{base_domain}/dashboard\n\n"
                    f"- ConfettiRoll"
                )
                if mailer.send(user["email"],
                               f"Your free week for {ev['title']} is ending", body):
                    with db() as conn:
                        conn.execute(
                            "UPDATE events SET trial_notified = 1 WHERE id = ?", (ev["id"],)
                        )
            count = len(list((data_dir / "events" / ev["id"] / "meta").glob("*.json"))) \
                if (data_dir / "events" / ev["id"] / "meta").exists() else 0
            url = event_url(ev)
            if ev["event_type"] in ("prom", "gala"):
                gala = ev["event_type"] == "gala"
                with db() as conn:
                    roster = conn.execute(
                        "SELECT * FROM members WHERE event_id = ? ORDER BY name",
                        (ev["id"],),
                    ).fetchall()
                roster_label = "Table hosts" if gala else "Class roster"
                roster_unit = "table host" if gala else "student"
                roster_placeholder = ("One table host per line&#10;Table 1 - The Smith party&#10;Table 2 - Chen family"
                                      if gala else "One student per line&#10;Ava Martin&#10;Noah Chen")
                roster_rows = "".join(
                    f'''<tr><td>{esc(m["name"])}</td><td><code>{esc(m["code"])}</code></td>
                        <td><form method="post" action="/api/events/{ev['id']}/members/{m['id']}/delete" style="display:inline">
                        <button type="submit" style="border:none; background:none; color:var(--soft); cursor:pointer">✕</button>
                        </form></td></tr>'''
                    for m in roster
                ) or '<tr><td colspan="3" style="color:var(--soft); font-style:italic">No students yet — paste the class list below.</td></tr>'
                if gala:
                    cred_line = (
                        f'<p class="event-cred">🥂 Private event — guests view the album with the password '
                        f'<code>{esc(ev["guest_password"])}</code>; only the table hosts below add photos, '
                        f'each with their own host code.</p>'
                    )
                else:
                    cred_line = (
                        f'<p class="event-cred">🎓 Tagged event — students sign in with their own codes '
                        f'and only see photos they\'re tagged in. Photos are uploaded by your designated '
                        f'staff member (e.g. the Vice Principal) with the upload code: '
                        f'<code>{esc(ev["guest_password"])}</code></p>'
                    )
                roster_panel = f"""
              <details style="margin-top:12px">
                <summary style="cursor:pointer; font-size:14px; color:var(--soft)">{roster_label} — {len(roster)} {roster_unit}{'' if len(roster) == 1 else 's'}</summary>
                <table style="width:100%; margin-top:10px; font-size:14px; border-collapse:collapse">{roster_rows}</table>
                <form method="post" action="/api/events/{ev['id']}/members" style="margin-top:10px">
                  <textarea name="names" rows="3" placeholder="{roster_placeholder}"
                    style="width:100%; padding:10px 12px; font-size:14px; border:1px solid var(--line); border-radius:8px; background:#fdfcfa; font-family:inherit"></textarea>
                  <button class="mini" type="submit" style="cursor:pointer; background:none; margin-top:8px">Add {roster_unit}s</button>
                  <a class="mini" href="/api/events/{ev['id']}/members.csv">Download codes CSV</a>
                </form>
              </details>"""
            else:
                cred_line = f'<p class="event-cred">Guest password: <code>{esc(ev["guest_password"])}</code></p>'
                roster_panel = ""
            rows.append(f"""
            <div class="event">
              <div class="event-head">
                <h3>{esc(ev['title'])}</h3>
                <span class="count">{count} item{'' if count == 1 else 's'}{' · 🔒 album closed' if ev['uploads_locked'] else ''}{trial_badge}</span>
              </div>
              <p class="event-link"><a href="{esc(url)}" target="_blank">{esc(url.replace('https://', ''))}</a></p>
              {cred_line}
              <form method="post" action="/api/events/{ev['id']}/settings"
                    style="display:flex; gap:8px; align-items:center; margin:6px 0 0; font-size:13.5px; color:var(--soft); font-family:'Helvetica Neue', Arial, sans-serif">
                Photos per guest:
                <input type="number" name="guest_upload_limit" value="{ev['guest_upload_limit']}"
                       min="0" max="500" style="width:70px; padding:6px 8px; font-size:13.5px; border:1px solid var(--line); border-radius:6px; background:#fdfcfa">
                <button class="mini" type="submit" style="cursor:pointer; background:none">Save</button>
                <span>(0 = unlimited{' · currently ' + str(ev['guest_upload_limit']) + ' each' if ev['guest_upload_limit'] else ''})</span>
              </form>
              <form method="post" action="/api/events/{ev['id']}/card-photo" enctype="multipart/form-data"
                    style="display:flex; gap:8px; align-items:center; margin:6px 0 0; font-size:13.5px; color:var(--soft); font-family:'Helvetica Neue', Arial, sans-serif">
                Table-card photo/logo:
                <input type="file" name="photo" accept="image/*" required style="font-size:12.5px">
                <button class="mini" type="submit" style="cursor:pointer; background:none">Upload</button>
                <span>{'✓ set' if (data_dir / 'events' / ev['id'] / 'card.jpg').exists() else '(couple photo or company logo)'}</span>
              </form>
              <p class="event-actions">
                <a class="mini" href="{esc(url)}" target="_blank">Open gallery</a>
                <a class="mini" href="/api/events/{ev['id']}/qr.png" download="{esc(ev['slug'])}-qr.png">Download QR code</a>
                <a class="mini" href="/api/events/{ev['id']}/table-cards.pdf">🪧 Table cards (PDF)</a>
                <a class="mini" href="{esc(url)}/stream" target="_blank">📺 Live slideshow</a>
                <button class="mini recap-btn" data-event="{ev['id']}" type="button">✨ AI recap</button>
                <button class="mini book-btn" data-event="{ev['id']}" data-url="{esc(url)}" type="button">📖 Keepsake book</button>
                <form method="post" action="/api/events/{ev['id']}/lock" style="display:inline">
                  <input type="hidden" name="locked" value="{'0' if ev['uploads_locked'] else '1'}">
                  <button class="mini" type="submit">{'🔓 Reopen uploads' if ev['uploads_locked'] else '🔒 Close album'}</button>
                </form>
                <button class="mini announce-btn" data-event="{ev['id']}" type="button">📣 Email the book ({sub_counts.get(ev['id'], 0)} signed up)</button>
                <form method="post" action="/api/events/{ev['id']}/delete" style="display:inline"
                      onsubmit="return confirm('Permanently delete this event and every photo, video, and book in it? This cannot be undone - nothing is retained on our servers.')">
                  <button class="mini" type="submit" style="cursor:pointer; background:none; color:#94433a; border-color:#e8cfcb">Delete forever</button>
                </form>
              </p>
              {unlock_html}
              <div class="recap" id="recap-{ev['id']}" hidden></div>{roster_panel}
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
            credits=str(balance),
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
                     custom_domain: str = Form(""), venue_id: str = Form(""),
                     event_type: str = Form("party")):
        if event_type not in ("party", "prom", "gala"):
            event_type = "party"
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
                    "INSERT INTO events (id, owner_id, slug, title, event_date, guest_password, custom_domain, created_at, venue_id, event_type)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (event_id, user["id"], slug, title, event_date.strip()[:40],
                     guest_password, custom_domain or None, int(time.time()),
                     venue_id or None, event_type),
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

    @app.post("/api/events/{event_id}/card-photo")
    async def card_photo(request: Request, event_id: str,
                         photo: UploadFile = File(...)):
        """Upload the photo/logo shown on the printable table cards."""
        event = owned_event(request, event_id)
        if event is None:
            return RedirectResponse("/login", status_code=303)
        data = await photo.read(MAX_IMAGE_BYTES + 1)
        if not data or len(data) > MAX_IMAGE_BYTES:
            return RedirectResponse("/dashboard?error=That+image+couldn't+be+used.", status_code=303)
        try:
            img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
            img.thumbnail((1200, 1200))
            img.convert("RGB").save(
                data_dir / "events" / event_id / "card.jpg", "JPEG", quality=90)
        except Exception:
            return RedirectResponse("/dashboard?error=That+image+couldn't+be+read.", status_code=303)
        return RedirectResponse("/dashboard", status_code=303)

    @app.get("/api/events/{event_id}/table-cards.pdf")
    def table_cards(request: Request, event_id: str):
        """Print-ready table cards: 4 per page with QR, title, photo/logo."""
        event = owned_event(request, event_id)
        if event is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        url = event_url(event)
        accent = "#7d8c6f"
        if event["venue_id"]:
            with db() as conn:
                venue = conn.execute(
                    "SELECT accent FROM venues WHERE id = ?", (event["venue_id"],)
                ).fetchone()
            if venue is not None and venue["accent"]:
                accent = venue["accent"]
        if event["event_type"] == "prom":
            notes = [url.replace("https://", ""),
                     "Sign in with your personal access code"]
        elif event["event_type"] == "gala":
            notes = [url.replace("https://", ""),
                     f"Password to watch: {event['guest_password']}",
                     "Table hosts: use your host code"]
        else:
            notes = [url.replace("https://", ""),
                     f"Password: {event['guest_password']}"]
        photo_path = data_dir / "events" / event_id / "card.jpg"
        pdf = card_maker.generate_cards(
            event["title"], url, accent, notes,
            photo_path=photo_path if photo_path.exists() else None,
            footer=f"powered by {base_domain}",
        )
        safe = re.sub(r"[^\w\- ]", "_", event["title"]) or "event"
        return Response(
            pdf, media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{safe} - table cards.pdf"'},
        )

    # ---- trade show kiosk (virtual booth agent) ----------------------------

    kiosk_key = os.environ.get("CR_KIOSK_KEY", "")

    def make_kiosk_token() -> str:
        exp = str(int(time.time()) + 60 * 60 * 24 * 3)  # a show weekend
        payload = f"k.{exp}"
        return f"{payload}.{sign(payload)}"

    def kiosk_ok(request: Request) -> bool:
        token = request.cookies.get("cr_kiosk", "")
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != "k":
            return False
        if not hmac.compare_digest(parts[2], sign(f"k.{parts[1]}")):
            return False
        return parts[1].isdigit() and int(parts[1]) > time.time()

    @app.get("/kiosk", response_class=HTMLResponse)
    def kiosk(request: Request, key: str = "", event: str = ""):
        if not kiosk_key:
            return JSONResponse({"error": "kiosk not enabled"}, status_code=404)
        authed = kiosk_ok(request)
        if not authed and not hmac.compare_digest(key, kiosk_key):
            return HTMLResponse(
                "<p style='font-family:sans-serif;padding:40px'>Kiosk locked — open "
                "/kiosk?key=&lt;your CR_KIOSK_KEY&gt; once on this device.</p>",
                status_code=403,
            )
        demo_url = f"https://{base_domain}/signup"
        if event:
            with db() as conn:
                ev = conn.execute("SELECT * FROM events WHERE slug = ?", (event,)).fetchone()
            if ev is not None:
                demo_url = event_url(ev)
        response = page(
            "kiosk",
            demo_url=esc(demo_url.replace("https://", "")),
            qr_src=esc(f"/kiosk-qr.png?event={event}" if event else "/kiosk-qr.png"),
            base=esc(base_domain),
        )
        if not authed:
            response.set_cookie("cr_kiosk", make_kiosk_token(), max_age=60 * 60 * 24 * 3,
                                httponly=True, samesite="lax")
        return response

    @app.get("/kiosk-qr.png")
    def kiosk_qr(request: Request, event: str = ""):
        if not kiosk_ok(request):
            return JSONResponse({"error": "kiosk locked"}, status_code=403)
        target = f"https://{base_domain}/signup"
        if event:
            with db() as conn:
                ev = conn.execute("SELECT * FROM events WHERE slug = ?", (event,)).fetchone()
            if ev is not None:
                target = event_url(ev)
        img = qrcode.make(target)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(buf.getvalue(), media_type="image/png")

    @app.post("/api/kiosk/chat")
    async def kiosk_chat(request: Request):
        if not kiosk_ok(request):
            return JSONResponse({"error": "kiosk locked"}, status_code=403)
        ip = request.client.host if request.client else "unknown"
        if too_many_attempts(f"kiosk:{ip}", limit=120):  # a busy booth all afternoon
            return JSONResponse({"error": "slow down a moment"}, status_code=429)
        record_attempt(f"kiosk:{ip}")
        if not ai_agents.ai_enabled():
            return JSONResponse(
                {"reply": "Our booth assistant is napping - but grab the QR code, "
                          "or leave your email with the humans at the booth!"})
        try:
            body = await request.json()
            raw = body.get("messages", [])
        except Exception:
            return JSONResponse({"error": "bad request"}, status_code=400)
        if not isinstance(raw, list):
            return JSONResponse({"error": "bad request"}, status_code=400)
        history = []
        for m in raw[-20:]:
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            content = str(m.get("content", ""))[:1000].strip()
            if role in ("user", "assistant") and content:
                history.append({"role": role, "content": content})
        if not history or history[-1]["role"] != "user":
            return JSONResponse({"error": "bad request"}, status_code=400)

        def save_lead(name: str, email: str, role: str, interest: str) -> None:
            with db() as conn:
                conn.execute(
                    "INSERT INTO leads (name, email, role, interest, created_at)"
                    " VALUES (?,?,?,?,?)",
                    (name, email, role, interest, int(time.time())),
                )

        reply = ai_agents.booth_reply(history, save_lead)
        if reply is None:
            reply = ("Great question - I'll let the team give you the full answer. "
                     "Want to leave your name and email so they can follow up?")
        return {"reply": reply}

    # ---- partner outreach engine (photographers / planners / venues) -------

    partner_rate = os.environ.get("CR_PARTNER_RATE", "50% of the first sale")

    @app.get("/outreach", response_class=HTMLResponse)
    def outreach(request: Request, key: str = ""):
        if not kiosk_key:
            return JSONResponse({"error": "operator console not enabled"}, status_code=404)
        authed = kiosk_ok(request)
        if not authed and not hmac.compare_digest(key, kiosk_key):
            return HTMLResponse(
                "<p style='font-family:sans-serif;padding:40px'>Locked — open "
                "/outreach?key=&lt;your CR_KIOSK_KEY&gt; once on this device.</p>",
                status_code=403,
            )
        with db() as conn:
            prospects = conn.execute(
                "SELECT * FROM prospects ORDER BY created_at DESC"
            ).fetchall()
        rows = []
        for p in prospects:
            pitch_block = ""
            if p["pitch_body"]:
                mailto = (
                    f"mailto:{esc(p['email'])}?subject={esc(p['pitch_subject'])}"
                )
                pitch_block = f"""
              <div class="pitch">
                <div class="psubj">{esc(p['pitch_subject'])}</div>
                <pre>{esc(p['pitch_body'])}</pre>
                <a class="mini" href="{mailto}">Open in email app</a>
                <button class="mini copy-btn" data-id="{p['id']}" type="button">Copy</button>
              </div>"""
            rows.append(f"""
            <div class="prospect" data-id="{p['id']}">
              <div class="phead">
                <strong>{esc(p['name'] or p['email'])}</strong>
                <span class="ptag">{esc(p['type'])}</span>
                <span class="pmeta">{esc(p['business'])}{' · ' + esc(p['city']) if p['city'] else ''} · {esc(p['email'])}</span>
                <select class="status-sel" data-id="{p['id']}">
                  {''.join(f'<option value="{s}"{" selected" if p["status"] == s else ""}>{s}</option>'
                           for s in ('new', 'pitched', 'replied', 'joined', 'pass'))}
                </select>
                <button class="mini pitch-btn" data-id="{p['id']}" type="button">✨ Write pitch</button>
              </div>{pitch_block}
            </div>""")
        response = page(
            "outreach",
            rows="\n".join(rows) or '<p class="empty">No prospects yet — add some above.</p>',
            count=str(len(prospects)),
            rate=esc(partner_rate),
        )
        if not authed:
            response.set_cookie("cr_kiosk", make_kiosk_token(), max_age=60 * 60 * 24 * 3,
                                httponly=True, samesite="lax")
        return response

    @app.post("/api/outreach/prospects")
    def add_prospects(request: Request, bulk: str = Form("")):
        if not kiosk_ok(request):
            return RedirectResponse("/outreach", status_code=303)
        added = 0
        with db() as conn:
            for line in bulk.splitlines()[:200]:
                parts = [f.strip() for f in line.split(",")]
                if len(parts) < 4 or "@" not in parts[3]:
                    continue
                name, business, ptype, email = parts[0], parts[1], parts[2].lower(), parts[3]
                city = parts[4] if len(parts) > 4 else ""
                notes = parts[5] if len(parts) > 5 else ""
                if ptype not in ("photographer", "planner", "venue"):
                    ptype = "planner"
                conn.execute(
                    "INSERT INTO prospects (name, business, type, email, city, notes, created_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (name[:80], business[:120], ptype, email[:120], city[:80],
                     notes[:300], int(time.time())),
                )
                added += 1
        return RedirectResponse("/outreach", status_code=303)

    @app.post("/api/outreach/prospects/{pid}/pitch")
    def pitch_prospect(request: Request, pid: int):
        if not kiosk_ok(request):
            return JSONResponse({"error": "locked"}, status_code=403)
        if not ai_agents.ai_enabled():
            return JSONResponse({"error": "AI isn't enabled on this server"}, status_code=503)
        with db() as conn:
            p = conn.execute("SELECT * FROM prospects WHERE id = ?", (pid,)).fetchone()
        if p is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        pitch = ai_agents.write_pitch(dict(p), partner_rate, f"https://{base_domain}/partners")
        if pitch is None:
            return JSONResponse({"error": "couldn't write the pitch"}, status_code=502)
        with db() as conn:
            conn.execute(
                "UPDATE prospects SET pitch_subject = ?, pitch_body = ?,"
                " status = CASE WHEN status = 'new' THEN 'pitched' ELSE status END"
                " WHERE id = ?",
                (pitch["subject"], pitch["body"], pid),
            )
        return pitch

    @app.post("/api/outreach/prospects/{pid}/status")
    def prospect_status(request: Request, pid: int, status: str = Form(...)):
        if not kiosk_ok(request):
            return JSONResponse({"error": "locked"}, status_code=403)
        if status not in ("new", "pitched", "replied", "joined", "pass"):
            return JSONResponse({"error": "bad status"}, status_code=400)
        with db() as conn:
            conn.execute("UPDATE prospects SET status = ? WHERE id = ?", (status, pid))
        return {"ok": True}

    @app.get("/outreach/prospects.csv")
    def prospects_csv(key: str = ""):
        if not kiosk_key or not hmac.compare_digest(key, kiosk_key):
            return JSONResponse({"error": "locked"}, status_code=403)
        with db() as conn:
            rows = conn.execute("SELECT * FROM prospects ORDER BY created_at DESC").fetchall()
        lines = ["name,business,type,email,city,status,pitch_subject,pitch_body"]
        for r in rows:
            fields = [str(r[k] or "").replace('"', "'").replace("\n", " / ")
                      for k in ("name", "business", "type", "email", "city",
                                "status", "pitch_subject", "pitch_body")]
            lines.append(",".join(f'"{f}"' for f in fields))
        return Response("\n".join(lines), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="prospects.csv"'})

    @app.get("/kiosk/leads.csv")
    def kiosk_leads(key: str = ""):
        if not kiosk_key or not hmac.compare_digest(key, kiosk_key):
            return JSONResponse({"error": "kiosk locked"}, status_code=403)
        with db() as conn:
            rows = conn.execute("SELECT * FROM leads ORDER BY created_at DESC").fetchall()
        lines = ["name,email,role,interest,source,created_at"]
        for r in rows:
            fields = [str(r[k] or "").replace('"', "'") for k in
                      ("name", "email", "role", "interest", "source", "created_at")]
            lines.append(",".join(f'"{f}"' for f in fields))
        return Response("\n".join(lines), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="leads.csv"'})

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

    # ---- billing: packages, checkout, webhook ------------------------------

    @app.get("/api/packages")
    def packages():
        return {
            "packages": {
                k: {"name": p["name"], "price": billing.price_label(k),
                    "tagline": p["tagline"], "features": p["features"],
                    "kind": p["kind"]}
                for k, p in billing.PACKAGES.items()
            },
            "venue_tiers": {
                k: {"name": t["name"], "monthly": f"${t['monthly_cents'] // 100}",
                    "events_per_month": t["events_per_month"], "blurb": t["blurb"]}
                for k, t in billing.VENUE_TIERS.items()
            },
            "stripe": billing.stripe_enabled(),
        }

    @app.post("/api/checkout/{package_key}")
    def checkout(request: Request, package_key: str, event_id: str = ""):
        user = current_user(request)
        if user is None:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        package = billing.PACKAGES.get(package_key)
        if package is None:
            return JSONResponse({"error": "unknown package"}, status_code=404)
        if package["price_cents"] == 0:
            return {"beta": True, "message": "Starter is free - just create your event!"}
        if not billing.stripe_enabled():
            return {"beta": True,
                    "message": "Everything is free during the beta - paid packages "
                               "launch soon, and beta users keep their events."}
        try:
            url = billing.create_checkout(
                package_key, user["id"], f"https://{base_domain}", event_id[:64]
            )
        except Exception:
            return JSONResponse({"error": "checkout failed - try again shortly"},
                                status_code=502)
        return {"url": url}

    @app.post("/api/redeem")
    def redeem_code(request: Request, code: str = Form(...)):
        """Redeem a promo code (e.g. a family & friends code) for a free
        package — one redemption per account."""
        user = current_user(request)
        if user is None:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        package_key = billing.promo_package(code)
        if package_key is None:
            return JSONResponse({"error": "That code isn't valid."}, status_code=404)
        package = billing.PACKAGES[package_key]
        marker = f"promo:{code.strip().lower()}"
        with db() as conn:
            used = conn.execute(
                "SELECT 1 FROM purchases WHERE user_id = ? AND stripe_session = ?",
                (user["id"], marker),
            ).fetchone()
            if used is not None:
                return JSONResponse(
                    {"error": "You've already used that code."}, status_code=400
                )
            conn.execute(
                "INSERT INTO purchases (user_id, package, amount_cents, stripe_session, event_id, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (user["id"], package_key, 0, marker, "", int(time.time())),
            )
            if package["credits"]:
                conn.execute(
                    "INSERT INTO credits (user_id, delta, reason, created_at) VALUES (?,?,?,?)",
                    (user["id"], package["credits"], marker, int(time.time())),
                )
        return {"granted": package["name"], "credits": package["credits"]}

    @app.post("/stripe/webhook")
    async def stripe_webhook(request: Request):
        if not billing.stripe_enabled():
            return JSONResponse({"error": "billing disabled"}, status_code=503)
        payload = await request.body()
        try:
            event = billing.parse_webhook(
                payload, request.headers.get("stripe-signature", "")
            )
        except Exception:
            return JSONResponse({"error": "bad signature"}, status_code=400)
        if event["type"] == "checkout.session.completed":
            session = event["data"]["object"]
            meta = session.get("metadata") or {}
            package_key = meta.get("package", "")
            package = billing.PACKAGES.get(package_key)
            is_book = package_key.startswith("book_")
            try:
                user_id = int(meta.get("user_id", "0"))
            except ValueError:
                user_id = 0
            if (package or is_book) and user_id:
                with db() as conn:
                    already = conn.execute(
                        "SELECT 1 FROM purchases WHERE stripe_session = ?",
                        (session.get("id", ""),),
                    ).fetchone()
                    if not already:  # webhooks can be delivered twice
                        conn.execute(
                            "INSERT INTO purchases (user_id, package, amount_cents,"
                            " stripe_session, event_id, created_at) VALUES (?,?,?,?,?,?)",
                            (user_id, package_key,
                             session.get("amount_total")
                             or (package["price_cents"] if package else 0),
                             session.get("id", ""), meta.get("event_id", ""),
                             int(time.time())),
                        )
                        target_event = meta.get("event_id", "")
                        grant = package["credits"] if package else 0
                        if target_event and grant:
                            # buying for a specific event: unlock it directly,
                            # one credit is consumed by that unlock
                            conn.execute(
                                "UPDATE events SET paid = 1 WHERE id = ? AND owner_id = ?",
                                (target_event, user_id),
                            )
                            grant -= 1
                        if grant:
                            conn.execute(
                                "INSERT INTO credits (user_id, delta, reason, created_at)"
                                " VALUES (?,?,?,?)",
                                (user_id, grant,
                                 f"purchase:{package_key}", int(time.time())),
                            )
        return {"received": True}

    def credit_balance(conn: sqlite3.Connection, user_id: int) -> int:
        row = conn.execute(
            "SELECT COALESCE(SUM(delta), 0) AS n FROM credits WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return row["n"]

    @app.get("/partners", response_class=HTMLResponse)
    def partners(request: Request):
        if resolve_event(request) is not None:
            return RedirectResponse("/", status_code=303)
        return page("partners", base=esc(base_domain))

    @app.get("/samples/{slug}", response_class=HTMLResponse)
    def sample_flipbook(request: Request, slug: str):
        """Flip through a sample keepsake book like a real book."""
        if resolve_event(request) is not None:
            return RedirectResponse("/", status_code=303)
        if not re.fullmatch(r"[a-z0-9-]{1,60}", slug):
            return JSONResponse({"error": "not found"}, status_code=404)
        manifest_path = BASE_DIR / "static" / "samples" / f"{slug}.json"
        if not manifest_path.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        data = json.loads(manifest_path.read_text())
        return page(
            "flipbook",
            title=esc(data["title"]),
            accent=esc(data.get("accent", "#7d8c6f")),
            pdf=esc(data.get("pdf", "")),
            payload=json.dumps(data),
        )

    @app.get("/celebrations", response_class=HTMLResponse)
    def celebrations(request: Request):
        """Family reunions & birthdays vertical."""
        if resolve_event(request) is not None:
            return RedirectResponse("/", status_code=303)
        return page("celebrations", p_celebration=billing.price_label("celebration"))

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

    def remember_subscriber(event_id: str, email: str, name: str = "") -> None:
        """A guest left their email at sign-in — remember them for the
        book-ready announcement. Best-effort, never blocks login."""
        email = email.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            return
        try:
            with db() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO subscribers (event_id, email, name, created_at)"
                    " VALUES (?,?,?,?)",
                    (event_id, email, name.strip()[:60], int(time.time())),
                )
        except sqlite3.Error:
            pass

    def tenant_login(request: Request, event: sqlite3.Row, password: str, email: str = ""):
        ip = request.client.host if request.client else "unknown"
        key = f"guest:{event['id']}:{ip}"
        if too_many_attempts(key):
            return guest_login_page(event, "Too many attempts - please wait a few minutes.")
        if is_tagged_event(event):
            # Students sign in with their personal code; the event password
            # is the staff upload code (e.g. the Vice Principal's) and grants
            # upload-and-tag rights only.
            code = password.strip().lower()
            with db() as conn:
                member = conn.execute(
                    "SELECT * FROM members WHERE event_id = ? AND code = ?",
                    (event["id"], code),
                ).fetchone()
            if member is None and hmac.compare_digest(password, event["guest_password"]):
                if email:
                    remember_subscriber(event["id"], email)
                response = RedirectResponse("/", status_code=303)
                response.set_cookie(
                    GUEST_COOKIE, make_staff_token(event["id"]),
                    max_age=SESSION_TTL_SECONDS, httponly=True, samesite="lax",
                )
                return response
            if member is None:
                record_attempt(key)
                return guest_login_page(event, "That code isn't right - check the card you were given.")
            if email:
                remember_subscriber(event["id"], email, member["name"])
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(
                GUEST_COOKIE, make_member_token(event["id"], member["id"]),
                max_age=SESSION_TTL_SECONDS, httponly=True, samesite="lax",
            )
            return response
        if is_gala(event):
            code = password.strip().lower()
            with db() as conn:
                member = conn.execute(
                    "SELECT * FROM members WHERE event_id = ? AND code = ?",
                    (event["id"], code),
                ).fetchone()
            if member is not None:
                if email:
                    remember_subscriber(event["id"], email, member["name"])
                response = RedirectResponse("/", status_code=303)
                response.set_cookie(
                    GUEST_COOKIE, make_member_token(event["id"], member["id"]),
                    max_age=SESSION_TTL_SECONDS, httponly=True, samesite="lax",
                )
                return response
        if not hmac.compare_digest(password, event["guest_password"]):
            record_attempt(key)
            return guest_login_page(event, "That password isn't right.")
        if email:
            remember_subscriber(event["id"], email)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            GUEST_COOKIE, make_guest_token(event["id"]),
            max_age=SESSION_TTL_SECONDS, httponly=True, samesite="lax",
        )
        return response

    @app.post("/api/events/{event_id}/book")
    def make_book(request: Request, event_id: str):
        """Compose the keepsake book PDF for an event (owner only)."""
        user = current_user(request)
        if user is None:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        with db() as conn:
            event = conn.execute(
                "SELECT * FROM events WHERE id = ? AND owner_id = ?",
                (event_id, user["id"]),
            ).fetchone()
        if event is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        dirs = event_dirs(data_dir, event["id"])
        photos = []
        for meta_file in dirs["meta"].glob("*.json"):
            try:
                photos.append(json.loads(meta_file.read_text()))
            except (OSError, json.JSONDecodeError):
                continue
        # If guests starred photos for the book (after the album closed),
        # the book is built from exactly that set.
        with db() as conn:
            picked_ids = {
                row["photo_id"] for row in conn.execute(
                    "SELECT DISTINCT photo_id FROM book_picks WHERE event_id = ?",
                    (event["id"],),
                )
            }
        if picked_ids:
            chosen = [p for p in photos if p["id"] in picked_ids]
            if any(p.get("type") != "video" for p in chosen):
                photos = chosen
        if not any(p.get("type") != "video" for p in photos):
            return JSONResponse({"error": "no photos in the album yet"}, status_code=400)

        recap_file = data_dir / "events" / event["id"] / "recap.txt"
        recap = recap_file.read_text() if recap_file.exists() else None
        if recap is None and ai_agents.ai_enabled():
            recap = ai_agents.generate_recap(event["title"], photos)
            if recap:
                recap_file.write_text(recap)

        venue_name, accent = "", "#7d8c6f"
        if event["venue_id"]:
            with db() as conn:
                venue = conn.execute(
                    "SELECT * FROM venues WHERE id = ?", (event["venue_id"],)
                ).fetchone()
            if venue is not None:
                venue_name, accent = venue["name"], venue["accent"] or accent

        dest = data_dir / "events" / event["id"] / "book.pdf"
        pages = book_maker.generate_book(
            dest, event["title"], event["event_date"], photos, dirs["photos"],
            recap=recap, venue_name=venue_name, accent=accent,
            credit=f"Made with love on {base_domain}",
        )
        return {"pages": pages, "url": f"{event_url(event)}/book.pdf"}

    @app.get("/book.pdf")
    def book_pdf(request: Request):
        event = resolve_event(request)
        if event is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        role = gallery_role(request, event)
        if role is None:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        if is_tagged_event(event) and role != "admin":
            # The all-photos event book stays with the organizer; students get
            # their own book at /my-book.pdf.
            return JSONResponse({"error": "not found"}, status_code=404)
        path = data_dir / "events" / event["id"] / "book.pdf"
        if not path.exists():
            return JSONResponse({"error": "no book yet"}, status_code=404)
        safe = re.sub(r"[^\w\- ]", "_", event["title"]) or "keepsake"
        return FileResponse(
            path, media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{safe} - keepsake book.pdf"'},
        )

    # ---- tagged events (proms & grad nights) -------------------------------

    def owned_event(request: Request, event_id: str) -> sqlite3.Row | None:
        user = current_user(request)
        if user is None:
            return None
        with db() as conn:
            return conn.execute(
                "SELECT * FROM events WHERE id = ? AND owner_id = ?",
                (event_id, user["id"]),
            ).fetchone()

    # ---- close the album, announce the book, sell the book -----------------

    @app.post("/api/events/{event_id}/apply-credit")
    def apply_credit(request: Request, event_id: str):
        """Spend one event credit to unlock an event forever."""
        event = owned_event(request, event_id)
        if event is None:
            return RedirectResponse("/login", status_code=303)
        if event["paid"]:
            return RedirectResponse("/dashboard", status_code=303)
        user = current_user(request)
        with db() as conn:
            if credit_balance(conn, user["id"]) < 1:
                return RedirectResponse(
                    "/dashboard?error=No+event+credits+yet+-+buy+a+package+or+redeem+a+code.",
                    status_code=303,
                )
            conn.execute(
                "INSERT INTO credits (user_id, delta, reason, created_at) VALUES (?,?,?,?)",
                (user["id"], -1, f"apply:{event_id}", int(time.time())),
            )
            conn.execute("UPDATE events SET paid = 1 WHERE id = ?", (event_id,))
        return RedirectResponse("/dashboard", status_code=303)

    @app.post("/api/events/{event_id}/settings")
    def event_settings(request: Request, event_id: str,
                       guest_upload_limit: str = Form("0")):
        """Host settings: how many photos each guest may share (0 = unlimited)."""
        event = owned_event(request, event_id)
        if event is None:
            return RedirectResponse("/login", status_code=303)
        try:
            limit = max(0, min(500, int(guest_upload_limit)))
        except ValueError:
            limit = 0
        with db() as conn:
            conn.execute(
                "UPDATE events SET guest_upload_limit = ? WHERE id = ?",
                (limit, event_id),
            )
        return RedirectResponse("/dashboard", status_code=303)

    @app.post("/api/events/{event_id}/lock")
    def lock_event(request: Request, event_id: str, locked: str = Form("1")):
        """Host closes (or reopens) the album: no more uploads, the book is
        being finished. Admin uploads still work for last-minute fixes."""
        event = owned_event(request, event_id)
        if event is None:
            return RedirectResponse("/login", status_code=303)
        with db() as conn:
            conn.execute(
                "UPDATE events SET uploads_locked = ? WHERE id = ?",
                (1 if locked == "1" else 0, event_id),
            )
        return RedirectResponse("/dashboard", status_code=303)

    def book_announcement(event: sqlite3.Row, host_name: str, name: str) -> tuple[str, str]:
        greeting = name.split(" ")[0] if name else "there"
        price = "from $39"
        subject = f"The keepsake book from {event['title']} is ready 📖"
        body = (
            f"Hi {greeting},\n\n"
            f"The keepsake book from {event['title']} is ready!\n\n"
            f"See the finished album and download the book (the PDF is free):\n"
            f"{event_url(event)}\n\n"
            f"Want it on your coffee table? Order the printed 8×8\" book "
            f"({price}, softcover or hardcover, shipped) right from the album - look for the "
            f"\U0001f6d2 Order printed book button. Pick as many copies as you "
            f"like at checkout - they all ship together to one address. "
            f"Sending books somewhere else too? Just place another order.\n\n"
            f"With love,\n{host_name or 'Your host'} - via ConfettiRoll"
        )
        return subject, body

    @app.post("/api/events/{event_id}/announce-book")
    def announce_book(request: Request, event_id: str):
        """Email everyone who signed in with an email: the book is ready and
        can be ordered. Without a mail provider, reports who's waiting."""
        user = current_user(request)
        if user is None:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        event = owned_event(request, event_id)
        if event is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if not (data_dir / "events" / event_id / "book.pdf").exists():
            return JSONResponse(
                {"error": "make the keepsake book first"}, status_code=400
            )
        with db() as conn:
            subs = conn.execute(
                "SELECT * FROM subscribers WHERE event_id = ? AND notified_at IS NULL",
                (event_id,),
            ).fetchall()
        host_name = user["name"] or ""
        sample_subject, sample_body = book_announcement(event, host_name, "")
        if not mailer.enabled():
            return {
                "sent": 0,
                "pending": len(subs),
                "email_configured": False,
                "subject": sample_subject,
                "body": sample_body,
            }
        sent = 0
        for sub in subs:
            subject, body = book_announcement(event, host_name, sub["name"])
            if mailer.send(sub["email"], subject, body):
                sent += 1
                with db() as conn:
                    conn.execute(
                        "UPDATE subscribers SET notified_at = ? WHERE id = ?",
                        (int(time.time()), sub["id"]),
                    )
        return {"sent": sent, "pending": len(subs) - sent, "email_configured": True}

    @app.post("/api/photos/{photo_id}/book-pick")
    def book_pick(request: Request, photo_id: str, voter: str = Form(...)):
        """After the host closes the album, anyone in it can star photos to
        vote them into the keepsake book (per-browser voter id)."""
        event = resolve_event(request)
        if event is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        role = gallery_role(request, event)
        if role is None:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        if role == "member":
            return JSONResponse({"error": "use your personal picks"}, status_code=400)
        if not event["uploads_locked"] and role != "admin":
            return JSONResponse(
                {"error": "the host hasn't closed the album yet"}, status_code=400
            )
        voter = voter.strip()[:40]
        if len(voter) < 6:
            return JSONResponse({"error": "bad voter id"}, status_code=400)
        dirs = event_dirs(data_dir, event["id"])
        if _read_meta(dirs, photo_id) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        with db() as conn:
            existing = conn.execute(
                "SELECT id FROM book_picks WHERE event_id = ? AND photo_id = ? AND voter = ?",
                (event["id"], photo_id, voter),
            ).fetchone()
            if existing is not None:
                conn.execute("DELETE FROM book_picks WHERE id = ?", (existing["id"],))
                picked = False
            else:
                conn.execute(
                    "INSERT INTO book_picks (event_id, photo_id, voter, created_at) VALUES (?,?,?,?)",
                    (event["id"], photo_id, voter, int(time.time())),
                )
                picked = True
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM book_picks WHERE event_id = ? AND photo_id = ?",
                (event["id"], photo_id),
            ).fetchone()["n"]
        return {"picked": picked, "count": count}

    @app.post("/api/photos/{photo_id}/edit")
    def edit_photo(request: Request, photo_id: str, op: str = Form(...)):
        """Host photo editing: rotate or one-click enhance (photos only)."""
        event = resolve_event(request)
        if event is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if gallery_role(request, event) != "admin":
            return JSONResponse({"error": "admin only"}, status_code=403)
        dirs = event_dirs(data_dir, event["id"])
        path = _find_media_file(dirs["photos"], photo_id)
        meta = _read_meta(dirs, photo_id)
        if path is None or meta is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if meta.get("type") == "video":
            return JSONResponse(
                {"error": "videos can't be edited yet - photos only"}, status_code=400
            )
        try:
            img = ImageOps.exif_transpose(Image.open(path))
            if op == "rotate_left":
                img = img.rotate(90, expand=True)
            elif op == "rotate_right":
                img = img.rotate(-90, expand=True)
            elif op == "enhance":
                img = ImageEnhance.Brightness(img.convert("RGB")).enhance(1.04)
                img = ImageEnhance.Contrast(img).enhance(1.10)
                img = ImageEnhance.Color(img).enhance(1.12)
                img = ImageEnhance.Sharpness(img).enhance(1.15)
            else:
                return JSONResponse({"error": "unknown edit"}, status_code=400)
            if path.suffix.lower() == ".png":
                img.save(path, "PNG")
            else:
                img.convert("RGB").save(path, "JPEG", quality=92)
            thumb = img.convert("RGB")
            thumb.thumbnail((THUMB_MAX_DIM, THUMB_MAX_DIM))
            thumb.save(dirs["thumbs"] / f"{photo_id}.jpg", "JPEG", quality=80)
        except Exception:
            return JSONResponse({"error": "couldn't edit that photo"}, status_code=500)
        meta.update(width=img.width, height=img.height, edited_at=int(time.time()))
        (dirs["meta"] / f"{photo_id}.json").write_text(json.dumps(meta))
        return meta

    @app.post("/api/photos/{photo_id}/meta")
    def edit_photo_meta(request: Request, photo_id: str,
                        caption: str = Form(None), uploader: str = Form(None)):
        """Host edits a photo/video's caption or credit."""
        event = resolve_event(request)
        if event is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if gallery_role(request, event) != "admin":
            return JSONResponse({"error": "admin only"}, status_code=403)
        dirs = event_dirs(data_dir, event["id"])
        meta = _read_meta(dirs, photo_id)
        if meta is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if caption is not None:
            meta["caption"] = re.sub(r"\s+", " ", caption).strip()[:200]
        if uploader is not None:
            meta["uploader"] = re.sub(r"\s+", " ", uploader).strip()[:60]
        meta["edited_at"] = int(time.time())
        (dirs["meta"] / f"{photo_id}.json").write_text(json.dumps(meta))
        return meta

    def _book_page_count(event: sqlite3.Row) -> int:
        """How many photo pages the event's book has (mirrors make_book)."""
        dirs = event_dirs(data_dir, event["id"])
        photos = []
        for meta_file in dirs["meta"].glob("*.json"):
            meta = _read_meta(dirs, meta_file.stem)
            if meta is not None and meta.get("type") != "video":
                photos.append(meta)
        with db() as conn:
            picked = {
                row["photo_id"] for row in conn.execute(
                    "SELECT DISTINCT photo_id FROM book_picks WHERE event_id = ?",
                    (event["id"],),
                )
            }
        if picked:
            chosen = [p for p in photos if p["id"] in picked]
            if chosen:
                photos = chosen
        return min(len(photos), book_maker.MAX_PHOTOS)

    def _heirloom_discount(event: sqlite3.Row) -> bool:
        """Heirloom buyers get 50% off the FIRST printed book for the event."""
        with db() as conn:
            has_heirloom = conn.execute(
                "SELECT 1 FROM purchases WHERE user_id = ? AND package = 'heirloom'",
                (event["owner_id"],),
            ).fetchone()
            if has_heirloom is None:
                return False
            prior_book = conn.execute(
                "SELECT 1 FROM purchases WHERE event_id = ? AND package LIKE 'book_%'",
                (event["id"],),
            ).fetchone()
        return prior_book is None

    @app.get("/api/book-quote")
    def book_quote(request: Request):
        """Price the printed book for this album: cover options, page tier,
        and any Heirloom first-book discount."""
        event = resolve_event(request)
        if event is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if gallery_role(request, event) is None:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        pages = _book_page_count(event)
        discount = _heirloom_discount(event)
        covers = {}
        for key, spec in billing.BOOK_PRICING.items():
            cents = billing.book_price_cents(key, pages)
            covers[key] = {
                "name": spec["name"],
                "blurb": spec["blurb"],
                "tier": billing.book_tier_label(key, pages),
                "price_cents": cents,
                "final_cents": cents // 2 if discount else cents,
            }
        return {"pages": pages, "discount": discount, "covers": covers}

    @app.post("/api/book-order")
    def book_order(request: Request, cover: str = Form(...)):
        """Anyone in the album (guest, student, host) orders the printed book."""
        event = resolve_event(request)
        if event is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        role = gallery_role(request, event)
        if role is None:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        if cover not in billing.BOOK_PRICING:
            return JSONResponse({"error": "pick softcover or hardcover"}, status_code=400)
        if not billing.stripe_enabled():
            return {
                "beta": True,
                "message": "Printed book ordering opens soon - the PDF is free to download today.",
            }
        pages = _book_page_count(event)
        price = billing.book_price_cents(cover, pages)
        if _heirloom_discount(event):
            price //= 2
        try:
            url = billing.create_book_checkout(
                cover, pages, price, event["owner_id"],
                f"https://{base_domain}", event["id"],
                success_url=f"{event_url(event)}/?ordered=1",
                cancel_url=f"{event_url(event)}/",
            )
        except Exception:
            return JSONResponse({"error": "couldn't start checkout"}, status_code=502)
        return {"url": url}

    @app.post("/api/events/{event_id}/delete")
    def delete_event(request: Request, event_id: str):
        """Permanently delete an event and every photo, video, thumbnail,
        book, and trash file it holds. This is the 'your photos are never
        ours' promise made real — nothing is retained."""
        event = owned_event(request, event_id)
        if event is None:
            return RedirectResponse("/login", status_code=303)
        with db() as conn:
            conn.execute("DELETE FROM selections WHERE event_id = ?", (event_id,))
            conn.execute("DELETE FROM members WHERE event_id = ?", (event_id,))
            conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        shutil.rmtree(data_dir / "events" / event_id, ignore_errors=True)
        return RedirectResponse("/dashboard", status_code=303)

    @app.post("/api/events/{event_id}/members")
    def add_members(request: Request, event_id: str, names: str = Form("")):
        event = owned_event(request, event_id)
        if event is None:
            return RedirectResponse("/login", status_code=303)
        cleaned = [re.sub(r"\s+", " ", n).strip()[:60] for n in names.splitlines()]
        cleaned = [n for n in cleaned if n][:500]
        with db() as conn:
            for name in cleaned:
                for _ in range(20):  # retry on the rare per-event code collision
                    try:
                        conn.execute(
                            "INSERT INTO members (event_id, name, code, created_at)"
                            " VALUES (?,?,?,?)",
                            (event_id, name, new_member_code(), int(time.time())),
                        )
                        break
                    except sqlite3.IntegrityError:
                        continue
        return RedirectResponse("/dashboard", status_code=303)

    @app.post("/api/events/{event_id}/members/{member_id}/delete")
    def delete_member(request: Request, event_id: str, member_id: int):
        event = owned_event(request, event_id)
        if event is None:
            return RedirectResponse("/login", status_code=303)
        with db() as conn:
            conn.execute("DELETE FROM selections WHERE member_id = ?", (member_id,))
            conn.execute(
                "DELETE FROM members WHERE id = ? AND event_id = ?",
                (member_id, event_id),
            )
        return RedirectResponse("/dashboard", status_code=303)

    @app.get("/api/events/{event_id}/members.csv")
    def members_csv(request: Request, event_id: str):
        event = owned_event(request, event_id)
        if event is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        with db() as conn:
            rows = conn.execute(
                "SELECT name, code FROM members WHERE event_id = ? ORDER BY name",
                (event_id,),
            ).fetchall()
        url = event_url(event)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["name", "access code", "gallery"])
        for row in rows:
            writer.writerow([row["name"], row["code"], url])
        return Response(
            buf.getvalue(), media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{event["slug"]}-codes.csv"'},
        )

    @app.post("/api/photos/{photo_id}/tags")
    def tag_photo(request: Request, photo_id: str, members: str = Form("")):
        event = resolve_event(request)
        if event is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if gallery_role(request, event) not in ("admin", "staff") or not is_tagged_event(event):
            return JSONResponse({"error": "staff only"}, status_code=403)
        wanted = {int(m) for m in members.split(",") if m.strip().isdigit()}
        with db() as conn:
            valid = {
                row["id"] for row in conn.execute(
                    "SELECT id FROM members WHERE event_id = ?", (event["id"],)
                )
            }
        dirs = event_dirs(data_dir, event["id"])
        meta = _read_meta(dirs, photo_id)
        if meta is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        meta["tagged"] = sorted(wanted & valid)
        (dirs["meta"] / f"{photo_id}.json").write_text(json.dumps(meta))
        return {"tagged": meta["tagged"]}

    @app.post("/api/photos/{photo_id}/pick")
    def pick_photo(request: Request, photo_id: str):
        event = resolve_event(request)
        if event is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if not is_tagged_event(event):
            return JSONResponse({"error": "not found"}, status_code=404)
        if gallery_role(request, event) != "member":
            return JSONResponse({"error": "not logged in"}, status_code=401)
        member = current_member(request, event)
        dirs = event_dirs(data_dir, event["id"])
        if member is None or not member_can_view(dirs, photo_id, member["id"]):
            return JSONResponse({"error": "not found"}, status_code=404)
        with db() as conn:
            existing = conn.execute(
                "SELECT id FROM selections WHERE member_id = ? AND photo_id = ?",
                (member["id"], photo_id),
            ).fetchone()
            if existing is not None:
                conn.execute("DELETE FROM selections WHERE id = ?", (existing["id"],))
                return {"picked": False}
            conn.execute(
                "INSERT INTO selections (event_id, member_id, photo_id, created_at)"
                " VALUES (?,?,?,?)",
                (event["id"], member["id"], photo_id, int(time.time())),
            )
        return {"picked": True}

    @app.post("/api/my-book")
    def make_my_book(request: Request):
        """A student's personal keepsake book from the photos they picked."""
        event = resolve_event(request)
        if event is None or not is_tagged_event(event):
            return JSONResponse({"error": "not found"}, status_code=404)
        if gallery_role(request, event) != "member":
            return JSONResponse({"error": "not logged in"}, status_code=401)
        member = current_member(request, event)
        if member is None:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        dirs = event_dirs(data_dir, event["id"])
        with db() as conn:
            picked = {
                row["photo_id"] for row in conn.execute(
                    "SELECT photo_id FROM selections WHERE member_id = ?",
                    (member["id"],),
                )
            }
        photos = []
        for meta_file in dirs["meta"].glob("*.json"):
            meta = _read_meta(dirs, meta_file.stem)
            if meta is None or member["id"] not in meta.get("tagged", []):
                continue
            if picked and meta["id"] not in picked:
                continue
            photos.append(meta)
        if not any(p.get("type") != "video" for p in photos):
            return JSONResponse({"error": "no photos picked yet"}, status_code=400)
        books_dir = data_dir / "events" / event["id"] / "books"
        books_dir.mkdir(parents=True, exist_ok=True)
        dest = books_dir / f"member-{member['id']}.pdf"
        pages = book_maker.generate_book(
            dest, f"{member['name']} · {event['title']}", event["event_date"],
            photos, dirs["photos"],
            credit=f"Made with love on {base_domain}",
        )
        return {"pages": pages, "url": "/my-book.pdf"}

    @app.get("/my-book.pdf")
    def my_book_pdf(request: Request):
        event = resolve_event(request)
        if event is None or not is_tagged_event(event):
            return JSONResponse({"error": "not found"}, status_code=404)
        if gallery_role(request, event) != "member":
            return JSONResponse({"error": "not logged in"}, status_code=401)
        member = current_member(request, event)
        if member is None:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        path = data_dir / "events" / event["id"] / "books" / f"member-{member['id']}.pdf"
        if not path.exists():
            return JSONResponse({"error": "no book yet"}, status_code=404)
        safe = re.sub(r"[^\w\- ]", "_", member["name"]) or "my"
        return FileResponse(
            path, media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{safe} - keepsake book.pdf"'},
        )

    @app.get("/stream", response_class=HTMLResponse)
    def stream(request: Request):
        """Live venue slideshow: photos crossfade on a big screen, new uploads
        jump the queue, a QR code invites guests to join."""
        event = resolve_event(request)
        if event is None:
            return RedirectResponse("/", status_code=303)
        role = gallery_role(request, event)
        if role is None:
            return RedirectResponse("/login", status_code=303)
        if is_tagged_event(event) and role != "admin":
            # On tagged events only the organizer's device runs the big screen.
            return RedirectResponse("/", status_code=303)
        brand = event_brand(event)
        brand_line = ""
        if event["venue_id"]:
            with db() as conn:
                venue = conn.execute(
                    "SELECT * FROM venues WHERE id = ?", (event["venue_id"],)
                ).fetchone()
            if venue is not None:
                logo = (
                    f'<img src="/venue-assets/{venue["id"]}/{esc(venue["logo"])}" alt="">'
                    if venue["logo"] else ""
                )
                brand_line = f'{logo}Hosted at {esc(venue["name"])}'
        return page(
            "stream",
            title=esc(event["title"]),
            brand_css=brand["brand_css"],
            brand_line=brand_line,
            join_url=esc(event_url(event).replace("https://", "")),
        )

    @app.get("/stream-qr.png")
    def stream_qr(request: Request):
        event = resolve_event(request)
        if event is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if gallery_role(request, event) is None:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        img = qrcode.make(event_url(event))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(buf.getvalue(), media_type="image/png")

    def _read_meta(dirs: dict[str, Path], photo_id: str) -> dict | None:
        meta_file = dirs["meta"] / f"{photo_id}.json"
        if not meta_file.exists():
            return None
        try:
            return json.loads(meta_file.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def member_can_view(dirs: dict[str, Path], photo_id: str, member_id: int) -> bool:
        # Safety rule on tagged events: a student can only reach photos they
        # are tagged in — enforced on the media routes, not just the listing.
        meta = _read_meta(dirs, photo_id)
        return meta is not None and member_id in meta.get("tagged", [])

    @app.get("/api/photos")
    def list_photos(request: Request, q: str = "", highlights: bool = False,
                    voter: str = ""):
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
        member = current_member(request, event) if role == "member" else None
        if member is not None and is_tagged_event(event):
            photos = [p for p in photos if member["id"] in p.get("tagged", [])]
        my_upload_count = sum(1 for p in photos if voter and p.get("device") == voter)
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
        out = {
            "photos": photos,
            "is_admin": role == "admin",
            "is_staff": role == "staff",
            "ai_enabled": ai_agents.ai_enabled(),
            "book": (data_dir / "events" / event["id"] / "book.pdf").exists(),
            "mode": event["event_type"],
            "locked": bool(event["uploads_locked"]),
            "trial": trial_state(event),
            "upload_limit": event["guest_upload_limit"],
        }
        if event["guest_upload_limit"] and voter and role not in ("admin", "staff"):
            out["my_upload_count"] = my_upload_count
        with db() as conn:
            pick_rows = conn.execute(
                "SELECT photo_id, voter FROM book_picks WHERE event_id = ?",
                (event["id"],),
            ).fetchall()
        counts: dict[str, int] = {}
        mine = []
        for row in pick_rows:
            counts[row["photo_id"]] = counts.get(row["photo_id"], 0) + 1
            if voter and row["voter"] == voter:
                mine.append(row["photo_id"])
        out["book_picks"] = counts
        out["my_book_picks"] = mine
        if member is not None:
            out["member_name"] = member["name"]
            if is_tagged_event(event):
                with db() as conn:
                    picked = conn.execute(
                        "SELECT photo_id FROM selections WHERE member_id = ?",
                        (member["id"],),
                    ).fetchall()
                out["picked"] = [row["photo_id"] for row in picked]
        if role in ("admin", "staff") and is_tagged_event(event):
            with db() as conn:
                rows = conn.execute(
                    "SELECT id, name FROM members WHERE event_id = ? ORDER BY name",
                    (event["id"],),
                ).fetchall()
            out["members"] = [{"id": r["id"], "name": r["name"]} for r in rows]
        return out

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
                     files: list[UploadFile] = File(...), uploader: str = Form(""),
                     device: str = Form("")):
        event = resolve_event(request)
        if event is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        role = gallery_role(request, event)
        if role is None:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        if is_tagged_event(event) and role not in ("admin", "staff"):
            # On tagged events only the designated staff uploader (e.g. the
            # Vice Principal) adds photos — students view, pick, and print.
            return JSONResponse(
                {"error": "photos are added by event staff only"}, status_code=403
            )
        if is_gala(event) and role == "guest":
            return JSONResponse(
                {"error": "tonight's photos are added by the table hosts - enjoy the show!"},
                status_code=403,
            )
        if event["uploads_locked"] and role != "admin":
            # The host has closed the album to finish the keepsake book.
            return JSONResponse(
                {"error": "the host has closed the album to new uploads"},
                status_code=403,
            )
        if trial_state(event)["expired"]:
            return JSONResponse(
                {"error": "the free week for this album has ended - the host"
                          " can unlock it to keep adding photos"},
                status_code=403,
            )
        dirs = event_dirs(data_dir, event["id"])
        member = current_member(request, event) if role == "member" else None
        device = re.sub(r"[^\w-]", "", device)[:40]
        if member is not None:
            device = f"member-{member['id']}"
        limit = event["guest_upload_limit"]
        remaining = None
        if limit and role not in ("admin", "staff"):
            # Per-guest photo allowance, chosen by the host. Guests are
            # identified by a per-browser id (same one used for book picks).
            bucket = device or f"ip-{request.client.host if request.client else 'unknown'}"
            used = 0
            for meta_file in dirs["meta"].glob("*.json"):
                meta = _read_meta(dirs, meta_file.stem)
                if meta is not None and meta.get("device") == bucket:
                    used += 1
            remaining = limit - used
            if remaining <= 0:
                return JSONResponse(
                    {"error": f"you've shared your {limit} photos - thank you!"},
                    status_code=403,
                )
            device = bucket
        uploader = re.sub(r"\s+", " ", uploader).strip()[:60]
        if member is not None and not uploader:
            uploader = member["name"]
        saved, errors = [], []
        for upload_file in files:
            original_name = upload_file.filename or "photo"
            ext = Path(original_name).suffix.lower()
            if remaining is not None and remaining <= 0:
                errors.append({"file": original_name,
                               "reason": f"the host's limit is {limit} photos per guest"})
                continue
            try:
                if ext in VIDEO_EXTENSIONS:
                    meta = await _save_video(upload_file, original_name, uploader, ext, dirs)
                else:
                    meta = await _save_photo(upload_file, original_name, uploader, ext, dirs)
                    if ai_agents.ai_enabled():
                        background.add_task(_caption_task, event["id"], meta["id"])
                changed = False
                if device and role not in ("admin", "staff"):
                    meta["device"] = device
                    changed = True
                if member is not None:
                    # a table host's photos are tagged to their table
                    meta["tagged"] = [member["id"]]
                    changed = True
                if changed:
                    (dirs["meta"] / f"{meta['id']}.json").write_text(json.dumps(meta))
                saved.append(meta)
                if remaining is not None:
                    remaining -= 1
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
        role = gallery_role(request, event)
        if role is None:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        dirs = event_dirs(data_dir, event["id"])
        if role == "member":
            member = current_member(request, event)
            if member is None or not member_can_view(dirs, photo_id, member["id"]):
                return JSONResponse({"error": "not found"}, status_code=404)
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
        role = gallery_role(request, event)
        if role is None:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        dirs = event_dirs(data_dir, event["id"])
        if role == "member":
            member = current_member(request, event)
            if member is None or not member_can_view(dirs, photo_id, member["id"]):
                return JSONResponse({"error": "not found"}, status_code=404)
        path = dirs["thumbs"] / f"{photo_id}.jpg"
        if path.exists():
            return FileResponse(path, media_type="image/jpeg")
        full = _find_media_file(dirs["photos"], photo_id)
        if full is not None:
            return FileResponse(full)
        return JSONResponse({"error": "not found"}, status_code=404)

    return app


app = create_app()
