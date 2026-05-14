"""POST /api/social — draft three social media post versions.

Returns three distinct drafts (short, medium, story-style) tuned to the
selected platform, theme, tone, and CTA. The trainer picks one, tweaks
it, and posts it herself.
"""

from __future__ import annotations

import json
import re

from _shared import (  # type: ignore[import-not-found]
    anthropic_client,
    err,
    extract_text,
    ok,
    parse_json,
    safe_handler,
)

SOCIAL_SYSTEM = """\
You are FDAF Consulting's social-media writer. FDAF is a certified female
personal trainer who comes to clients' homes in West Kelowna / Central
Okanagan, BC, to train couples and small groups. The brand voice is warm,
grounded, slightly self-aware, never hype-y or boot-camp-ish. Never use
words like "fitspo", "grind", "no excuses", or "transformation".

Given a topic and constraints, write THREE distinct post drafts:

1. SHORT — a tight, hook-led post (50–80 words). Good for Instagram.
2. MEDIUM — a balanced post (90–130 words) with one concrete example or
   "why it matters" beat.
3. STORY — a more personal/narrative-flavoured post (130–180 words) that
   sounds like she's talking to a friend.

Constraints:
- Match the requested tone, platform, and CTA exactly. If "No CTA", omit
  any sales line.
- If "Include hashtags": end with 3–6 lowercase, locally-relevant hashtags
  (e.g. #westkelowna #okanaganfitness #couplestraining). Otherwise no hashtags.
- If "Include light emoji": use 1–2 max, only where natural. Never spammy.
- If "Include strong hook": open with a single punchy line — a question,
  contrarian take, or vivid image. Otherwise lead with a normal first line.
- If "Include local mention": work West Kelowna / Okanagan / lake-life into
  one line naturally. Otherwise keep it neutral.
- Never invent client stories with names. "A couple I trained last spring…"
  is OK; "Jenny and Mike said…" is not.
- No medical claims. No before/after promises with timelines.
- No markdown formatting — these are social posts, write them as the
  trainer would post them.

Return ONLY valid JSON in this exact shape (no commentary, no code fences):

{
  "posts": [
    {"label": "Short hook", "text": "..."},
    {"label": "Medium", "text": "..."},
    {"label": "Story", "text": "..."}
  ]
}
"""


def _extract_json(text: str) -> dict | None:
    """Try to pull a JSON object out of the model's reply, even if it added
    stray prose or wrapped it in a code fence."""
    if not text:
        return None
    text = text.strip()

    # Try direct parse first.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strip a leading code fence and try again.
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # Last-ditch: locate the first { ... } block.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


@safe_handler
def handler(event, context):
    body = parse_json(event)

    topic = (body.get("topic") or "").strip()[:1000]
    if not topic:
        return err(400, "Topic is required.")

    platform = (body.get("platform") or "Instagram").strip()[:40]
    theme = (body.get("theme") or "Educational tip").strip()[:60]
    tone = (body.get("tone") or "Warm + encouraging").strip()[:60]
    cta = (body.get("cta") or "Book a free consult").strip()[:60]

    include = body.get("include") or []
    if isinstance(include, str):
        include = [include]
    include = {str(x).strip().lower() for x in include}

    user_msg = (
        f"Topic: {topic}\n"
        f"Platform: {platform}\n"
        f"Theme: {theme}\n"
        f"Tone: {tone}\n"
        f"CTA: {cta}\n"
        f"Include hashtags: {'yes' if 'hashtags' in include else 'no'}\n"
        f"Include light emoji: {'yes' if 'emoji' in include else 'no'}\n"
        f"Include strong hook: {'yes' if 'hook' in include else 'no'}\n"
        f"Include local mention: {'yes' if 'local' in include else 'no'}\n"
    )

    cli = anthropic_client()
    resp = cli.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1400,
        system=[
            {
                "type": "text",
                "text": SOCIAL_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_msg}],
    )
    text = extract_text(resp)
    data = _extract_json(text)

    if not data or "posts" not in data or not isinstance(data["posts"], list):
        # Graceful fallback: return the raw text as one post so the trainer
        # at least has something usable, instead of an opaque error.
        return ok({
            "posts": [
                {
                    "label": "Raw draft (couldn't parse as 3 versions)",
                    "text": text or "(empty response)",
                }
            ]
        })

    posts = []
    for p in data["posts"][:3]:
        if isinstance(p, dict) and isinstance(p.get("text"), str) and p["text"].strip():
            posts.append({
                "label": str(p.get("label", ""))[:60],
                "text": p["text"].strip(),
            })

    if not posts:
        return err(502, "The agent returned no usable drafts. Try again.")

    return ok({"posts": posts})
