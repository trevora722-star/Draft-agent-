from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import events, photos, qr, storage
from .agents import CurationAgent, NotifierAgent, PhotoAnalysisAgent
from .db import init_db

PUBLIC_DIR = Path(__file__).resolve().parent.parent.parent / "public"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Wedding Photo QR", lifespan=_lifespan)

if PUBLIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(PUBLIC_DIR)), name="static")


class CreateEventRequest(BaseModel):
    couple_names: str
    event_date: str | None = None


class ReviewRequest(BaseModel):
    decision: str  # "approve" | "reject"


def _require_event(event_id: str) -> events.Event:
    event = events.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


def _require_guest(event_id: str, token: str) -> events.Event:
    event = _require_event(event_id)
    if not events.check_guest_token(event, token):
        raise HTTPException(status_code=403, detail="Invalid guest token")
    return event


def _require_moderator(event_id: str, token: str) -> events.Event:
    event = _require_event(event_id)
    if not events.check_moderator_token(event, token):
        raise HTTPException(status_code=403, detail="Invalid moderator token")
    return event


@app.post("/events")
def create_event(req: CreateEventRequest):
    event = events.create_event(req.couple_names, req.event_date)
    return {
        "event_id": event.id,
        "couple_names": event.couple_names,
        "guest_token": event.guest_token,
        "moderator_token": event.moderator_token,
        "guest_upload_url": qr.guest_upload_url(event.id, event.guest_token),
        "dashboard_url": qr.dashboard_url(event.id, event.moderator_token),
    }


@app.get("/events/{event_id}/info")
def event_info(event_id: str, token: str):
    """Public-ish event info for the landing page and dashboard.

    Couple names/date aren't sensitive, so either a guest or a moderator
    token unlocks them — the moderator token additionally unlocks the
    guest upload link and review-queue count, which the dashboard needs
    but a guest landing page never should.
    """
    event = _require_event(event_id)
    is_guest = events.check_guest_token(event, token)
    is_moderator = events.check_moderator_token(event, token)
    if not (is_guest or is_moderator):
        raise HTTPException(status_code=403, detail="Invalid token")

    info: dict = {"couple_names": event.couple_names, "event_date": event.event_date}
    if is_moderator:
        info["guest_token"] = event.guest_token
        info["guest_upload_url"] = qr.guest_upload_url(event.id, event.guest_token)
        info["pending_review_count"] = len(photos.list_review_queue(event_id))
    return info


@app.get("/events/{event_id}/qr.png")
def event_qr(event_id: str):
    event = _require_event(event_id)
    url = qr.guest_upload_url(event.id, event.guest_token)
    png = qr.qr_png_bytes(url)
    return Response(content=png, media_type="image/png")


@app.post("/events/{event_id}/photos")
async def upload_photo(
    event_id: str,
    token: str = Form(...),
    file: UploadFile = File(...),
    guest_name: str | None = Form(None),
    caption: str | None = Form(None),
):
    _require_guest(event_id, token)

    raw = await file.read()

    stored = storage.save(raw)
    photo_id = photos.create_photo(
        event_id=event_id,
        guest_name=guest_name,
        guest_caption=caption,
        storage_path=stored.storage_path,
        thumbnail_path=stored.thumbnail_path,
    )

    thumb_bytes = storage.read_bytes(stored.thumbnail_path)
    analysis = PhotoAnalysisAgent().analyze(thumb_bytes)
    photos.apply_analysis(photo_id, analysis)

    # The guest never learns the moderation outcome — always a friendly ack.
    return {"status": "ok", "message": "Thanks! Your photo is uploading to the gallery."}


@app.get("/events/{event_id}/gallery")
def gallery(event_id: str, token: str):
    _require_guest(event_id, token)
    items = photos.list_gallery(event_id)
    return [
        {
            "id": p["id"],
            "guest_name": p["guest_name"],
            "caption": p["ai_caption"] or p["guest_caption"],
            "tags": p["ai_tags"],
            "moment": p["moment"],
            "thumbnail_url": f"/events/{event_id}/photos/{p['id']}/thumbnail?token={token}",
            "created_at": p["created_at"],
        }
        for p in items
    ]


@app.get("/events/{event_id}/photos/{photo_id}/thumbnail")
def photo_thumbnail(event_id: str, photo_id: str, token: str):
    _require_guest(event_id, token)
    photo = photos.get_photo(photo_id)
    if photo is None or photo["event_id"] != event_id:
        raise HTTPException(status_code=404, detail="Photo not found")
    return FileResponse(photo["thumbnail_path"], media_type="image/jpeg")


@app.get("/events/{event_id}/review")
def review_queue(event_id: str, token: str):
    _require_moderator(event_id, token)
    items = photos.list_review_queue(event_id)
    return [
        {
            "id": p["id"],
            "guest_name": p["guest_name"],
            "guest_caption": p["guest_caption"],
            "ai_caption": p["ai_caption"],
            "is_appropriate": p["is_appropriate"],
            "thumbnail_url": f"/events/{event_id}/photos/{p['id']}/review_thumbnail?token={token}",
            "created_at": p["created_at"],
        }
        for p in items
    ]


@app.get("/events/{event_id}/photos/{photo_id}/review_thumbnail")
def review_thumbnail(event_id: str, photo_id: str, token: str):
    _require_moderator(event_id, token)
    photo = photos.get_photo(photo_id)
    if photo is None or photo["event_id"] != event_id:
        raise HTTPException(status_code=404, detail="Photo not found")
    return FileResponse(photo["thumbnail_path"], media_type="image/jpeg")


@app.post("/events/{event_id}/photos/{photo_id}/review")
def submit_review(event_id: str, photo_id: str, req: ReviewRequest, token: str):
    _require_moderator(event_id, token)
    photo = photos.get_photo(photo_id)
    if photo is None or photo["event_id"] != event_id:
        raise HTTPException(status_code=404, detail="Photo not found")
    try:
        photos.review_decision(photo_id, req.decision)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok"}


@app.get("/events/{event_id}/digest")
def digest(event_id: str, token: str):
    event = _require_moderator(event_id, token)
    candidates = photos.curation_candidates(event_id)
    curation = CurationAgent().curate(candidates)
    pending = len(photos.list_review_queue(event_id))
    message = NotifierAgent().compose(
        couple_names=event.couple_names,
        total_photos=len(candidates),
        pending_review_count=pending,
        curation=curation,
    )
    return JSONResponse(
        {
            "subject": message.subject,
            "body": message.body,
            "highlight_photo_ids": curation.highlight_photo_ids,
            "narrative": curation.narrative,
        }
    )
