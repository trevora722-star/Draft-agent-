"""ConfettiRoll AI agents — album curator and recap writer.

Backed by the Anthropic API (Claude Opus 5). Everything here is best-effort
and optional: if the SDK or ANTHROPIC_API_KEY is missing, or a single call
fails, the platform keeps working — photos just won't get captions, search
enrichment, highlight scores, or recaps.

Agents:
  - caption_photo: vision pass over each uploaded photo. Produces a warm
    one-line caption, search tags, and a 1-10 highlight score. Runs as a
    FastAPI background task on upload.
  - generate_recap: reads the whole album's captions/uploaders/timestamps
    and writes "the story of the event" for the host to share.

Server-side refusal fallbacks are enabled by default (`fallbacks: "default"`)
so the rare safety-classifier decline on Claude Opus 5 is transparently
retried on Anthropic's recommended fallback model.
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

try:
    import anthropic

    SDK_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    SDK_AVAILABLE = False

DEFAULT_MODEL = "claude-opus-5"
FALLBACK_BETAS = ["server-side-fallback-2026-07-01"]

_client = None


def model() -> str:
    return os.environ.get("CR_AI_MODEL", DEFAULT_MODEL)


def ai_enabled() -> bool:
    return SDK_AVAILABLE and bool(os.environ.get("ANTHROPIC_API_KEY"))


def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


CAPTION_SCHEMA = {
    "type": "object",
    "properties": {
        "caption": {
            "type": "string",
            "description": "One warm, specific sentence describing the moment, under 120 characters. No emoji.",
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "3-6 lowercase single-word search tags (e.g. dancing, cake, toast, kids, outdoors, group)",
        },
        "quality": {
            "type": "integer",
            "enum": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
            "description": "Highlight-worthiness: sharpness, composition, and emotional impact. 8+ means album-highlight material.",
        },
    },
    "required": ["caption", "tags", "quality"],
    "additionalProperties": False,
}


def caption_photo(thumb_path: Path) -> dict | None:
    """Caption, tag, and score one photo. Returns None on any failure."""
    if not ai_enabled():
        return None
    try:
        image_data = base64.standard_b64encode(thumb_path.read_bytes()).decode()
        response = _get_client().beta.messages.create(
            model=model(),
            max_tokens=1024,
            betas=FALLBACK_BETAS,
            fallbacks="default",
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": CAPTION_SCHEMA},
            },
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "This is a guest-uploaded photo from a private event album "
                            "(a wedding, party, or similar celebration). Describe it for "
                            "the shared gallery."
                        ),
                    },
                ],
            }],
        )
        if response.stop_reason == "refusal":
            return None
        text = next(b.text for b in response.content if b.type == "text")
        result = json.loads(text)
        result["tags"] = [str(t).lower()[:30] for t in result.get("tags", [])][:6]
        result["caption"] = str(result.get("caption", ""))[:200]
        result["quality"] = max(1, min(10, int(result.get("quality", 5))))
        return result
    except Exception:
        return None


BOOTH_SYSTEM = """You are the ConfettiRoll booth assistant at a wedding & events trade show, \
chatting with visitors on a tablet at the booth.

