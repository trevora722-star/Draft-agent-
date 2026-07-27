"""Wedding photo & video sharing platform.

A single shared username + password (set via environment variables) gates
everything: guests log in once, then can view the full gallery and upload
their own photos and videos. Signing in with the same username and the
separate WEDDING_ADMIN_PASSWORD grants an admin session that can delete
items (they're moved to a trash folder, not destroyed). Media lives on disk
under WEDDING_DATA_DIR so a mounted persistent volume keeps it across
restarts.

Run locally:
    pip install -r requirements.txt
    uvicorn app:app --reload
Then open http://127.0.0.1:8000 (default login guest / wedding).
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
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
)
from PIL import Image, ImageOps

try:  # iPhone photos arrive as HEIC; convert them so browsers can show them.
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF_SUPPORTED = True
except ImportError:  # pragma: no cover - optional dependency
    HEIF_SUPPORTED = False

BASE_DIR = Path(__file__).resolve().parent

SESSION_COOKIE = "wedding_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30  # stay logged in for 30 days
MAX_IMAGE_BYTES = 30 * 1024 * 1024  # 30 MB per photo
MAX_VIDEO_BYTES = 200 * 1024 * 1024  # 200 MB per video
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

LOGIN_ATTEMPT_LIMIT = 20  # per IP within the window below
LOGIN_ATTEMPT_WINDOW = 15 * 60


def create_app() -> FastAPI:
    site_title = os.environ.get("WEDDING_TITLE", "Our Wedding Album")
    username = os.environ.get("WEDDING_USERNAME", "guest")
    password = os.environ.get("WEDDING_PASSWORD", "")
    admin_password = os.environ.get("WEDDING_ADMIN_PASSWORD", "")
    if not password:
        password = "wedding"
        print(
            "WARNING: WEDDING_PASSWORD is not set - using the default dev "
            "password 'wedding'. Set WEDDING_USERNAME / WEDDING_PASSWORD "
            "before sharing the site."
        )

    data_dir = Path(os.environ.get("WEDDING_DATA_DIR", BASE_DIR / "data"))
    photos_dir = data_dir / "photos"
    thumbs_dir = data_dir / "thumbs"
    meta_dir = data_dir / "meta"
    trash_dir = data_dir / "trash"
    for d in (photos_dir, thumbs_dir, meta_dir, trash_dir):
        d.mkdir(parents=True, exist_ok=True)

    secret = _load_secret(data_dir)

    login_page = (BASE_DIR / "templates" / "login.html").read_text()
    gallery_page = (BASE_DIR / "templates" / "gallery.html").read_text()
    login_page = login_page.replace("{{TITLE}}", site_title)
    gallery_page = gallery_page.replace("{{TITLE}}", site_title)

    app = FastAPI(title=site_title, docs_url=None, redoc_url=None)
    login_attempts: dict[str, list[float]] = {}

    # ---- session helpers ---------------------------------------------------

    def make_token(role: str) -> str:
        exp = str(int(time.time()) + SESSION_TTL_SECONDS)
        payload = f"{exp}.{role}"
        sig = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}.{sig}"

    def session_role(request: Request) -> str | None:
        """Return 'guest' or 'admin' for a valid session, else None."""
        token = request.cookies.get(SESSION_COOKIE)
        if not token or token.count(".") != 2:
            return None
        exp, role, sig = token.split(".")
        expected = hmac.new(secret, f"{exp}.{role}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        if role not in ("guest", "admin") or not exp.isdigit() or int(exp) <= time.time():
            return None
        return role

    def too_many_attempts(ip: str) -> bool:
        now = time.time()
        attempts = [t for t in login_attempts.get(ip, []) if now - t < LOGIN_ATTEMPT_WINDOW]
        login_attempts[ip] = attempts
        return len(attempts) >= LOGIN_ATTEMPT_LIMIT

    # ---- pages -------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def gallery(request: Request):
        if session_role(request) is None:
            return RedirectResponse("/login", status_code=303)
        return HTMLResponse(gallery_page)

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        if session_role(request) is not None:
            return RedirectResponse("/", status_code=303)
        return HTMLResponse(login_page)

    @app.post("/login")
    def login(request: Request, form_username: str = Form(alias="username"),
              form_password: str = Form(alias="password")):
        ip = request.client.host if request.client else "unknown"
        if too_many_attempts(ip):
            return HTMLResponse(
                login_page.replace(
                    "<!--ERROR-->",
                    '<p class="error">Too many attempts - please wait a few minutes.</p>',
                ),
                status_code=429,
            )
        user_ok = hmac.compare_digest(form_username.strip(), username)
        admin_ok = bool(admin_password) and hmac.compare_digest(form_password, admin_password)
        guest_ok = hmac.compare_digest(form_password, password)
        if not (user_ok and (admin_ok or guest_ok)):
            login_attempts.setdefault(ip, []).append(time.time())
            return HTMLResponse(
                login_page.replace(
                    "<!--ERROR-->",
                    '<p class="error">That username or password isn\'t right.</p>',
                ),
                status_code=401,
            )
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            make_token("admin" if admin_ok else "guest"),
            max_age=SESSION_TTL_SECONDS,
            httponly=True,
            samesite="lax",
        )
        return response

    @app.post("/logout")
    def logout():
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE)
        return response

    @app.get("/health")
    def health():
        return {"ok": True}

    # ---- media API ---------------------------------------------------------

    @app.get("/api/photos")
    def list_photos(request: Request):
        role = session_role(request)
        if role is None:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        photos = []
        for meta_file in meta_dir.glob("*.json"):
            try:
                photos.append(json.loads(meta_file.read_text()))
            except (OSError, json.JSONDecodeError):
                continue
        photos.sort(key=lambda p: p.get("uploaded_at", 0), reverse=True)
        return {"photos": photos, "is_admin": role == "admin"}

    @app.post("/api/upload")
    async def upload(
        request: Request,
        files: list[UploadFile] = File(...),
        uploader: str = Form(""),
    ):
        if session_role(request) is None:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        uploader = re.sub(r"\s+", " ", uploader).strip()[:60]
        saved, errors = [], []
        for upload_file in files:
            original_name = upload_file.filename or "photo"
            ext = Path(original_name).suffix.lower()
            try:
                if ext in VIDEO_EXTENSIONS:
                    meta = await _save_video(
                        upload_file, original_name, uploader, ext,
                        photos_dir, meta_dir,
                    )
                else:
                    meta = await _save_photo(
                        upload_file, original_name, uploader, ext,
                        photos_dir, thumbs_dir, meta_dir,
                    )
                saved.append(meta)
            except PhotoError as exc:
                errors.append({"file": original_name, "reason": str(exc)})
        return {"saved": saved, "errors": errors}

    @app.delete("/api/photos/{photo_id}")
    def delete_photo(request: Request, photo_id: str):
        role = session_role(request)
        if role is None:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        if role != "admin":
            return JSONResponse({"error": "admin only"}, status_code=403)
        path = _find_media_file(photos_dir, photo_id)
        if path is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        # Soft delete: move everything into the trash folder so a mistaken
        # delete can be undone by moving the files back. Subfolders mirror
        # the live layout - the photo and its thumbnail share a filename.
        moves = [
            (path, "photos"),
            (thumbs_dir / f"{photo_id}.jpg", "thumbs"),
            (meta_dir / f"{photo_id}.json", "meta"),
        ]
        for src, sub in moves:
            if src.exists():
                dest = trash_dir / sub
                dest.mkdir(exist_ok=True)
                shutil.move(str(src), dest / src.name)
        return {"deleted": photo_id}

    @app.get("/photos/{photo_id}")
    def photo(request: Request, photo_id: str, download: bool = False):
        if session_role(request) is None:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        path = _find_media_file(photos_dir, photo_id)
        if path is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        headers = {}
        if download:
            name = photo_id
            meta_file = meta_dir / f"{photo_id}.json"
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
        if session_role(request) is None:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        path = thumbs_dir / f"{photo_id}.jpg"
        if path.exists():
            return FileResponse(path, media_type="image/jpeg")
        full = _find_media_file(photos_dir, photo_id)
        if full is not None:
            return FileResponse(full)
        return JSONResponse({"error": "not found"}, status_code=404)

    return app


class PhotoError(Exception):
    """Raised when a single uploaded file can't be accepted."""


