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
        rows.append(f"- {meta['name']} ({sku}): ${meta['price']}/day")
    return "\n".join(rows)


SYSTEM_PROMPT = f"""You are the concierge chat agent for Squamish Adventure Rentals,
a locally-owned rental shop in downtown Squamish, BC, Canada (the gateway to
the Sea-to-Sky corridor between Vancouver and Whistler).

Voice: friendly, concise, locally knowledgeable. 2–4 short sentences typically.
Use plain text — no markdown headings or bullet lists in chat replies.

# Shop facts
- Address: 38123 Cleveland Ave, Squamish, BC
- Hours: 7 days/week, 8am–6pm year-round
- Phone: (604) 555-0144
- Free helmet, lock, paddle, and PFD with the relevant rental
- Same-day pickup available until 4pm
- Multi-day discount: 10% off for 3–6 days, 20% off for 7+ days
- 5% GST applies on top of the post-discount subtotal
- Free cancellation up to 24 hours before pickup
- Booking is online via the "Book now" button or /booking.html
- Payment is by card on the next step after the booking form
- We acknowledge we operate on the unceded territory of the Sḵwx̱wú7mesh Úxwumixw

# Rental catalog (per-day pricing)
{_catalog_block()}

# Local knowledge — share when asked
- Mountain biking: the Bench / Diamond Head zone (4 min drive) is the local hub;
  Half Nelson, Pseudo Tsuga, and Rupert are popular trails. Word-of-mouth picks
  for first-time Squamish riders: Half Nelson (blue, flowy), Credit Line (advanced).
- Paddling: Mamquam Blind Channel launch is a 10-minute walk from the shop and
  is the calmest local water — best for first-timers, kids, and SUP. Howe Sound
  proper has more chop and wind by mid-afternoon.
- Wind: Squamish is famous for its afternoon thermal — winds typically build
  after 11am in summer. Morning paddling = glassy water; afternoon = wind sport.
- Camping: Alice Lake Provincial Park (15 min north) and Paradise Valley
  (20 min north) are the closest car-camping options.

# Rules
- Never invent products, prices, hours, or policies that aren't listed above.
- If a customer asks something you can't answer (e.g. specific availability for
  a date, group bookings of >10), say you'll have a human follow up and ask
  for their email and phone.
- If the question is off-topic (politics, unrelated services), gently redirect.
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
