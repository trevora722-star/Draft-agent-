# FitCoach — AI coaching & retention platform for multi-location gyms

> White-label AI coaching the gym brands as its own and sells to members as a
> monthly add-on. Built on the existing multi-tenant agent harness in
> `src/npo_agent/` (renamed conceptually here; the engine is the same).
>
> Reference deployment: an Anytime Fitness ownership group operating
> **11 locations** in the Kelowna / Okanagan region.

---

## 1. The business: who pays whom

There are two distinct customers. Keeping them straight drives every other
decision in this doc.

| Party | Role | Pays | Gets |
|---|---|---|---|
| **Us (the platform)** | SaaS vendor | — | Per-location / per-active-member fee from the gym |
| **The gym ownership group** | Our customer | Pays us a SaaS fee | A white-label app + agent system they resell |
| **Gym members** | The gym's customers | Pay the gym a monthly add-on | An AI coach in their pocket |

The product is **white-label**: the gym brands it as their own ("Anytime
Coach"), sells it to members as a $15–25/month add-on, and keeps the margin.
We charge the gym, not the members.

### Why a gym owner buys this

1. **Ancillary revenue at high margin.** A human PT costs a member
   $60–100/session. An AI coaching add-on at $19/month is pure upside and
   consumes zero staff hours.
2. **Retention.** This is the real money. Gym churn is ~30–50%/year and almost
   entirely driven by members who *stop showing up*. The accountability agent
   exists to catch attendance decay early and re-engage. A 5-point churn
   improvement across 11 locations dwarfs the add-on revenue.
3. **Lead capture & upsell.** The coach can route members who plateau to a
   paid human PT session — the AI becomes a funnel into higher-margin services,
   not a replacement for them.

### Pricing model (what we charge the gym)

| Tier | Structure | Notes |
|---|---|---|
| Platform fee | Flat $/location/month | Covers the 11 sites; predictable |
| Active-member fee | $/member/month for members who opted into the add-on | Aligns our revenue with their add-on revenue |
| Setup / ingestion | One-time per ownership group | Load equipment lists, schedules, policies for all sites |

Suggested starting point: **$199/location/month + $2/active member/month.**
At 11 locations and ~150 opted-in members each, that's roughly
$2,200 + $3,300 = **~$5,500/month** to us, against the gym collecting
~$31,000/month in add-on fees (1,650 members × $19) — a margin story the
owner can see immediately, before counting retention savings.

---

## 2. The member-facing product

Headline: **a personal coach in your pocket, 24/7, that knows your gym.**

| # | Capability | Agent | Why it matters |
|---|---|---|---|
| 1 | Build & adapt a workout program from goals, experience, injuries, and the **equipment actually at your home location** | Coach | The member "wow" |
| 2 | In-gym live companion ("rack's taken, 35 min, what do I do?") → substitution from available equipment | Coach | Daily active use |
| 3 | Proactive check-ins, streaks, churn-risk nudges, plan re-scheduling | Accountability | The feature the *owner* pays for |
| 4 | Class & booking concierge (schedules, "next spin at Rutland?") | Concierge | Stickiness |
| 5 | Nutrition / habit guidance (scoped — see §6) | Coach | Completes the "coach" promise |
| 6 | Front-desk FAQ (hours, billing, freeze/cancel, guest passes) per location | Concierge | Deflects staff load |

MVP leads with **Coach + Accountability together** — the full agentic loop.
Coach generates and adapts the plan; Accountability keeps the member on it.

---

## 3. Architecture — mapping onto the existing harness

The existing platform (`src/npo_agent/`) already provides everything the engine
needs. The fitness product is a new set of agents + a new knowledge schema on
the same core.

```
┌──────────────────── shared core (already built) ────────────────────┐
│  Anthropic SDK · prompt caching · adaptive thinking (llm.complete)   │
│  PII / PHI scrubber (privacy.scrub)                                  │
│  Per-tenant Knowledge Vault (vault.Vault, namespaced retrieval)     │
│  Tenant + persona + API-key auth (tenancy, db, api)                 │
│  Agent base class (agents/base.Agent)                               │
└──────────────────────────────────────────────────────────────────────┘
        │                                  │
        ▼                                  ▼
┌── Coach agent ──┐                ┌── Accountability agent ──┐
│ program build / │                │ check-in loop, churn-    │
│ live substitute │◀──── shares ──▶│ risk scoring, nudges,    │
│                 │   member state │ escalation to human PT   │
└─────────────────┘                └──────────────────────────┘
```

### What maps to what

| Existing concept | Fitness equivalent | Change required |
|---|---|---|
| `Tenant` | One ownership group; **11 locations as sub-sites** | Add `location_id` dimension (see §4) |
| `tenant.persona` (`personas.py`) | Coaching voice: `hype`, `calm`, `clinical-rehab` | Add fitness personas |
| `Vault` + namespaces | `equipment`, `exercises`, `classes`, `policies` per location | New namespaces; same retriever |
| `Agent` base class | `Coach`, `Accountability`, `Concierge` subclass it | New agent files |
| `privacy.scrub` (PII) | Member PHI scrubbing before LLM (PIPEDA/BC PIPA) | Extend patterns (health terms optional) |
| `PolicyNavigator` refusal pattern | Medical-scope refusal + injury escalation | Reuse pattern, new rules |
| `complete(model=...)` | Opus for program design, Haiku for FAQ/nudges | Config already supports both |

### The agents (subclassing `agents/base.Agent`)

**Coach** — `agents/coach.py`
- `build_program(profile)` → retrieves `equipment` for the member's home
  location + `exercises`, runs Opus with adaptive thinking, returns a
  structured multi-week plan. Equipment-aware: never prescribes a machine the
  location doesn't have.
- `substitute(exercise, available)` → fast Haiku call for the in-gym "rack's
  taken" case.
- Scrubs the member profile (injuries, age, conditions) before it reaches the
  model; rehydrates nothing sensitive into stored plans.

**Accountability** — `agents/accountability.py`
- This is the genuinely *agentic* component: it has a **goal** (member attends
  N×/week), **observes state** (check-in events), and **takes actions**
  (nudge, reschedule plan via Coach, escalate to human PT).
- `assess(member)` → computes a churn-risk score from attendance trend +
  streak + last-seen. Pure logic, no LLM, cheap to run on a schedule.
- `compose_nudge(member, risk)` → Haiku, persona-voiced, references *their*
  plan and a concrete next session. Only fires above a risk threshold.
- `escalate(member, reason)` → flags a human (front-desk / PT) when risk is
  high or an injury/medical complaint is detected. **Never silently drops a
  member who reports pain.**

The loop is what justifies a *recurring* fee instead of a one-time app sale:
the system keeps working between member sessions.

**Concierge** — `agents/concierge.py` (post-MVP)
- Class schedules + policy FAQ, per location. Direct reuse of the
  `PolicyNavigator` pattern (RAG over the gym's own docs, cite the source,
  refuse off-topic).

---

## 4. Data model

Two new dimensions on top of the existing tenant/document tables.

```
ownership_group (tenant)
 └── location (id, name, address, timezone)        # 11 rows
      └── document (namespace, body)               # equipment/classes/policies
                                                     scoped to this location
member
 ├── home_location_id
 ├── profile: goals, experience, injuries, constraints (PHI — scrubbed pre-LLM)
 ├── consent flags (PIPEDA): coaching, proactive_contact, data_retention
 └── program (current plan, version history)

checkin_event (member_id, location_id, ts)         # the accountability signal
nudge_log (member_id, ts, channel, risk_at_send)   # audit + frequency caps
escalation (member_id, reason, ts, resolved_by)    # human handoff trail
```

**Location-scoped retrieval** is the key new constraint: when the Coach builds
a program, the Vault query must filter to the member's `home_location_id` so it
only sees that gym's equipment. Same isolation principle the platform already
enforces for tenants, one level deeper.

### Integrations (the unglamorous but decisive part)

The accountability loop is only as good as its attendance signal. Anytime
Fitness members badge in with a key fob → that door-access data is the
check-in stream. Options, in order of preference:

1. **Club management system API** (e.g. the gym's member-management/billing
   platform) — pull check-ins + roster. Cleanest.
2. **Manual / CSV** for a pilot — good enough to prove the retention story at
   one location.
3. **In-app self-check-in** — fallback; lossy because it depends on the member.

This is the first thing to validate with the ownership group — *no attendance
feed, no accountability product.*

---

## 5. MVP scope (Coach + Accountability, one location)

Goal: a demo to the ownership group that proves the retention story.

- [ ] One ownership-group tenant, **one** location seeded (equipment +
      a small exercise library + class schedule).
- [ ] Member onboarding: goals / experience / injuries / consent.
- [ ] **Coach**: `build_program` (Opus, equipment-aware) + `substitute`
      (Haiku, in-gym).
- [ ] **Accountability**: `assess` risk scoring on a CSV-imported check-in
      feed + `compose_nudge` + `escalate`.
- [ ] Member chat UI (reuse the `static/demo.html` pattern).
- [ ] Owner dashboard view: roster, risk scores, nudges sent, escalations —
      *this is the screen that sells the deal.*
- [ ] Tests that monkey-patch `llm.complete()` (no live API), mirroring the
      existing `tests/test_agent_smoke.py` approach: program is
      equipment-constrained, nudges fire only above threshold, PHI is scrubbed,
      injury reports always escalate.

Explicitly **out** of MVP: payments/billing, native mobile apps (web first),
all 11 locations, nutrition module, Concierge.

---

## 6. Two constraints that must be designed in, not bolted on

### Health advice in Canada
- Position as **fitness & wellness coaching**, not medical advice.
- Hard guardrails in the Coach/Accountability system prompts (reuse the
  `PolicyNavigator` refusal style): no diagnosis, no rehab prescription for
  acute injury, no medical claims.
- **Injury / pain → escalate to a human**, every time. This is both a safety
  rule and a feature (warm handoff to a paid PT).
- Onboarding medical-disclaimer + PAR-Q-style readiness screen; high-risk
  answers gate the member to "see a professional first."

### Member health data = PIPEDA + BC PIPA
- The platform already has the PII scrubber and a `ca-central` data-residency
  story — extend the scrubber to member profiles before any text hits the LLM.
- Explicit **consent flags** for coaching, proactive contact, and retention.
- Audit logging of who saw what (already on the platform hardening checklist).
- For production, route the Anthropic SDK through Bedrock `ca-central-1` /
  Vertex `northamerica-northeast1` per the existing residency design.

These aren't overhead — they're the slide that gets you past the ownership
group's lawyer, and a genuine differentiator vs. a generic fitness chatbot.

---

## 7. Build sequence

1. **Validate the attendance feed** with the ownership group (§4). Blocks
   everything; do it first.
2. Data model: add `location` + `member` + event tables; location-scoped
   vault retrieval.
3. Coach agent + equipment-aware program generation + tests.
4. Accountability loop (risk scoring → nudge → escalate) + tests.
5. Member chat UI + owner dashboard.
6. Pilot at **one** location; measure retention vs. a comparable location.
7. Roll out to all 11; add Concierge + nutrition.

---

## 8. Open questions for the ownership group

- What club-management / access-control system do the 11 locations use? (Drives
  the check-in integration — see §4.)
- Is there appetite for AI → human-PT upsell, or is the AI meant to stand
  alone? (Changes the escalation/funnel design.)
- White-label branding: one brand across all 11, or per-location?
- Target add-on price point to members? (Sets our active-member fee.)
- Who owns member data contractually — the ownership group or us? (PIPEDA
  accountability + the data-processing agreement.)
