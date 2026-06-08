"""FitCoach domain layer — locations, members, check-ins, risk scoring.

This is the non-LLM core of the fitness product. It holds the data model and
the deterministic logic that the Coach and Accountability agents build on:

  * CRUD for locations and members, all tenant-scoped (an ownership group is a
    tenant; its physical gyms are locations).
  * The check-in feed — the signal the accountability loop runs on.
  * `assess()` — pure churn-risk scoring from attendance history. No LLM, cheap
    enough to run on a schedule across every member.
  * `detect_injury()` — a conservative keyword scan so the agents can enforce
    the hard safety rule: a member reporting pain is ALWAYS escalated to a human.

Equipment, class schedules, and policies are not stored here — they live in the
per-location vault namespace (see `location_namespace`) so the existing
tenant-scoped retriever gives us location-scoped retrieval for free.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable

from .db import connect
from .tenancy import Tenant

# ── vault namespace helpers ────────────────────────────────────────────────
# Equipment is per-location; the exercise library and policies are tenant-wide
# by default. Keeping these in one place means the Coach and the ingest API
# can never drift on how a namespace is spelled.


def location_namespace(location_id: str, kind: str) -> str:
    """Namespace for location-scoped vault docs, e.g. 'loc:abc123:equipment'."""
    return f"loc:{location_id}:{kind}"


EXERCISE_LIBRARY_NS = "exercises"


# ── dataclasses ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Location:
    id: str
    name: str
    address: str | None
    timezone: str


@dataclass(frozen=True)
class Member:
    id: str
    name: str
    email: str | None
    home_location_id: str | None
    goals: str | None
    experience: str
    injuries: str | None
    constraints: str | None
    target_visits_per_week: int
    consent_coaching: bool
    consent_contact: bool
    consent_retention: bool

    def profile_text(self) -> str:
        """Free-text profile handed to the Coach (scrubbed before the LLM)."""
        parts = [
            f"Name: {self.name}",
            f"Experience level: {self.experience}",
            f"Goals: {self.goals or 'general fitness'}",
            f"Target training days per week: {self.target_visits_per_week}",
            f"Injuries / conditions: {self.injuries or 'none reported'}",
            f"Constraints / preferences: {self.constraints or 'none reported'}",
        ]
        return "\n".join(parts)


@dataclass(frozen=True)
class RiskAssessment:
    member_id: str
    score: float  # 0.0 (engaged) → 1.0 (about to churn)
    band: str  # "low" | "medium" | "high"
    days_since_last: int | None
    visits_last_7: int
    visits_last_14: int
    visits_last_28: int
    target_visits_per_week: int
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Program:
    id: str
    member_id: str
    body: str
    created_at: str


# ── locations ──────────────────────────────────────────────────────────────


def create_location(
    tenant: Tenant,
    name: str,
    *,
    address: str | None = None,
    timezone: str = "America/Vancouver",
) -> Location:
    loc_id = uuid.uuid4().hex
    with connect() as conn:
        conn.execute(
            "INSERT INTO locations (id, tenant_id, name, address, timezone) "
            "VALUES (?, ?, ?, ?, ?)",
            (loc_id, tenant.id, name, address, timezone),
        )
    return Location(id=loc_id, name=name, address=address, timezone=timezone)


def list_locations(tenant: Tenant) -> list[Location]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, name, address, timezone FROM locations "
            "WHERE tenant_id = ? ORDER BY name",
            (tenant.id,),
        ).fetchall()
    return [
        Location(id=r["id"], name=r["name"], address=r["address"], timezone=r["timezone"])
        for r in rows
    ]


def get_location(tenant: Tenant, location_id: str) -> Location | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id, name, address, timezone FROM locations "
            "WHERE tenant_id = ? AND id = ?",
            (tenant.id, location_id),
        ).fetchone()
    if row is None:
        return None
    return Location(
        id=row["id"], name=row["name"], address=row["address"], timezone=row["timezone"]
    )


# ── members ────────────────────────────────────────────────────────────────


def _row_to_member(row) -> Member:
    return Member(
        id=row["id"],
        name=row["name"],
        email=row["email"],
        home_location_id=row["home_location_id"],
        goals=row["goals"],
        experience=row["experience"],
        injuries=row["injuries"],
        constraints=row["constraints"],
        target_visits_per_week=row["target_visits_per_week"],
        consent_coaching=bool(row["consent_coaching"]),
        consent_contact=bool(row["consent_contact"]),
        consent_retention=bool(row["consent_retention"]),
    )


def create_member(
    tenant: Tenant,
    name: str,
    *,
    home_location_id: str | None = None,
    email: str | None = None,
    goals: str | None = None,
    experience: str = "beginner",
    injuries: str | None = None,
    constraints: str | None = None,
    target_visits_per_week: int = 3,
    consent_coaching: bool = True,
    consent_contact: bool = False,
    consent_retention: bool = True,
) -> Member:
    member_id = uuid.uuid4().hex
    with connect() as conn:
        conn.execute(
            "INSERT INTO members (id, tenant_id, home_location_id, name, email, goals, "
            "experience, injuries, constraints, target_visits_per_week, "
            "consent_coaching, consent_contact, consent_retention) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                member_id, tenant.id, home_location_id, name, email, goals,
                experience, injuries, constraints, target_visits_per_week,
                int(consent_coaching), int(consent_contact), int(consent_retention),
            ),
        )
    return get_member(tenant, member_id)  # type: ignore[return-value]


def get_member(tenant: Tenant, member_id: str) -> Member | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM members WHERE tenant_id = ? AND id = ?",
            (tenant.id, member_id),
        ).fetchone()
    return _row_to_member(row) if row else None


def list_members(tenant: Tenant) -> list[Member]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM members WHERE tenant_id = ? ORDER BY name",
            (tenant.id,),
        ).fetchall()
    return [_row_to_member(r) for r in rows]


# ── check-ins (the accountability signal) ───────────────────────────────────


def _parse_ts(value: str) -> datetime:
    """Accept ISO datetime or plain YYYY-MM-DD."""
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.combine(date.fromisoformat(value), datetime.min.time())


def record_checkin(
    tenant: Tenant, member_id: str, *, location_id: str | None = None, ts: str | None = None
) -> None:
    """Record a single visit. `ts` defaults to now; accepts ISO or YYYY-MM-DD."""
    stamp = _parse_ts(ts).isoformat() if ts else datetime.now().isoformat()
    with connect() as conn:
        conn.execute(
            "INSERT INTO checkin_events (id, tenant_id, member_id, location_id, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, tenant.id, member_id, location_id, stamp),
        )


def record_checkins(
    tenant: Tenant, member_id: str, timestamps: Iterable[str], *, location_id: str | None = None
) -> int:
    """Bulk import (CSV pilot path). Returns the count inserted."""
    rows = [
        (uuid.uuid4().hex, tenant.id, member_id, location_id, _parse_ts(ts).isoformat())
        for ts in timestamps
    ]
    if not rows:
        return 0
    with connect() as conn:
        conn.executemany(
            "INSERT INTO checkin_events (id, tenant_id, member_id, location_id, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
    return len(rows)


def list_checkin_dates(tenant: Tenant, member_id: str) -> list[date]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT ts FROM checkin_events WHERE tenant_id = ? AND member_id = ? "
            "ORDER BY ts DESC",
            (tenant.id, member_id),
        ).fetchall()
    return [_parse_ts(r["ts"]).date() for r in rows]


# ── risk scoring (pure; the heart of the accountability loop) ───────────────

# Risk blends two signals:
#   recency  — how long since the member last showed up (a lapsing member is the
#              one we can still win back, so recency is weighted highest)
#   adherence — visits over the trailing 28 days vs. their own target cadence
_RECENCY_WEIGHT = 0.6
_ADHERENCE_WEIGHT = 0.4
_RECENCY_FULL_RISK_DAYS = 14  # no visit in 2 weeks → recency risk maxes out

HIGH_BAND = 0.66
MEDIUM_BAND = 0.40


def _band(score: float) -> str:
    if score >= HIGH_BAND:
        return "high"
    if score >= MEDIUM_BAND:
        return "medium"
    return "low"


def assess(tenant: Tenant, member: Member, *, today: date | None = None) -> RiskAssessment:
    """Score a member's churn risk from their attendance history. No LLM."""
    today = today or date.today()
    dates = list_checkin_dates(tenant, member.id)
    target = max(1, member.target_visits_per_week)

    visits_7 = sum(1 for d in dates if 0 <= (today - d).days < 7)
    visits_14 = sum(1 for d in dates if 0 <= (today - d).days < 14)
    visits_28 = sum(1 for d in dates if 0 <= (today - d).days < 28)
    days_since = min((today - d).days for d in dates) if dates else None

    reasons: list[str] = []

    # Recency component.
    if days_since is None:
        recency = 1.0
        reasons.append("No check-ins on record yet.")
    else:
        recency = min(1.0, days_since / _RECENCY_FULL_RISK_DAYS)
        if days_since >= _RECENCY_FULL_RISK_DAYS:
            reasons.append(f"Hasn't visited in {days_since} days.")
        elif days_since >= 7:
            reasons.append(f"Last visit was {days_since} days ago.")

    # Adherence component over the trailing 4 weeks vs. their own target.
    expected_28 = target * 4
    adherence = min(1.0, visits_28 / expected_28) if expected_28 else 0.0
    adherence_risk = 1.0 - adherence
    if adherence < 0.5:
        reasons.append(
            f"Only {visits_28} visits in the last 4 weeks vs. a target of ~{expected_28}."
        )

    score = round(_RECENCY_WEIGHT * recency + _ADHERENCE_WEIGHT * adherence_risk, 3)
    band = _band(score)
    if band == "low" and not reasons:
        reasons.append("On track with their target cadence.")

    return RiskAssessment(
        member_id=member.id,
        score=score,
        band=band,
        days_since_last=days_since,
        visits_last_7=visits_7,
        visits_last_14=visits_14,
        visits_last_28=visits_28,
        target_visits_per_week=target,
        reasons=reasons,
    )


