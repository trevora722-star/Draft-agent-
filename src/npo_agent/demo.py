"""Demo mode — pre-seeded BCSS tenant + endpoints the CEO-facing HTML page calls.

Disabled in production (config.demo_mode = False). Enabled for laptop walkthroughs
and one-link Render deploys via NPO_DEMO_MODE=1.

The demo tenant is seeded once on startup (if missing) with realistic BCSS-flavored
program and policy docs. Demo endpoints look up that tenant by name and use it
internally — the browser never handles an API key.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from .agents import GrantWriter, PolicyNavigator
from .config import get_settings
from .db import connect
from .privacy import scrub
from .tenancy import Tenant, create_tenant, get_tenant
from .vault import Vault

DEMO_PROGRAM_DOCS = [
    (
        "24/7 Crisis Line",
        "BCSS operates a province-wide 24/7 crisis line for individuals and "
        "families affected by serious mental illness. We answer ~3,000 calls "
        "per year. Volunteer responders complete 60 hours of training including "
        "WHMIS, suicide-risk screening, and trauma-informed de-escalation. "
        "Average call duration is 18 minutes. Outcomes are tracked via a "
        "post-call disposition code: in 2024, 22% of callers reported active "
        "risk on first contact, dropping to 4% on follow-up.",
    ),
    (
        "Family Support Groups",
        "Eight peer-led family support groups meet weekly across the Lower "
        "Mainland and Fraser Valley. Each group is co-facilitated by a trained "
        "family member and a clinical coordinator. ~400 family members attend "
        "annually. 84% of 12-week participants report reduced caregiver burden "
        "on the UCLA Loneliness Scale, and 78% report improved knowledge of "
        "the BC mental health system.",
    ),
    (
        "Annual Outcomes 2024",
        "BCSS served 1,847 unique families in fiscal year 2024 across crisis "
        "line, support groups, and one-on-one navigation. Total operating "
        "budget was $4.2M, of which 72% came from BC Gaming, BC Ministry of "
        "Mental Health and Addictions, and individual donors. We employ 23 "
        "FTE staff and engage 142 active volunteers.",
    ),
    (
        "Rural BC Outreach Program",
        "BCSS launched a rural outreach pilot in 2023 serving the Cariboo, "
        "Kootenay, and Northern Health regions. Delivered via tele-counselling "
        "and quarterly in-person visits, the program currently reaches 180 "
        "families in communities under 25,000 population. Wait times for "
        "first contact average 4 days vs. 18 days for non-served regions.",
    ),
]

DEMO_POLICY_DOCS = [
    (
        "Volunteer Onboarding Policy",
        "All new BCSS volunteers must complete WHMIS, Privacy Awareness "
        "(PIPA), and Crisis De-escalation training within 30 days of their "
        "start date. Background checks (Criminal Record Review) are required "
        "before any direct client contact and renewed every 3 years. "
        "Volunteers under 19 require parental consent and may not staff the "
        "crisis line.",
    ),
    (
        "Privacy & PIPA Compliance Manual",
        "Client personally-identifiable information (names, contact details, "
        "health information) must be stored only in the encrypted case "
        "management system. Email containing PII must use encrypted delivery. "
        "Any suspected privacy breach must be reported to the Privacy Officer "
        "within 24 hours. BCSS is bound by BC's Personal Information "
        "Protection Act (PIPA) and, for partners receiving public funding, "
        "the Freedom of Information and Protection of Privacy Act (FOIPPA).",
    ),
    (
        "BC Societies Act Reporting",
        "Annual reports to the BC Registrar of Societies are due within 60 "
        "days of the AGM. Director changes (appointments, resignations) must "
        "be filed within 15 days. The annual financial statement must be "
        "approved by the membership at the AGM and submitted with the "
        "annual report.",
    ),
    (
        "Conflict of Interest Policy",
        "Directors, staff, and volunteers must disclose any actual, potential, "
        "or perceived conflicts of interest in writing to the Executive "
        "Director (for staff/volunteers) or Board Chair (for directors). "
        "Disclosed conflicts are reviewed by the Governance Committee. "
        "Individuals must recuse themselves from decisions where they have "
        "a material conflict.",
    ),
]


def ensure_demo_tenant() -> Tenant:
    """Create the demo tenant if missing and seed it with BCSS-flavored docs.

    Idempotent — safe to call on every cold start.
    """
    settings = get_settings()
    name = settings.demo_tenant_name

    with connect() as conn:
        row = conn.execute(
            "SELECT id, name, persona FROM tenants WHERE name = ?", (name,)
        ).fetchone()
    if row is not None:
        return get_tenant(row["id"])  # type: ignore[return-value]

    tenant, _ = create_tenant(name=name, persona="clinical-empathetic")
    vault = Vault(tenant)
    for title, body in DEMO_PROGRAM_DOCS:
        vault.add(title=title, body=body, namespace="programs")
    for title, body in DEMO_POLICY_DOCS:
        vault.add(title=title, body=body, namespace="policies")
    return tenant


def _demo_tenant() -> Tenant:
    settings = get_settings()
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM tenants WHERE name = ?",
            (settings.demo_tenant_name,),
        ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Demo tenant not seeded. Restart the server with NPO_DEMO_MODE=1.",
        )
    tenant = get_tenant(row["id"])
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Demo tenant unavailable"
        )
    return tenant


# ---- request / response models -------------------------------------------


class DemoInfo(BaseModel):
    tenant_name: str
    persona: str
    program_doc_count: int
    policy_doc_count: int
    model_default: str
    data_residency: str


class ScrubRequest(BaseModel):
    text: str


class ScrubResponse(BaseModel):
    scrubbed: str
    placeholder_count: int
    placeholder_types: list[str]


class DraftRequest(BaseModel):
    funder: str = "BC Gaming Community Grants"
    opportunity_title: str = "Community Gaming Grant 2025"
    funder_brief: str
    ask_amount: str | None = "$75,000"


class DraftResponse(BaseModel):
    draft_id: str
    body: str
    elapsed_seconds: float
    input_tokens: int
    output_tokens: int


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    answer: str
    citations: list[str]


# ---- router ---------------------------------------------------------------

router = APIRouter(prefix="/demo", tags=["demo"])


@router.get("/info", response_model=DemoInfo)
def demo_info() -> DemoInfo:
    settings = get_settings()
    tenant = _demo_tenant()
    vault = Vault(tenant)
    programs = vault.list(namespace="programs")
    policies = vault.list(namespace="policies")
    return DemoInfo(
        tenant_name=tenant.name,
        persona=tenant.persona,
        program_doc_count=len(programs),
        policy_doc_count=len(policies),
        model_default=settings.model_default,
        data_residency=settings.data_residency,
    )


@router.post("/preview-scrub", response_model=ScrubResponse)
def demo_preview_scrub(req: ScrubRequest) -> ScrubResponse:
    """Show exactly what the PII filter strips before any text reaches Claude.

    The CEO-demo killer feature: paste a brief with names/emails/phones and
    see the scrubbed version with placeholders. Nothing leaves the server here.
    """
    result = scrub(req.text)
    types = sorted({p.strip("[]").rsplit("_", 1)[0] for p in result.mapping.keys()})
    return ScrubResponse(
        scrubbed=result.text,
        placeholder_count=len(result.mapping),
        placeholder_types=types,
    )


@router.post("/draft", response_model=DraftResponse)
def demo_draft(req: DraftRequest) -> DraftResponse:
    import time

    tenant = _demo_tenant()
    agent = GrantWriter(tenant)
    started = time.monotonic()
    draft = agent.draft(
        funder=req.funder,
        opportunity_title=req.opportunity_title,
        funder_brief=req.funder_brief,
        ask_amount=req.ask_amount,
        max_tokens=8_000,  # demo-tuned: faster turnaround, still produces full sections
    )
    elapsed = time.monotonic() - started
    return DraftResponse(
        draft_id=draft.id,
        body=draft.body,
        elapsed_seconds=round(elapsed, 1),
        input_tokens=draft.usage.input_tokens,
        output_tokens=draft.usage.output_tokens,
    )


@router.post("/ask", response_model=AskResponse)
def demo_ask(req: AskRequest) -> AskResponse:
    tenant = _demo_tenant()
    answer = PolicyNavigator(tenant).ask(req.question)
    return AskResponse(answer=answer.answer, citations=answer.citations)
