"""Agent smoke tests with the LLM call stubbed out.

We don't want unit tests to hit Anthropic. We monkey-patch llm.complete()
to assert the agent passes the right context and returns a draft that
preserves rehydrated PII placeholders correctly.
"""

from npo_agent import llm
from npo_agent.agents import GrantWriter, PolicyNavigator
from npo_agent.tenancy import create_tenant
from npo_agent.vault import Vault


def _stub_response(text: str) -> llm.LLMResponse:
    return llm.LLMResponse(
        text=text,
        input_tokens=100,
        output_tokens=200,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        stop_reason="end_turn",
    )


def test_grant_writer_persists_and_returns_draft(isolated_db, monkeypatch):
    tenant, _ = create_tenant("BCSS", persona="clinical-empathetic")
    Vault(tenant).add(
        title="24/7 crisis line",
        body="Our crisis line serves 3,000 callers/year in BC.",
        namespace="programs",
    )

    captured: dict = {}

    def fake_complete(**kwargs):
        captured.update(kwargs)
        return _stub_response("# Executive Summary\nDrafted application body.")

    # grant_writer.draft imports `complete` into its module — patch the local symbol.
    from npo_agent.agents import grant_writer as gw
    monkeypatch.setattr(gw, "complete", fake_complete)

    agent = GrantWriter(tenant)
    draft = agent.draft(
        funder="BC Gaming",
        opportunity_title="Community Gaming Grant 2025",
        funder_brief="Supports mental health programs serving vulnerable BC residents.",
        ask_amount="$75,000",
        program_focus="crisis line",
    )

    assert draft.title == "Community Gaming Grant 2025"
    assert "Drafted application" in draft.body
    # The persona text must reach the LLM call.
    assert "clinical yet empathetic" in captured["persona"]
    # Retrieved program context must be in the user payload (content, not title —
    # the privacy filter conservatively scrubs proper-noun titles as potential names).
    assert "3,000 callers/year" in captured["user_content"]
    # Adaptive thinking should be on for grant writing.
    assert captured["use_thinking"] is True

    # Listing should return the persisted draft for this tenant only.
    assert len(agent.list_drafts()) == 1


def test_grant_writer_rehydrates_pii_in_output(isolated_db, monkeypatch):
    tenant, _ = create_tenant("BCSS")
    # Funder brief contains a contact email — it must be scrubbed before the
    # LLM call but rehydrated in the final stored draft.
    funder_brief = "Submit to program.officer@gov.bc.ca by March 1."

    def fake_complete(**kwargs):
        # Verify the LLM never sees the raw email.
        assert "program.officer@gov.bc.ca" not in kwargs["user_content"]
        assert "[EMAIL_1]" in kwargs["user_content"]
        # Echo the placeholder back the way a real model would.
        return _stub_response("Submit to [EMAIL_1] before the deadline.")

    from npo_agent.agents import grant_writer as gw
    monkeypatch.setattr(gw, "complete", fake_complete)

    draft = GrantWriter(tenant).draft(
        funder="BC Gov",
        opportunity_title="X",
        funder_brief=funder_brief,
    )
    # The stored draft has the email restored — placeholders never leak to the user.
    assert "program.officer@gov.bc.ca" in draft.body
    assert "[EMAIL_1]" not in draft.body


def test_policy_navigator_uses_haiku_and_no_thinking(isolated_db, monkeypatch):
    tenant, _ = create_tenant("BCSS")
    Vault(tenant).add(
        title="Volunteer Handbook",
        body="All volunteers must complete WHMIS within 30 days of start date.",
        namespace="policies",
    )

    captured: dict = {}

    def fake_complete(**kwargs):
        captured.update(kwargs)
        return _stub_response("Volunteers must complete WHMIS within 30 days. [Doc 1]")

    from npo_agent.agents import policy_navigator as pn
    monkeypatch.setattr(pn, "complete", fake_complete)

    answer = PolicyNavigator(tenant).ask("When do volunteers need WHMIS training?")
    assert "WHMIS" in answer.answer
    assert captured["use_thinking"] is False
    assert "haiku" in captured["model"]
    assert "Volunteer Handbook" in answer.citations
