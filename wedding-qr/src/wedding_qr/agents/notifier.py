"""NotifierAgent — composes the couple-facing digest.

Template-based on purpose: by the time this runs, CurationAgent has already
done the reasoning, so composing the summary line is plain formatting, not
another model call. Kept as its own agent (rather than inlined into the API
route) so a real channel — email/SMS/Slack — can be swapped in later without
touching curation or moderation logic.
"""

from __future__ import annotations

from dataclasses import dataclass

from .curator import CurationResult


@dataclass(frozen=True)
class Digest:
    subject: str
    body: str


class NotifierAgent:
    name = "notifier"

    def compose(
        self,
        *,
        couple_names: str,
        total_photos: int,
        pending_review_count: int,
        curation: CurationResult,
    ) -> Digest:
        subject = f"{couple_names}'s wedding gallery: {total_photos} photos"
        lines = [
            f"Hi {couple_names}!",
            "",
            f"Guests have shared {total_photos} photos so far.",
        ]
        if pending_review_count:
            lines.append(
                f"{pending_review_count} photo(s) need a quick look in the review queue."
            )
        if curation.highlight_photo_ids:
            lines.append(
                f"The highlight reel has {len(curation.highlight_photo_ids)} photos picked "
                "out for you."
            )
        if curation.narrative:
            lines.extend(["", curation.narrative])
        return Digest(subject=subject, body="\n".join(lines))
