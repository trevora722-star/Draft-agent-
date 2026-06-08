"""Domain-layer tests for FitCoach: risk scoring, injury gate, tenant isolation.

No LLM calls here — this is the deterministic core the agents build on.
"""

from datetime import date, timedelta

from npo_agent import fitness
from npo_agent.tenancy import create_tenant

TODAY = date(2026, 6, 8)


def _iso_days_ago(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


def test_consistent_member_is_low_risk(isolated_db):
    tenant, _ = create_tenant("Gym A", persona="coach-hype")
    m = fitness.create_member(tenant, "Regular Reg", target_visits_per_week=3)
    # 3x/week for the last 4 weeks.
    offsets = [base + i * 2 for base in range(0, 28, 7) for i in range(3)]
    fitness.record_checkins(tenant, m.id, [_iso_days_ago(d) for d in offsets])

    a = fitness.assess(tenant, m, today=TODAY)
    assert a.band == "low"
    assert a.days_since_last is not None and a.days_since_last <= 2
    assert a.visits_last_28 >= 10


def test_never_visited_is_high_risk(isolated_db):
    tenant, _ = create_tenant("Gym A")
    m = fitness.create_member(tenant, "Ghost Member")
    a = fitness.assess(tenant, m, today=TODAY)
    assert a.band == "high"
    assert a.days_since_last is None
    assert a.score >= fitness.HIGH_BAND
    assert any("No check-ins" in r for r in a.reasons)


def test_lapsed_member_is_medium_or_high(isolated_db):
    tenant, _ = create_tenant("Gym A")
    m = fitness.create_member(tenant, "Lapsing Lou", target_visits_per_week=3)
    # Was regular three weeks ago, nothing in the last ~12 days.
    fitness.record_checkins(tenant, m.id, [_iso_days_ago(d) for d in (12, 14, 16, 19, 21)])
    a = fitness.assess(tenant, m, today=TODAY)
    assert a.band in ("medium", "high")
    assert a.visits_last_7 == 0


def test_score_is_bounded(isolated_db):
    tenant, _ = create_tenant("Gym A")
    m = fitness.create_member(tenant, "X")
    a = fitness.assess(tenant, m, today=TODAY)
    assert 0.0 <= a.score <= 1.0


def test_detect_injury_flags_pain_language():
    assert fitness.detect_injury("my knee really hurts after squats").detected
    assert fitness.detect_injury("sharp pain in my lower back").detected
    assert "pain" in fitness.detect_injury("chest pain when I run").terms
    assert not fitness.detect_injury("what should I do for leg day?").detected
    assert not fitness.detect_injury("").detected


def test_members_and_checkins_are_tenant_isolated(isolated_db):
    a_tenant, _ = create_tenant("Gym A")
    b_tenant, _ = create_tenant("Gym B")
    ma = fitness.create_member(a_tenant, "Alice")
    fitness.create_member(b_tenant, "Bob")

    fitness.record_checkin(a_tenant, ma.id, ts=_iso_days_ago(1))

    assert [m.name for m in fitness.list_members(a_tenant)] == ["Alice"]
    assert [m.name for m in fitness.list_members(b_tenant)] == ["Bob"]
    # B cannot see A's member or check-ins.
    assert fitness.get_member(b_tenant, ma.id) is None
    assert fitness.list_checkin_dates(b_tenant, ma.id) == []


def test_escalations_and_nudges_roundtrip(isolated_db):
    tenant, _ = create_tenant("Gym A")
    m = fitness.create_member(tenant, "Alice")
    fitness.create_escalation(tenant, m.id, reason="member_reported_pain", detail="knee")
    fitness.log_nudge(tenant, m.id, body="come back!", risk_at_send=0.7)

    assert len(fitness.list_escalations(tenant, unresolved_only=True)) == 1
    assert fitness.list_nudges(tenant)[0]["body"] == "come back!"


def test_location_namespace_is_scoped():
    assert fitness.location_namespace("abc", "equipment") == "loc:abc:equipment"
