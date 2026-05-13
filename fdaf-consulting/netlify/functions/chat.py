"""POST /api/chat — FDAF Consulting site chatbot.

A small intake assistant for FDAF Consulting's website. Runs on Claude
Haiku 4.5 so it returns inside Netlify's 10s sync-function ceiling and
stays cheap per conversation.

Request body
------------
{
  "messages": [
    {"role": "user" | "assistant", "content": "..."},
    ...
  ]
}

Response
--------
{ "reply": "..." }

Set ANTHROPIC_API_KEY in Netlify → Site settings → Environment variables.
"""

from __future__ import annotations

import json
import os
import traceback

import anthropic

# ---- Knowledge baked into the assistant ----------------------------------
# Everything below comes straight from the website copy. If a fact isn't here
# (e.g. exact scheduling, certifications, custom prices), the assistant is
# told to defer to the free consult instead of inventing.
SYSTEM_PROMPT = """\
You are the friendly website assistant for **FDAF Consulting**, an in-home
personal training business in West Kelowna, BC. You help visitors learn what
FDAF offers and, when they're ready, route them to the free 20-minute consult.

## The trainer
- Certified female personal trainer, fully insured
- Based in West Kelowna; serves West Kelowna, Kelowna, Peachland, Lake Country
  and the rest of the Central Okanagan
- Continuing education in pre/postnatal and small-group programming
- Beginner-friendly; works with 50+, pre/postnatal, and clients returning
  from injury (coordinates with physio/physician for complex cases)

## What FDAF offers
- **Couples training** — 60 min, in your home. From $95/session. Same workout,
  individualized intensity for each partner.
- **Small-group sessions** — 60 min, 3–6 people in your space. From $40/person.
  Great for friend groups, moms' groups, work crews, wedding parties.
- **In-home programming** — a follow-along plan for the weeks between visits.
  From $120/month, added on to any package. Includes check-ins.

## What every session includes
- A trainer who comes to you (no gym, no travel)
- All equipment provided (mats, bands, dumbbells, sliders, etc.)
- Workouts tailored to every body in the room, with form coaching
- A plan you can keep doing between visits

## Logistics
- Service area: West Kelowna + Central Okanagan. Outside that, a small
  travel fee may apply.
- Space needed: roughly living-room sized. Garages, decks, backyards are great.
- Payment: e-transfer or credit card.
- Cancellation: 24 hours' notice appreciated; same-day = 50% charge.
- Booking: every new client starts with a free 20-minute consult (phone or coffee).

## How to behave
1. **Be warm and concise.** Short paragraphs. Real, friendly tone — not corporate.
   Match the tone the visitor is using.
2. **Don't invent facts.** If you don't know (exact certification names, exact
   availability, custom pricing, anything not above), say so and steer toward
   the free consult or the contact form.
3. **Route to action.** When someone is interested, gently guide them to:
   - the **free 20-minute consult** (best),
   - or the contact form on the page,
   - or emailing **hello@fdafconsulting.ca**.
4. **Collect light intake when natural** — group size, goals, neighbourhood,
   any injuries — but don't interrogate. One question at a time.
5. **Stay on-topic.** If asked about unrelated things (taxes, recipes, etc.),
   redirect politely back to training.
6. **No medical advice.** For pain, rehab, or medical concerns, recommend
   they speak with their physician or physio. The trainer can adapt
   programming around a diagnosis but won't diagnose or treat.
7. **Keep replies short on mobile.** Aim for 2–4 sentences unless they
   asked something detailed. Use plain text (no markdown headers, no
   bullet symbols beyond simple dashes).

If someone asks to book, give them the next step: "I can pass this along to
the trainer — what's the best email or phone to reach you, and roughly when
works for a quick consult?" Then thank them and tell them she'll follow up
within one business day.
"""

# Greeting shown if the client sends an empty `messages` list.
GREETING = (
    "Hi! I'm the FDAF Consulting assistant. I can answer questions about "
    "in-home training for couples and small groups in the Central Okanagan, "
    "or help you book a free 20-minute consult. What can I help with?"
)

# CORS so the static page can call this from any origin.
_CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


def _ok(body: dict) -> dict:
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json", **_CORS},
        "body": json.dumps(body),
    }


def _err(status: int, message: str) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", **_CORS},
        "body": json.dumps({"error": message}),
    }


def _parse_body(event: dict) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64

        raw = base64.b64decode(raw).decode("utf-8")
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return {}


def _sanitize(messages: list) -> list[dict]:
    """Trim to the last 20 turns and drop anything that isn't a clean
    user/assistant pair. Keeps context small + predictable."""
    clean: list[dict] = []
    for m in messages[-20:]:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        content = content.strip()
        if not content:
            continue
        # Cap any single message — defense against pasted novels.
        clean.append({"role": role, "content": content[:4000]})
    # The Messages API requires the first message to be from the user.
    while clean and clean[0]["role"] != "user":
        clean.pop(0)
    return clean


def handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 204, "headers": _CORS, "body": ""}

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _err(
            500,
            "Server is missing ANTHROPIC_API_KEY. Set it in Netlify → "
            "Site settings → Environment variables and redeploy.",
        )

    body = _parse_body(event)
    raw_messages = body.get("messages") or []
    if not isinstance(raw_messages, list):
        return _err(400, "Body must include a 'messages' array.")

    messages = _sanitize(raw_messages)
    if not messages:
        # Fresh conversation — return the canned greeting without burning tokens.
        return _ok({"reply": GREETING})

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=messages,
        )
        # The response is a list of content blocks; concatenate the text ones.
        reply = "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        ).strip()
        if not reply:
            reply = (
                "Sorry — I didn't catch that. Could you say it another way? "
                "Or feel free to email hello@fdafconsulting.ca."
            )
        return _ok({"reply": reply})
    except anthropic.APIError as exc:
        traceback.print_exc()
        return _err(502, f"Upstream error: {exc}")
    except Exception as exc:  # pragma: no cover
        traceback.print_exc()
        return _err(500, f"{type(exc).__name__}: {exc}")