def _load_secret(data_dir: Path) -> bytes:
    """Signing key for session cookies, persisted so restarts keep guests logged in."""
    env_secret = os.environ.get("WEDDING_SECRET_KEY")
    if env_secret:
        return env_secret.encode()
    secret_file = data_dir / "secret_key"
    if secret_file.exists():
        return secret_file.read_bytes()
    secret = secrets.token_bytes(32)
    secret_file.write_bytes(secret)
    return secret


def _find_media_file(photos_dir: Path, photo_id: str) -> Path | None:
    if not re.fullmatch(r"[0-9a-f]{32}", photo_id):
        return None
    for path in photos_dir.glob(f"{photo_id}.*"):
        return path
    return None


async def _save_photo(
    upload_file: UploadFile,
    original_name: str,
    uploader: str,
    ext: str,
    photos_dir: Path,
    thumbs_dir: Path,
    meta_dir: Path,
) -> dict:
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
        # Browsers can't display HEIC - store a high-quality JPEG instead.
        stored = photos_dir / f"{photo_id}.jpg"
        image.convert("RGB").save(stored, "JPEG", quality=92)
        original_name = Path(original_name).stem + ".jpg"
    else:
        stored = photos_dir / f"{photo_id}{ext}"
        stored.write_bytes(data)

    thumbnail = image.convert("RGB")
    thumbnail.thumbnail((THUMB_MAX_DIM, THUMB_MAX_DIM))
    thumbnail.save(thumbs_dir / f"{photo_id}.jpg", "JPEG", quality=80)

    meta = {
        "id": photo_id,
        "type": "photo",
        "original_name": original_name,
        "uploader": uploader,
        "uploaded_at": int(time.time()),
        "width": image.width,
        "height": image.height,
    }
    (meta_dir / f"{photo_id}.json").write_text(json.dumps(meta))
    return meta


async def _save_video(
    upload_file: UploadFile,
    original_name: str,
    uploader: str,
    ext: str,
    photos_dir: Path,
    meta_dir: Path,
) -> dict:
    photo_id = uuid.uuid4().hex
    dest = photos_dir / f"{photo_id}{ext}"
    size = 0
    # Stream to disk in chunks - videos are too big to hold in memory.
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
    (meta_dir / f"{photo_id}.json").write_text(json.dumps(meta))
    return meta


app = create_app()
