"""POST /api/lead — process a contact-form submission.

Pipeline:
  1. Validate the submission.
  2. Draft a personalized first-reply email from the trainer's voice
     (Claude Haiku 4.5).
  3. Hand off the lead + draft to the trainer via the configured channels
     (webhook and/or Resend; falls back to function logs).
  4. Return a clean success to the page.

The drafted email is sent to the trainer, **not** the lead. The trainer
reviews and sends it from her Gmail. This keeps her tone in the loop and
avoids accidental auto-replies to spam.
"""

from __future__ import annotations

import re

from _shared import (  # type: ignore[import-not-found]
    anthropic_client,
    err,
    extract_text,
    notify,
    ok,
    parse_json,
    safe_handler,
)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

DRAFTER_SYSTEM = """\
You are drafting a personalized reply email from the FDAF Consulting trainer
to a new lead. The trainer will review and send it from her Gmail.

Style:
- Warm, casual, real. No corporate jargon. No "thank you for reaching out."
- Open with something specific from THEIR message — show you read it.
- Confirm what you offer that fits their situation (couples / small group /
  in-home programming). Don't list everything; only what's relevant to them.
- Propose the next step: a free 20-minute phone consult. Offer 2–3 specific
  time-of-day windows (e.g. "weekday evenings after 6, or Saturday morning")
  rather than a calendar link.
- Sign off simply. Don't sign a name — the trainer will add it.

Hard constraints:
- Output ONLY the email body. No subject line, no "Hi [name]," is fine but
  use their actual first name if provided.
- 90–160 words. No bullet lists unless it's clearly useful (e.g. they asked
  about pricing for two services).
- Never invent specifics not in the lead's message (don't guess their age,
  experience, schedule, etc.). If something is unclear, ask it briefly.
- Don't promise specific availability — propose windows, not slots.

Service facts you may reference:
- In-home personal training, West Kelowna + Central Okanagan
- Couples sessions ($95+), small groups of 3–6 ($40+/person), in-home
  programming add-on ($120/mo)
- All equipment provided; she comes to their home
- Free 20-min consult to start
"""


@safe_handler
def handler(event, context):
    body = parse_json(event)

    name = (body.get("name") or "").strip()[:120]
    email = (body.get("email") or "").strip()[:200]
    phone = (body.get("phone") or "").strip()[:40]
    interest = (body.get("type") or "Not specified").strip()[:80]
    message = (body.get("message") or "").strip()[:4000]

    if not name or not email:
        return err(400, "Name and email are required.")
    if not EMAIL_RE.match(email):
        return err(400, "That email address looks off — can you double-check it?")
    if len(message) < 4:
        return err(400, "Tell me a little about your group & goals so I can help.")

    # ----- Draft the reply via Claude --------------------------------------
    lead_block = (
        f"NAME: {name}\n"
        f"EMAIL: {email}\n"
        f"PHONE: {phone or '(not given)'}\n"
        f"INTERESTED IN: {interest}\n"
        f"---\n"
        f"{message}"
    )

    client = anthropic_client()
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        system=[
            {
                "type": "text",
                "text": DRAFTER_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": f"New lead from the website. Draft my reply.\n\n{lead_block}",
            }
        ],
    )
    draft = extract_text(resp) or (
        f"Hi {name.split()[0] if name else 'there'},\n\n"
        "Thanks for getting in touch — I'd love to hear more. "
        "When's a good time for a quick 20-minute call this week?\n"
    )

    # ----- Notify the trainer ----------------------------------------------
    subject = f"New lead: {name} ({interest})"
    notify_body = (
        f"New lead from the FDAF Consulting website.\n\n"
        f"{lead_block}\n\n"
        f"------ Suggested reply (review before sending) ------\n\n"
        f"{draft}\n"
    )
    notify(subject, notify_body, payload={
        "name": name,
        "email": email,
        "phone": phone,
        "interest": interest,
        "message": message,
        "draft_reply": draft,
    })

    return ok({
        "ok": True,
        "message": "Thanks! Your message is in. She'll reply within one business day.",
    })
