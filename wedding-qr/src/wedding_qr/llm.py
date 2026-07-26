"""Anthropic client wrapper.

Two entry points, each a single function so tests can monkeypatch them
directly (same pattern as npo_agent.llm.complete) without ever hitting
the network:

    analyze_photo(image_bytes, media_type)  -> one vision call per photo,
        used by PhotoAnalysisAgent for moderation + captioning + tagging.

    curate(prompt) -> one text call over the whole collection's metadata,
        used by CurationAgent to build the highlight reel.

Both return raw text; callers are responsible for parsing the JSON they
asked the model to produce (kept simple on purpose — no tool-use schema
needed for a two-field response shape).
"""

from __future__ import annotations

import base64
from functools import lru_cache

import anthropic

from .config import get_settings

ANALYSIS_SYSTEM_PROMPT = """You are the photo intake agent for a wedding guest-upload gallery.
For the single image given, respond with ONLY a JSON object (no prose, no markdown fences):

{
  "is_appropriate": true/false,   // false only for nudity, violence, or clearly offensive content
  "is_blurry": true/false,        // true if too blurry/dark/unusable to feature
  "caption": "short warm one-sentence caption",
  "tags": ["ceremony"|"vows"|"reception"|"dancing"|"cake"|"toast"|"candid"|"portrait"|"kids"|"group", ...],
  "moment": "ceremony"|"cocktail_hour"|"reception"|"dancing"|"other",
  "quality_score": 1-10          // aesthetic/composition quality, for highlight-reel ranking
}

Be generous with is_appropriate — only flag genuinely inappropriate content, not just
unflattering photos. Err toward including photos of children as fine unless the content
itself is inappropriate."""

CURATION_SYSTEM_PROMPT = """You are the curation agent for a wedding photo gallery.
You are given the metadata (captions, tags, moment, quality score, guest name) for every
approved photo, NOT the images themselves. Select a diverse, non-redundant highlight reel:
prefer higher quality_score, spread across moments (ceremony, reception, dancing, etc.),
avoid picking many near-duplicate captions/tags from the same moment, and order the result
chronologically by moment (ceremony -> cocktail_hour -> reception -> dancing -> other).

Respond with ONLY a JSON object (no prose, no markdown fences):
{
  "highlight_photo_ids": ["id1", "id2", ...],
  "narrative": "2-3 sentence warm summary of the day for the couple"
}"""


@lru_cache
def _client() -> anthropic.Anthropic:
    settings = get_settings()
    return anthropic.Anthropic(api_key=settings.anthropic_api_key or "")


def analyze_photo(image_bytes: bytes, media_type: str = "image/jpeg") -> str:
    settings = get_settings()
    client = _client()
    message = client.messages.create(
        model=settings.analysis_model,
        max_tokens=500,
        system=ANALYSIS_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64.b64encode(image_bytes).decode("ascii"),
                        },
                    },
                    {"type": "text", "text": "Analyze this wedding guest photo."},
                ],
            }
        ],
    )
    return "".join(
        block.text for block in message.content if getattr(block, "type", None) == "text"
    )


def curate(prompt: str) -> str:
    settings = get_settings()
    client = _client()
    message = client.messages.create(
        model=settings.curation_model,
        max_tokens=1500,
        system=CURATION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(
        block.text for block in message.content if getattr(block, "type", None) == "text"
    )
