# recovery_tracker

A local, interactive CLI: daily self-accountability dashboard and impulse
circuit-breaker for managing executive dysfunction, TBI recovery symptoms,
and ADHD. Built with Python 3.11+ and [rich](https://github.com/Textualize/rich).

All data stays on your machine, in JSON files under `recovery_tracker/data/`
(auto-created on first run, and git-ignored).

## Run

```bash
cd recovery_tracker
python app.py
```

Missing dependencies (`rich`, `pydantic`, and optionally `anthropic`) are
installed automatically on first run.

For demos/testing, shorten the 10-minute countdown to ~15 seconds:

```bash
RECOVERY_TRACKER_FAST=1 python app.py
```

## Features

1. **Urge circuit-breaker** — a 10-minute live countdown with three sequential
   grounding steps (physical state check, environment change, reflection).
   Each incident (timestamp, trigger, emotional state, whether the impulse
   passed) is logged to `data/incidents.json`.
2. **Daily check-in** — brain fog and stress scores (1–10), digital-boundary
   adherence, impulse occurrences, and healthy dopamine activities, appended
   to `data/daily_logs.json`.
3. **Communication partner** — reviews a draft message to your spouse against
   four criteria (accountability, no defensiveness, empathy, actionability)
   and suggests improvements. With `ANTHROPIC_API_KEY` set, an optional
   AI-assisted review is available.
4. **Clinical report generator** — writes `data/report_YYYY-MM-DD.md`
   summarizing 7- or 30-day trends (average fog/stress, impulse frequency
   and triggers, guardrail compliance) for sharing with a doctor or
   psychologist.

## Tests

```bash
cd recovery_tracker
python -m pytest test_app.py -v
```
