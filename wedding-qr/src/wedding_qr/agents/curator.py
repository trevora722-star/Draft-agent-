"""CurationAgent — reasons over the whole collection's metadata (text only).

Deliberately never re-looks at the images: by the time curation runs, every
approved photo already carries a caption/tags/moment/quality_score from
PhotoAnalysisAgent, so picking a diverse highlight reel out of hundreds or
thousands of photos is a cheap text-reasoning pass, not another vision call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .. import llm


@dataclass(frozen=True)
class CurationResult:
    highlight_photo_ids: list[str]
    narrative: str


def _parse(raw_text: str) -> tuple[list[str], str]:
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    data = json.loads(text)
    ids = [str(i) for i in data.get("highlight_photo_ids", [])]
    narrative = str(data.get("narrative", "")).strip()
    return ids, narrative


class CurationAgent:
    name = "curation"

    def curate(self, photos: list[dict], *, max_highlights: int = 30) -> CurationResult:
        """`photos` is a list of dicts with id/caption/tags/moment/quality_score/guest_name."""
        if not photos:
            return CurationResult(highlight_photo_ids=[], narrative="No photos uploaded yet.")

        lines = []
        for p in photos:
            lines.append(
                f"id={p['id']} | moment={p.get('moment')} | quality={p.get('quality_score')} "
                f"| tags={p.get('tags')} | guest={p.get('guest_name') or 'anonymous'} "
                f"| caption: {p.get('caption')}"
            )
        prompt = (
            f"There are {len(photos)} approved photos. Pick up to {max_highlights} for the "
            "highlight reel.\n\n" + "\n".join(lines)
        )
        raw = llm.curate(prompt)
        ids, narrative = _parse(raw)
        # Guard against the model inventing ids that don't exist in the input.
        known_ids = {p["id"] for p in photos}
        ids = [i for i in ids if i in known_ids][:max_highlights]
        return CurationResult(highlight_photo_ids=ids, narrative=narrative)
