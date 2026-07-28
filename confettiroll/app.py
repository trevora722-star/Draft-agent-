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


def new_referral_code() -> str:
    # Short, human-friendly, unambiguous (no 0/O/1/l).
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


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
        for name in ("landing", "signup", "login", "dashboard", "guest_login", "gallery")
    }

    app = FastAPI(title="ConfettiRoll", docs_url=None, redoc_url=None)
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

    def page(name: str, **subs: str) -> HTMLResponse:
        html = tpl[name]
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
        if current_user(request) is not None:
            return RedirectResponse("/dashboard", status_code=303)
        return page("landing", base=base_domain)

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
            return page("guest_login", title=esc(event["title"]), error="")
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
        return page(
            "dashboard",
            user_name=esc(user["name"] or user["email"]),
            events="\n".join(rows) or '<p class="empty">No events yet — create your first one above.</p>',
            base_domain=esc(base_domain),
            error=error_html,
            ref_code=esc(referral_code),
            ref_link=esc(f"https://{base_domain}/signup?ref={referral_code}"),
            ref_signups=str(ref_signups),
            ref_events=str(ref_events),
            ref_pending=f"${pending:,.2f}",
        )

    @app.post("/api/events")
    def create_event(request: Request, title: str = Form(...), slug: str = Form(...),
                     guest_password: str = Form(...), event_date: str = Form(""),
                     custom_domain: str = Form("")):
        user = current_user(request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
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
                conn.execute(
                    "INSERT INTO events (id, owner_id, slug, title, event_date, guest_password, custom_domain, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (event_id, user["id"], slug, title, event_date.strip()[:40],
                     guest_password, custom_domain or None, int(time.time())),
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

    @app.get("/health")
    def health():
        return {"ok": True}

    # =======================================================================
    # tenant (event gallery) handlers
    # =======================================================================

    def tenant_gallery(request: Request, event: sqlite3.Row):
        if gallery_role(request, event) is None:
            return RedirectResponse("/login", status_code=303)
        return page("gallery", title=esc(event["title"]))

    def tenant_login(request: Request, event: sqlite3.Row, password: str):
        ip = request.client.host if request.client else "unknown"
        key = f"guest:{event['id']}:{ip}"
        if too_many_attempts(key):
            return page("guest_login", title=esc(event["title"]),
                        error=err_html("Too many attempts - please wait a few minutes."))
        if not hmac.compare_digest(password, event["guest_password"]):
            record_attempt(key)
            return page("guest_login", title=esc(event["title"]),
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
