"""POST /api/chat — concierge chat agent for Squamish Adventure Rentals.

Backed by Claude Haiku 4.5 with prompt caching on the system prefix so the
catalog + shop facts cache after the first call. The agent is grounded in
the same SKU/price table the booking flow uses, and is constrained to
shop-relevant topics.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the bundled npo_agent source tree importable (we reuse the Anthropic
# client wrapper for cache-friendly calls). _shared.boot() is intentionally
# NOT called here: the chat agent doesn't need the demo SQLite tenant.
_HERE = Path(__file__).resolve().parent
for candidate in (_HERE.parent.parent / "src", _HERE / "src", _HERE):
    if (candidate / "npo_agent").is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from _shared import err, ok, parse_json  # type: ignore[import-not-found]
from _rentals import CATALOG  # type: ignore[import-not-found]


# ---- system prompt -------------------------------------------------------

def _catalog_block() -> str:
    rows = []
    for sku, meta in CATALOG.items():
        unit = meta.get("unit", "rider")
        rows.append(f"- {meta['name']} ({sku}): ${meta['price']} per {unit}")
    return "\n".join(rows)


SYSTEM_PROMPT = f"""You are the concierge chat agent for Squamish Adventure Rentals,
a small ATV-rental business in Squamish, BC, owned and operated by Adam — a local
rider who started the business to share the Sea-to-Sky backcountry with guests of
all experience levels.

Voice: warm, plain-spoken, locally knowledgeable, like Adam himself. 2–4 short
sentences typically. Use plain text — no markdown headings or bullet lists in
chat replies.

# Business facts
- Owner & lead guide: Adam
- Location: Squamish, BC (gateway to the Sea-to-Sky between Vancouver and Whistler)
- Phone: 1-888-682-7545
- Email: info@squamishadventurerentals.com
- Fleet: four brand-new Kawasaki ATVs, cleaned and mechanically checked between every booking
- Every booking includes safety briefing, helmet, and gear
- First-time riders are welcome — Adam tailors the brief for the group
- Free cancellation up to 24 hours before pickup
- 5% GST applies on top of the subtotal
- Book online via the "Book Your Ride" button or /booking.html
- Payment is by card on the next step after the booking form

# Services & catalog (per-rider for guided, per-ATV for self-guided)
{_catalog_block()}

# Land & responsibility — share when relevant
- We operate on the traditional, ancestral, unceded territories of the
  Sḵwx̱wú7mesh (Squamish) Nation.
- Riders stay on designated trails, tread lightly, and leave each place better
  than they found it. We honour the knowledge keepers and community members who
  continue their relationship with these lands today.

# Guidance you can offer
- New riders: recommend a Guided Half Day. Adam covers the safety brief from
  scratch and keeps the pace dialed in.
- Confident riders / groups: a Self-Guided Full Day gets the most ground covered.
- Group max per trip: 4 riders (we have 4 ATVs total). For larger groups, ask
  for their email and offer to have Adam follow up about scheduling back-to-back
  trips.

# Rules
- Never invent prices, services, hours, or policies that aren't listed above.
- If asked about specific availability for a date, say you'll have Adam follow
  up and ask for their email and phone.
- If asked about anything not related to the business, gently redirect.
- Never claim to process a payment in chat. Direct customers to /booking.html.
- Do not request or store credit-card numbers, SINs, or other sensitive PII.
"""


# ---- handler -------------------------------------------------------------

MAX_TURNS = 12  # cap conversation length passed to the model


def handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        from _shared import cors_preflight  # type: ignore[import-not-found]
        return cors_preflight()

    try:
        body = parse_json(event)
        msgs = body.get("messages")
        if not isinstance(msgs, list) or not msgs:
            return err(400, "messages must be a non-empty list of {role, content}.")

        # Validate + truncate.
        cleaned: list[dict] = []
        for m in msgs[-MAX_TURNS:]:
            role = m.get("role")
            content = m.get("content")
            if role not in ("user", "assistant"):
                continue
            if not isinstance(content, str) or not content.strip():
                continue
            cleaned.append({"role": role, "content": content[:2000]})
        if not cleaned or cleaned[-1]["role"] != "user":
            return err(400, "the last message must be from the user.")

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return err(503, "Chat agent is offline — server is missing ANTHROPIC_API_KEY.")

        # Lazy import so cold-start cost only hits the first chat message.
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        # System prompt is cached so the catalog + facts live on the cache layer
        # after the first call (cheap & fast on every subsequent message).
        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=400,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                },
            ],
            messages=cleaned,
        )
        reply_parts = [b.text for b in message.content if getattr(b, "type", None) == "text"]
        reply = "".join(reply_parts).strip() or "Sorry — I didn't catch that. Could you rephrase?"

        return ok({
            "reply": reply,
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
            "cache_read_tokens": getattr(message.usage, "cache_read_input_tokens", 0) or 0,
            "cache_creation_tokens": getattr(message.usage, "cache_creation_input_tokens", 0) or 0,
        })
    except Exception as exc:  # pragma: no cover - error path
        import traceback
        traceback.print_exc()
        return err(500, f"{type(exc).__name__}: {exc}")