# ── injury / pain detection (safety gate) ───────────────────────────────────

# Conservative — over-matching is the right failure mode here. Any hit routes
# the member to a human and blocks the agent from giving training advice on it.
_INJURY_TERMS = (
    "pain", "hurt", "hurts", "injury", "injured", "sharp", "swollen", "swelling",
    "sprain", "sprained", "strain", "strained", "pull", "pulled", "tear", "torn",
    "pop", "popped", "dizzy", "dizziness", "faint", "fainted", "chest pain",
    "can't breathe", "cant breathe", "numb", "numbness", "tingling", "fracture",
    "broken", "concussion", "blackout", "passed out",
)


@dataclass(frozen=True)
class InjurySignal:
    detected: bool
    terms: list[str]


def detect_injury(text: str) -> InjurySignal:
    """Flag possible pain/injury language in a member message."""
    lowered = (text or "").lower()
    hits = sorted({term for term in _INJURY_TERMS if term in lowered})
    return InjurySignal(detected=bool(hits), terms=hits)


# ── escalations & nudge log ─────────────────────────────────────────────────


def create_escalation(
    tenant: Tenant, member_id: str, *, reason: str, detail: str | None = None
) -> str:
    esc_id = uuid.uuid4().hex
    with connect() as conn:
        conn.execute(
            "INSERT INTO escalations (id, tenant_id, member_id, reason, detail) "
            "VALUES (?, ?, ?, ?, ?)",
            (esc_id, tenant.id, member_id, reason, detail),
        )
    return esc_id


