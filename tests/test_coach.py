"""Coach agent tests with the LLM stubbed. Verifies equipment-awareness, the
injury safety gate, persona threading, and model selection."""

import pytest

from npo_agent import fitness, llm
from npo_agent.agents import Coach
from npo_agent.agents import coach as coach_mod
from npo_agent.tenancy import create_tenant
from npo_agent.vault import Vault


def _stub(text: str) -> llm.LLMResponse:
    return llm.LLMResponse(
        text=text, input_tokens=10, output_tokens=20,
        cache_read_tokens=0, cache_creation_tokens=0, stop_reason="end_turn",
    )


def _gym(tenant, location_id):
    v = Vault(tenant)
    v.add(
        title="Equipment",
        body="adjustable dumbbells, squat racks, leg press, cable row, treadmills",
        namespace=fitness.location_namespace(location_id, "equipment"),
    )
    v.add(
        title="Squat pattern",
        body="back squat, goblet squat, leg press, walking lunge",
        namespace=fitness.EXERCISE_LIBRARY_NS,
    )


def test_build_program_is_equipment_aware_and_persists(isolated_db, monkeypatch):
    tenant, _ = create_tenant("Gym A", persona="coach-hype")
    loc = fitness.create_location(tenant, "Rutland")
    _gym(tenant, loc.id)
    member = fitness.create_member(
        tenant, "Alex Kim", home_location_id=loc.id, goals="build strength",
        experience="intermediate", target_visits_per_week=3,
    )

    captured = {}

    def fake_complete(**kwargs):
        captured.update(kwargs)
        return _stub("# Overview\nA 4-week plan.")

    monkeypatch.setattr(coach_mod, "complete", fake_complete)

    result = Coach(tenant).build_program(member)
    assert "4-week plan" in result.body
    # The location's equipment must reach the model.
    assert "leg press" in captured["user_content"]
    # Persona threads through.
    assert "high-energy" in captured["persona"]
    # Program builder uses adaptive thinking.
    assert captured["use_thinking"] is True
    # And it's persisted for the member.
    assert fitness.latest_program(tenant, member.id).body == result.body


def test_chat_injury_escalates_without_calling_the_model(isolated_db, monkeypatch):
    tenant, _ = create_tenant("Gym A")
    loc = fitness.create_location(tenant, "Rutland")
    member = fitness.create_member(tenant, "Alex", home_location_id=loc.id)

    def boom(**kwargs):
        raise AssertionError("LLM must not be called when injury is reported")

    monkeypatch.setattr(coach_mod, "complete", boom)

    reply = Coach(tenant).chat(member, "sharp pain in my knee when I squat")
    assert reply.escalated is True
    assert reply.used_llm is False
    assert "physiotherapist" in reply.text.lower()
    # A human-follow-up escalation was recorded.
    escs = fitness.list_escalations(tenant, unresolved_only=True)
    assert len(escs) == 1
    assert escs[0]["reason"] == "member_reported_pain"


def test_chat_normal_question_uses_fast_model(isolated_db, monkeypatch):
    tenant, _ = create_tenant("Gym A")
    loc = fitness.create_location(tenant, "Rutland")
    _gym(tenant, loc.id)
    member = fitness.create_member(tenant, "Alex", home_location_id=loc.id)

    captured = {}

    def fake_complete(**kwargs):
        captured.update(kwargs)
        return _stub("Try goblet squats with dumbbells.")

    monkeypatch.setattr(coach_mod, "complete", fake_complete)

    reply = Coach(tenant).chat(member, "the squat rack is taken, what do I do?")
    assert reply.escalated is False
    assert reply.used_llm is True
    assert "haiku" in captured["model"]
    assert captured["use_thinking"] is False


def test_substitute_uses_fast_model(isolated_db, monkeypatch):
    tenant, _ = create_tenant("Gym A")
    loc = fitness.create_location(tenant, "Rutland")
    _gym(tenant, loc.id)
    member = fitness.create_member(tenant, "Alex", home_location_id=loc.id)

    captured = {}
    monkeypatch.setattr(
        coach_mod, "complete",
        lambda **k: (captured.update(k), _stub("Use the leg press."))[1],
    )

    reply = Coach(tenant).substitute(member, "hack squat")
    assert "leg press" in reply.text.lower()
    assert "haiku" in captured["model"]
