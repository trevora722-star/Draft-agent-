"""POST /api/program — generate a multi-week in-home training program.

Designed to fit inside Netlify's 10s sync function ceiling on Haiku 4.5
by:
  - keeping the output compact (one detailed week + progression notes for
    weeks 2+, rather than spelling out every set of every week)
  - capping max_tokens to ~2000
  - using a tight, structured Markdown template
"""

from __future__ import annotations

from _shared import (  # type: ignore[import-not-found]
    anthropic_client,
    err,
    extract_text,
    ok,
    parse_json,
    safe_handler,
)

PROGRAM_SYSTEM = """\
You design safe, effective in-home training programs for FDAF Consulting,
an in-home personal training business in West Kelowna. The trainer is a
certified female personal trainer; her clients are couples and small groups
training in their own homes with limited equipment.

Output a complete program in **Markdown** with EXACTLY this structure:

# {Client name} — {N}-week in-home program

**At a glance:** one short paragraph (40–80 words) describing the program's
focus, why it suits the client's goals, and how the four weeks build.

## Coaching principles
- 3–5 short bullets the client should remember (form cues, rest, RPE, etc.)

## Week 1 (detailed)
For each session day (number of sessions = the requested frequency):

### Day A — {focus}
- **Warm-up (5 min):** comma-separated list of 3–4 prep moves.
- **Main work:** 5–7 exercises, each as:
  `- **Exercise name** — sets × reps · brief cue or tempo (e.g. "3 × 8–10 · slow eccentric")`.
- **Finisher / cool-down (3–5 min):** 1–2 lines.

## Weeks 2–{N}: progression
- 4–6 bullets describing exactly how to progress week over week. Be specific
  (add reps, add weight, add a set, swap exercise X for variant Y, etc.).

## Notes for the client
- 3–5 bullets covering recovery, when to scale back, what "a good week"
  looks like, and what to text the trainer about.

Hard rules:
- **Respect injuries / contraindications.** Never prescribe a movement
  that conflicts with what the client listed.
- **Match equipment available.** If they only have bands + bodyweight,
  the program uses only those.
- **Match session length.** A 30-min session = 4–5 exercises, a 60-min
  session = 6–8.
- **Use beginner-friendly progressions** unless the level is intermediate
  or higher.
- No medical claims. No nutrition advice unless asked.
- Keep total output under 1500 words. Brevity over completeness.
"""


@safe_handler
def handler(event, context):
    body = parse_json(event)

    client = (body.get("client") or "").strip()[:120]
    goals = (body.get("goals") or "").strip()[:1000]
    if not client or not goals:
        return err(400, "Client name and goals are required.")

    frequency = int(body.get("frequency") or 3)
    weeks = int(body.get("weeks") or 4)
    session_len = int(body.get("session_len") or 45)
    level = (body.get("level") or "Returning beginner").strip()[:60]
    injuries = (body.get("injuries") or "").strip()[:1000] or "None reported"
    notes = (body.get("notes") or "").strip()[:1000]

    equipment = body.get("equipment") or []
    if isinstance(equipment, str):
        equipment = [equipment]
    equipment_str = ", ".join([str(e).strip() for e in equipment if str(e).strip()]) or "Bodyweight only"

    user_msg = (
        f"Design a {weeks}-week in-home program for **{client}**.\n\n"
        f"Sessions per week: {frequency}\n"
        f"Session length: {session_len} minutes\n"
        f"Starting level: {level}\n"
        f"Equipment available: {equipment_str}\n"
        f"Injuries / things to avoid: {injuries}\n"
        f"Primary goals: {goals}\n"
        f"Additional notes: {notes or '(none)'}\n"
    )

    cli = anthropic_client()
    resp = cli.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        system=[
            {
                "type": "text",
                "text": PROGRAM_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_msg}],
    )
    program = extract_text(resp)
    if not program:
        return err(502, "The agent returned no text. Try again.")

    return ok({"program": program})
