"""POST /api/intake — process a new-client intake submission.

Takes the raw intake answers from intake.html and asks Claude to produce
a one-page brief the trainer can read before the first session. Then
hands the brief + raw answers off via the configured notification channels.
"""

from __future__ import annotations

import json
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

BRIEFER_SYSTEM = """\
You are preparing a one-page session brief for an in-home personal trainer
(FDAF Consulting, West Kelowna). The trainer will read this on her phone in
the car before walking into the client's home, so it needs to be tight,
practical, and well-organized.

Output format — plain text, exactly these sections in this order:

CLIENT
- Name, age, neighbourhood, who else is in the session.

WHY THEY'RE HERE
- 2–3 sentences in plain language describing goals, life context, and what
  "winning" looks like for them.

SAFETY FLAGS
- Anything from the PAR-Q screening that's a YES.
- Pain, injuries, surgeries, pregnancy/postpartum, medications.
- If clearance from a physician/physio is advised before the first session,
  say so explicitly with the words "PHYSICIAN CLEARANCE RECOMMENDED" so it
  stands out. Use this when there's a YES on heart/chest-pain/supervised
  questions, or active pain that hasn't been cleared.

PROGRAMMING NOTES
- Concrete things to do and avoid in the first session, given their
  starting level, history, and goals.
- Equipment they already have (so the trainer knows what to bring).
- Space they'll train in.

LOGISTICS
- Frequency they want.
- Best times of day.
- Email + phone.

FIRST-SESSION SUGGESTION
- A 2–3 line plan for the first 60 minutes: warm-up theme, main movements,
  and what to assess.

Hard constraints:
- Do not invent facts. If a field is blank, write "Not provided" rather
  than guessing.
- No markdown headers, no asterisks, no bullets beyond simple dashes.
- Keep the whole brief under 350 words.
- Never give medical advice. For anything ambiguous, flag for the trainer
  to ask the client directly.
"""


def _normalize(form: dict) -> dict:
    """Coerce intake form fields into a clean dict with predictable keys."""
    clean = {}
    for k, v in form.items():
        if isinstance(v, list):
            clean[k] = [str(x).strip() for x in v if str(x).strip()]
        else:
            clean[k] = str(v or "").strip()
    return clean


def _format_intake(data: dict) -> str:
    """Turn the form payload into a human-readable block for the LLM."""

    def g(k: str, default: str = "Not provided") -> str:
        v = data.get(k)
        if not v:
            return default
        if isinstance(v, list):
            return ", ".join(v) if v else default
        return v

    parq = []
    parq_map = {
        "par_heart": "Heart condition / high BP",
        "par_chest": "Chest pain / dizziness with activity",
        "par_joint": "Bone/joint/muscle issue worsened by exercise",
        "par_preg": "Currently pregnant or <12mo postpartum",
        "par_meds": "On medication affecting exercise",
        "par_supervised": "Doctor said exercise must be supervised",
    }
    for key, label in parq_map.items():
        answer = data.get(key, "no")
        parq.append(f"  - {label}: {answer.upper()}")

    return (
        f"NAME: {g('name')}\n"
        f"EMAIL: {g('email')}\n"
        f"PHONE: {g('phone')}\n"
        f"AGE: {g('age')}\n"
        f"NEIGHBOURHOOD: {g('neighbourhood')}\n"
        f"WHO'S IN THE GROUP: {g('group')}\n"
        f"\n"
        f"PAR-Q SCREENING:\n"
        + "\n".join(parq) + "\n"
        f"INJURIES / PAIN: {g('injuries')}\n"
        f"MEDS / CONDITIONS: {g('meds')}\n"
        f"\n"
        f"ACTIVITY LEVEL: {g('activity')}\n"
        f"TRAINING HISTORY: {g('history')}\n"
        f"SLEEP: {g('sleep')}\n"
        f"STRESS: {g('stress')}\n"
        f"\n"
        f"GOALS: {g('goals')}\n"
        f"SUCCESS LOOKS LIKE: {g('success')}\n"
        f"WHAT'S WORKED / NOT WORKED: {g('tried')}\n"
        f"\n"
        f"SPACE: {g('space')}\n"
        f"EQUIPMENT ON HAND: {g('equipment')}\n"
        f"DESIRED FREQUENCY: {g('frequency')}\n"
        f"BEST TIMES: {g('times')}\n"
    )


@safe_handler
def handler(event, context):
    body = parse_json(event)
    data = _normalize(body)

    if not data.get("name") or not data.get("email"):
        return err(400, "Name and email are required.")
    if not EMAIL_RE.match(data["email"]):
        return err(400, "That email address looks off — can you double-check it?")
    if not data.get("goals"):
        return err(400, "Tell me a little about your goals.")

    raw_block = _format_intake(data)

    client = anthropic_client()
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1200,
        system=[
            {
                "type": "text",
                "text": BRIEFER_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    "Here's a new client intake. Produce the one-page session "
                    "brief.\n\n" + raw_block
                ),
            }
        ],
    )
    brief = extract_text(resp) or "(Briefer returned no text — see raw intake below.)"

    subject = f"New intake: {data['name']}"
    notify_body = (
        f"New client intake from the FDAF Consulting site.\n\n"
        f"------ Session brief ------\n\n"
        f"{brief}\n\n"
        f"------ Raw intake answers ------\n\n"
        f"{raw_block}"
    )
    notify(subject, notify_body, payload={
        "name": data["name"],
        "email": data["email"],
        "brief": brief,
        "intake": data,
    })

    return ok({
        "ok": True,
        "message": "Thanks! Your intake is in — she'll follow up within one business day.",
    })