What ConfettiRoll is: a private photo- and video-sharing platform for events. \
Guests scan a QR code, enter the event password, and every photo they take lands \
in one beautiful shared gallery — no app to install. Includes a live big-screen \
slideshow where new photos appear seconds after upload (it's running on the TV \
at this booth), AI that captions and organizes every photo so albums are \
searchable with automatic highlights, a one-click AI-written recap of the day, \
and a print-ready keepsake book the couple can send to family.

Who you're talking to and what to offer:
- Couples / hosts: one shared album for their wedding, free while in beta.
- Wedding & event planners: the partner program — a personal referral link and \
QR code, earning 50% of each referred client's first package (wholesale 10-packs at ~50% off for pros who want to set their own retail price). Instant signup, no \
application.
- Venues (wineries, golf courses, event spaces, corporate venues): the \
white-label tier — their own branded photo page on their own domain, their logo \
and photos, unlimited events, with an AI brand agent that writes their page \
copy. Planned at roughly $79/month once billing launches.

Ground rules:
- Keep replies to 2-4 short, warm sentences — this is a busy expo floor. No markdown.
- Answer only from the knowledge above; if asked something you don't know \
(exact launch dates, integrations, custom contracts), say the team will follow \
up — a great moment to collect contact info.
- Early in the conversation, find out whether they're a couple, a planner, or a \
venue, and tailor everything to that.
- When the conversation is warm, invite them to leave their name and email so \
the team can follow up (or set up their venue page / partner link for them). \
When they share contact details, save them with the save_lead tool, then \
confirm warmly that the team will be in touch.
- Stay on topic; politely steer unrelated conversations back to the product or \
suggest they visit the website."""

BOOTH_LEAD_TOOL = {
    "name": "save_lead",
    "description": (
        "Save a booth visitor's contact details so the team can follow up. "
        "Call this as soon as a visitor shares their contact info."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Visitor's name"},
            "email": {"type": "string", "description": "Visitor's email address"},
            "role": {
                "type": "string",
                "enum": ["couple", "planner", "venue", "other"],
                "description": "What kind of visitor they are",
            },
            "interest": {
                "type": "string",
                "description": "One line on what they're interested in (e.g. 'winery, white-label, ~30 weddings/yr')",
            },
        },
        "required": ["name", "email", "role", "interest"],
        "additionalProperties": False,
    },
}


def booth_reply(history: list[dict], save_lead) -> str | None:
    """One booth-agent turn. history = [{role, content}, ...]; save_lead is a
    callback(name, email, role, interest) that persists the lead."""
    if not ai_enabled():
        return None
    messages = list(history)
    try:
        for _ in range(3):
            response = _get_client().beta.messages.create(
                model=model(),
                max_tokens=1024,
                betas=FALLBACK_BETAS,
                fallbacks="default",
                system=BOOTH_SYSTEM,
                tools=[BOOTH_LEAD_TOOL],
                messages=messages,
            )
            if response.stop_reason == "refusal":
                return None
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                return next(
                    (b.text for b in response.content if b.type == "text"), ""
                ).strip() or None
            messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in tool_uses:
                try:
                    save_lead(
                        str(block.input.get("name", ""))[:80],
                        str(block.input.get("email", ""))[:120],
                        str(block.input.get("role", "other"))[:20],
                        str(block.input.get("interest", ""))[:300],
                    )
                    results.append({
                        "type": "tool_result", "tool_use_id": block.id,
                        "content": "Lead saved - the team will follow up.",
                    })
                except Exception:
                    results.append({
                        "type": "tool_result", "tool_use_id": block.id,
                        "content": "Couldn't save the lead - apologize and ask them to visit the website.",
                        "is_error": True,
                    })
            messages.append({"role": "user", "content": results})
        return None
    except Exception:
        return None


PITCH_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {
            "type": "string",
            "description": "Email subject line, under 60 characters, specific to this prospect. No clickbait, no emoji.",
        },
        "body": {
            "type": "string",
            "description": "The outreach email body: plain text, 120-170 words, personal, specific to their business type, one clear call to action. Sign off as 'The ConfettiRoll team'. No markdown, no placeholders like [Name].",
        },
    },
    "required": ["subject", "body"],
    "additionalProperties": False,
}


def write_pitch(prospect: dict, partner_rate: str, signup_url: str) -> dict | None:
    """Draft a personalized partner-recruitment email for one prospect."""
    if not ai_enabled():
        return None
    kind = prospect.get("type", "planner")
    angle = {
        "photographer": (
            "They shoot the professional photos; we collect the guest photos. "
            "Position us as complementary, never competitive: recommending us "
            "makes their delivery look richer, and they earn on every referral."
        ),
        "planner": (
            "Planners recommend vendors constantly; this is a new line item "
            "that costs their clients little and pays the planner on every "
            "referral, with a personal link and QR they can put in welcome packets."
        ),
        "venue": (
            "Venues can go beyond referrals: a white-label branded photo page "
            "on their own domain, unlimited events, a live photo wall for "
            "their TVs. Referral earnings are the easy first step."
        ),
    }.get(kind, "General event professional; keep it broad but concrete.")
    try:
        response = _get_client().beta.messages.create(
            model=model(),
            max_tokens=1024,
            betas=FALLBACK_BETAS,
            fallbacks="default",
            output_config={"format": {"type": "json_schema", "schema": PITCH_SCHEMA}},
            messages=[{
                "role": "user",
                "content": (
                    "Write a cold outreach email recruiting an event professional "
                    "as a ConfettiRoll partner.\n\n"
                    f"Prospect: {prospect.get('name', '')} at "
                    f"{prospect.get('business', '')} ({kind}"
                    + (f", {prospect['city']}" if prospect.get("city") else "") + ")\n"
                    + (f"Notes: {prospect['notes']}\n" if prospect.get("notes") else "")
                    + f"\nAngle for this prospect type: {angle}\n\n"
                    "The offer: ConfettiRoll is a private guest photo-sharing "
                    "platform for weddings and events (QR code in, one shared "
                    "album, live big-screen slideshow, AI-organized photos, a "
                    f"keepsake book). Partners earn {partner_rate} of what their "
                    f"referred clients spend, tracked from a personal link - "
                    f"instant self-serve signup at {signup_url}, no application.\n\n"
                    "Tone: warm, brief, specific to their business; open with "
                    "something true about their type of work, not flattery. One "
                    "CTA: sign up at the link (or reply with questions)."
                ),
            }],
        )
        if response.stop_reason == "refusal":
            return None
        pitch = json.loads(next(b.text for b in response.content if b.type == "text"))
        pitch["subject"] = str(pitch.get("subject", ""))[:120]
        pitch["body"] = str(pitch.get("body", ""))[:2500]
        return pitch
    except Exception:
        return None


BRAND_KIT_SCHEMA = {
    "type": "object",
    "properties": {
        "tagline": {
            "type": "string",
            "description": "A short tagline for the venue, under 60 characters. No quotes, no emoji.",
        },
        "headline": {
            "type": "string",
            "description": "A welcoming headline for the venue's photo-sharing page, under 70 characters, addressed to event guests.",
        },
        "about": {
            "type": "string",
            "description": "2-3 warm sentences about the venue and how guests share photos here. Plain prose.",
        },
        "accent": {
            "type": "string",
            "description": "A hex color (like #7a2f45) for buttons and accents, matched to the venue's logo/brand. Must contrast well against a cream background.",
        },
    },
    "required": ["tagline", "headline", "about", "accent"],
    "additionalProperties": False,
}


def generate_brand_kit(name: str, venue_type: str, notes: str,
                       logo_bytes: bytes | None = None,
                       logo_media_type: str = "image/png") -> dict | None:
    """Build a venue's brand voice + accent color from its logo and description."""
    if not ai_enabled():
        return None
    content: list = []
    if logo_bytes:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": logo_media_type,
                "data": base64.standard_b64encode(logo_bytes).decode(),
            },
        })
    content.append({
        "type": "text",
        "text": (
            f'You are the brand copywriter for "{name}", a {venue_type or "venue"} '
            "that hosts weddings, parties, and corporate events. "
            + (f"Notes from the venue: {notes}\n" if notes else "")
            + ("Their logo is attached - match its personality and pick an accent "
               "color drawn from it. " if logo_bytes else "")
            + "They offer every event a private shared photo album where guests "
            "upload photos and videos. Write the brand kit for that page."
        ),
    })
    try:
        response = _get_client().beta.messages.create(
            model=model(),
            max_tokens=1024,
            betas=FALLBACK_BETAS,
            fallbacks="default",
            output_config={
                "format": {"type": "json_schema", "schema": BRAND_KIT_SCHEMA},
            },
            messages=[{"role": "user", "content": content}],
        )
        if response.stop_reason == "refusal":
            return None
        kit = json.loads(next(b.text for b in response.content if b.type == "text"))
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", kit.get("accent", "")):
            kit["accent"] = "#e85d8a"
        for key in ("tagline", "headline", "about"):
            kit[key] = str(kit.get(key, "")).strip()[:400]
        return kit
    except Exception:
        return None


