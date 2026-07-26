"""PhotoAnalysisAgent — one vision call per uploaded photo.

Combines moderation + captioning + tagging + quality scoring into a single
Claude vision call (see llm.analyze_photo) so the expensive vision call
happens exactly once per photo, not once per concern.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .. import llm

VALID_MOMENTS = {"ceremony", "cocktail_hour", "reception", "dancing", "other"}


@dataclass(frozen=True)
class PhotoAnalysis:
    is_appropriate: bool
    is_blurry: bool
    caption: str
    tags: list[str]
    moment: str
    quality_score: int

    @property
    def status(self) -> str:
        """Auto-moderation decision.

        Inappropriate content is never silently deleted — it's routed to
        the moderator review queue (`flagged`), never a hard `rejected`,
        so a false positive can be overridden by the couple/wedding party.
        Blur/quality never affects visibility, only curation ranking.
        """
        return "approved" if self.is_appropriate else "flagged"


def _parse(raw_text: str) -> PhotoAnalysis:
    text = raw_text.strip()
    # Models sometimes wrap JSON in markdown fences despite instructions.
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    data = json.loads(text)

    moment = data.get("moment", "other")
    if moment not in VALID_MOMENTS:
        moment = "other"

    quality = int(data.get("quality_score", 5))
    quality = max(1, min(10, quality))

    return PhotoAnalysis(
        is_appropriate=bool(data.get("is_appropriate", True)),
        is_blurry=bool(data.get("is_blurry", False)),
        caption=str(data.get("caption", "")).strip(),
        tags=[str(t) for t in data.get("tags", [])],
        moment=moment,
        quality_score=quality,
    )


class PhotoAnalysisAgent:
    name = "photo-analysis"

    def analyze(self, image_bytes: bytes, media_type: str = "image/jpeg") -> PhotoAnalysis:
        raw = llm.analyze_photo(image_bytes, media_type=media_type)
        return _parse(raw)