def list_escalations(tenant: Tenant, *, unresolved_only: bool = False) -> list[dict]:
    query = (
        "SELECT id, member_id, reason, detail, resolved_by, created_at "
        "FROM escalations WHERE tenant_id = ?"
    )
    if unresolved_only:
        query += " AND resolved_by IS NULL"
    query += " ORDER BY created_at DESC"
    with connect() as conn:
        rows = conn.execute(query, (tenant.id,)).fetchall()
    return [dict(r) for r in rows]


def log_nudge(
    tenant: Tenant, member_id: str, *, body: str, risk_at_send: float, channel: str = "app"
) -> str:
    nudge_id = uuid.uuid4().hex
    with connect() as conn:
        conn.execute(
            "INSERT INTO nudge_log (id, tenant_id, member_id, channel, risk_at_send, body) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (nudge_id, tenant.id, member_id, channel, risk_at_send, body),
        )
    return nudge_id


def list_nudges(tenant: Tenant, *, limit: int = 50) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, member_id, channel, risk_at_send, body, created_at "
            "FROM nudge_log WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?",
            (tenant.id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ── programs ────────────────────────────────────────────────────────────────


def save_program(tenant: Tenant, member_id: str, body: str, metadata: str | None = None) -> Program:
    prog_id = uuid.uuid4().hex
    with connect() as conn:
        conn.execute(
            "INSERT INTO programs (id, tenant_id, member_id, body, metadata) "
            "VALUES (?, ?, ?, ?, ?)",
            (prog_id, tenant.id, member_id, body, metadata),
        )
        row = conn.execute(
            "SELECT created_at FROM programs WHERE id = ?", (prog_id,)
        ).fetchone()
    return Program(id=prog_id, member_id=member_id, body=body, created_at=row["created_at"])


def latest_program(tenant: Tenant, member_id: str) -> Program | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id, member_id, body, created_at FROM programs "
            "WHERE tenant_id = ? AND member_id = ? ORDER BY created_at DESC LIMIT 1",
            (tenant.id, member_id),
        ).fetchone()
    if row is None:
        return None
    return Program(
        id=row["id"], member_id=row["member_id"], body=row["body"], created_at=row["created_at"]
    )
