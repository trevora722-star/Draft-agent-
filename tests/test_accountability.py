"""Accountability loop tests with the LLM stubbed.

Verifies the agentic loop: it nudges only at-risk members who consented to
contact, escalates prolonged absence to a human, and logs everything.
"""

from datetime import date, timedelta

from npo_agent import fitness, llm
from npo_agent.agents import Accountability
from npo_agent.agents import accountability as acct_mod
from npo_agent.tenancy import create_tenant

TODAY = date(2026, 6, 8)


def _stub(text="Hey, missed you this week — come grab a quick session!"):
    return llm.LLMResponse(
        text=text, input_tokens=5, output_tokens=10,
        cache_read_tokens=0, cache_creation_tokens=0, stop_reason="end_turn",
    )


def _ago(n):
    return (TODAY - timedelta(days=n)).isoformat()


def test_sweep_nudges_only_at_risk_consenting_members(isolated_db, monkeypatch):
    tenant, _ = create_tenant("Gym A", persona="coach-calm")

    # Low risk, consents — should NOT be nudged.
    low = fitness.create_member(tenant, "Reg", target_visits_per_week=3, consent_contact=True)
    fitness.record_checkins(
        tenant, low.id, [_ago(d) for d in (0, 2, 4, 7, 9, 11, 14, 16, 18, 21, 23, 25)]
    )

    # Medium risk, consents — nudged.
    med = fitness.create_member(tenant, "Lou", target_visits_per_week=3, consent_contact=True)
    fitness.record_checkins(tenant, med.id, [_ago(d) for d in (11, 13, 15, 18)])

    # High risk (ghosted 25d), consents — nudged AND escalated.
    high = fitness.create_member(tenant, "Marcus", target_visits_per_week=3, consent_contact=True)
    fitness.record_checkins(tenant, high.id, [_ago(d) for d in (25, 27, 29)])

    # High risk, no consent — skipped (PIPEDA boundary).
    noconsent = fitness.create_member(tenant, "Sofia", consent_contact=False)

    captured = {}

    def fake_complete(**kwargs):
        captured.update(kwargs)
        return _stub()

    monkeypatch.setattr(acct_mod, "complete", fake_complete)

    result = Accountability(tenant).run_sweep(today=TODAY)

    assert result.assessed == 4
    assert result.nudged == 2  # med + high
    assert result.escalated == 1  # high (25d absence >= 21)
    assert result.skipped_no_consent == 1  # noconsent
    # Nudges were logged, and persona reached the model.
    assert len(fitness.list_nudges(tenant)) == 2
    assert "calm" in captured["persona"]
    assert "haiku" in captured["model"]


def test_sweep_send_false_does_not_log(isolated_db, monkeypatch):
    tenant, _ = create_tenant("Gym A")
    m = fitness.create_member(tenant, "Lou", target_visits_per_week=3, consent_contact=True)
    fitness.record_checkins(tenant, m.id, [_ago(d) for d in (12, 14, 16)])
    monkeypatch.setattr(acct_mod, "complete", lambda **k: _stub())

    result = Accountability(tenant).run_sweep(today=TODAY, send=False)
    assert result.nudged == 1
    assert fitness.list_nudges(tenant) == []  # preview only, nothing sent


def test_assess_delegates_to_domain(isolated_db):
    tenant, _ = create_tenant("Gym A")
    m = fitness.create_member(tenant, "Ghost")
    a = Accountability(tenant).assess(m, today=TODAY)
    assert a.band == "high"
    assert a.member_id == m.id