def generate_recap(event_title: str, photos: list[dict]) -> str | None:
    """Write a shareable recap of the event from the album's metadata."""
    if not ai_enabled() or not photos:
        return None
    lines = []
    for p in sorted(photos, key=lambda x: x.get("uploaded_at", 0)):
        parts = [p.get("type", "photo")]
        if p.get("uploader"):
            parts.append(f"shared by {p['uploader']}")
        if p.get("caption"):
            parts.append(p["caption"])
        if p.get("tags"):
            parts.append("tags: " + ", ".join(p["tags"]))
        lines.append(" — ".join(parts))
    album_digest = "\n".join(lines[:400])

    try:
        response = _get_client().beta.messages.create(
            model=model(),
            max_tokens=2048,
            betas=FALLBACK_BETAS,
            fallbacks="default",
            messages=[{
                "role": "user",
                "content": (
                    f'You are the album storyteller for "{event_title}", a private '
                    "shared photo album from a celebration. Below is the album's "
                    "metadata: one line per photo/video with who shared it and a "
                    "caption where available.\n\n"
                    f"{album_digest}\n\n"
                    "Write a warm, shareable recap of the event (150-250 words) that "
                    "the host can send to guests alongside the album link. Tell it as "
                    "a story of the day: the moments, the people who captured them, "
                    "the mood. Mention a few uploaders by name to thank them. Do not "
                    "invent specific facts that aren't supported by the captions - "
                    "keep unsupported details general. No emoji, no headings; plain "
                    "flowing prose."
                ),
            }],
        )
        if response.stop_reason == "refusal":
            return None
        return next(b.text for b in response.content if b.type == "text").strip()
    except Exception:
        return None
