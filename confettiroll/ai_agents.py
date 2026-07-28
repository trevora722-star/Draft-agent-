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
