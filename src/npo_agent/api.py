"""FastAPI surface for the NPOAgent platform.

Routes:
  - /health                         (public)
  - /admin/tenants                  (admin token; create new NPO + persona + key)
  - /v1/documents                   (tenant API key; ingest into the vault)
  - /v1/agents/grant-writer/draft   (tenant API key; produce a grant draft)
  - /v1/agents/grant-writer/drafts  (list / fetch by id)
  - /v1/agents/policy-navigator/ask (tenant API key; RAG Q&A)
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import fitness
from .agents import Accountability, Coach, GrantWriter, PolicyNavigator
from .config import get_settings
from .db import init_db
from .personas import DEFAULT_PERSONA, PERSONAS
from .tenancy import Tenant, authenticate, create_tenant
from .vault import Vault

_STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="NPOAgent",
    description=(
        "Multi-tenant agent platform for nonprofits. Grant drafting, policy Q&A, "
        "donor concierge, and bookkeeping — sharing one engine, isolated by tenant."
    ),
    version="0.1.0",
)


if get_settings().demo_mode:
    # Demo mode wires up the BCSS-themed CEO walkthrough UI + endpoints.
    # Off by default; enable with NPO_DEMO_MODE=1.
    from . import demo as demo_module

    app.include_router(demo_module.router)
    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.on_event("startup")
def _startup() -> None:
    init_db()
    if get_settings().demo_mode:
        from . import demo as demo_module

        demo_module.ensure_demo_tenant()


@app.get("/", include_in_schema=False)
def root():
    """In demo mode, redirect to the demo UI; otherwise to the OpenAPI docs."""
    if get_settings().demo_mode:
        return RedirectResponse(url="/demo-ui")
    return RedirectResponse(url="/docs")


@app.get("/demo-ui", include_in_schema=False)
def demo_ui():
    if not get_settings().demo_mode:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Demo mode disabled. Set NPO_DEMO_MODE=1 to enable.",
        )
    return FileResponse(_STATIC_DIR / "demo.html")


# ---- FitCoach UIs (always available; the browser supplies the tenant key) ---


@app.get("/coach", include_in_schema=False)
def coach_ui():
    """Member-facing app: onboarding, program, and chat with the AI coach."""
    return FileResponse(_STATIC_DIR / "coach.html")


@app.get("/dashboard", include_in_schema=False)
def dashboard_ui():
    """Owner-facing dashboard: roster, churn risk, nudges, escalations."""
    return FileResponse(_STATIC_DIR / "dashboard.html")


# ---- auth dependencies ----------------------------------------------------


def require_tenant(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Tenant:
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
        )
    tenant = authenticate(x_api_key)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key"
        )
    return tenant


def require_admin(
    x_admin_token: Annotated[str | None, Header(alias="X-Admin-Token")] = None,
) -> None:
    expected = get_settings().admin_token
    if not x_admin_token or x_admin_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin token required",
        )


# ---- request / response models -------------------------------------------


class HealthResponse(BaseModel):
    status: str
    data_residency: str
    model_default: str
    available_personas: list[str]


class CreateTenantRequest(BaseModel):
    name: str
    persona: str = Field(default=DEFAULT_PERSONA)


class CreateTenantResponse(BaseModel):
    tenant_id: str
    name: str
    persona: str
    api_key: str
    note: str = (
        "Store this api_key now. It is not retrievable later — only its hash is kept."
    )


class IngestDocumentRequest(BaseModel):
    title: str
    body: str
    namespace: str = Field(
        default="default",
        description=(
            "Logical grouping inside the tenant's vault. Common values: "
            "'programs', 'policies', 'donors', 'past_grants'."
        ),
    )


class IngestDocumentResponse(BaseModel):
    document_id: str
    namespace: str


class GrantDraftRequest(BaseModel):
    funder: str
    opportunity_title: str
    funder_brief: str
    ask_amount: str | None = None
    program_focus: str | None = None
    max_tokens: int = 16_000


class GrantDraftResponse(BaseModel):
    draft_id: str
    funder: str
    title: str
    body: str
    metadata: dict


class PolicyQuestionRequest(BaseModel):
    question: str


class PolicyAnswerResponse(BaseModel):
    answer: str
    citations: list[str]


# ---- public ---------------------------------------------------------------


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        data_residency=settings.data_residency,
        model_default=settings.model_default,
        available_personas=list(PERSONAS.keys()),
    )


# ---- admin ----------------------------------------------------------------


@app.post(
    "/admin/tenants",
    response_model=CreateTenantResponse,
    dependencies=[Depends(require_admin)],
)
def admin_create_tenant(req: CreateTenantRequest) -> CreateTenantResponse:
    if req.persona not in PERSONAS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown persona '{req.persona}'. Choose from: {list(PERSONAS)}",
        )
    tenant, api_key = create_tenant(req.name, persona=req.persona)
    return CreateTenantResponse(
        tenant_id=tenant.id,
        name=tenant.name,
        persona=tenant.persona,
        api_key=api_key,
    )


# ---- tenant: documents ----------------------------------------------------


@app.post("/v1/documents", response_model=IngestDocumentResponse)
def ingest_document(
    req: IngestDocumentRequest,
    tenant: Annotated[Tenant, Depends(require_tenant)],
) -> IngestDocumentResponse:
    doc = Vault(tenant).add(title=req.title, body=req.body, namespace=req.namespace)
    return IngestDocumentResponse(document_id=doc.id, namespace=doc.namespace)


@app.get("/v1/documents")
def list_documents(
    tenant: Annotated[Tenant, Depends(require_tenant)],
    namespace: str | None = None,
) -> dict:
    docs = Vault(tenant).list(namespace=namespace)
    return {
        "tenant_id": tenant.id,
        "count": len(docs),
        "documents": [
            {"id": d.id, "namespace": d.namespace, "title": d.title, "snippet": d.snippet()}
            for d in docs
        ],
    }


# ---- tenant: grant writer -------------------------------------------------


@app.post("/v1/agents/grant-writer/draft", response_model=GrantDraftResponse)
def grant_writer_draft(
    req: GrantDraftRequest,
    tenant: Annotated[Tenant, Depends(require_tenant)],
) -> GrantDraftResponse:
    agent = GrantWriter(tenant)
    draft = agent.draft(
        funder=req.funder,
        opportunity_title=req.opportunity_title,
        funder_brief=req.funder_brief,
        ask_amount=req.ask_amount,
        program_focus=req.program_focus,
        max_tokens=req.max_tokens,
    )
    return GrantDraftResponse(
        draft_id=draft.id,
        funder=draft.funder,
        title=draft.title,
        body=draft.body,
        metadata=draft.metadata,
    )


@app.get("/v1/agents/grant-writer/drafts")
def grant_writer_list(
    tenant: Annotated[Tenant, Depends(require_tenant)],
) -> dict:
    return {"drafts": GrantWriter(tenant).list_drafts()}


@app.get("/v1/agents/grant-writer/drafts/{draft_id}")
def grant_writer_get(
    draft_id: str,
    tenant: Annotated[Tenant, Depends(require_tenant)],
) -> dict:
    draft = GrantWriter(tenant).get_draft(draft_id)
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")
    return draft


# ---- tenant: policy navigator --------------------------------------------


@app.post("/v1/agents/policy-navigator/ask", response_model=PolicyAnswerResponse)
def policy_navigator_ask(
    req: PolicyQuestionRequest,
    tenant: Annotated[Tenant, Depends(require_tenant)],
) -> PolicyAnswerResponse:
    answer = PolicyNavigator(tenant).ask(req.question)
    return PolicyAnswerResponse(answer=answer.answer, citations=answer.citations)


# ===========================================================================
# FitCoach — multi-location gym coaching + retention
# ===========================================================================
#
# The gym ownership group is the tenant (authenticated by X-API-Key). Its
# physical gyms are locations; members belong to a home location. Equipment /
# class / policy docs are ingested into a location-scoped vault namespace so the
# Coach only ever sees one gym's gear.


def _member_or_404(tenant: Tenant, member_id: str):
    member = fitness.get_member(tenant, member_id)
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    return member


# ---- request / response models -------------------------------------------


class CreateLocationRequest(BaseModel):
    name: str
    address: str | None = None
    timezone: str = "America/Vancouver"


class LocationResponse(BaseModel):
    id: str
    name: str
    address: str | None
    timezone: str


class LocationDocumentRequest(BaseModel):
    title: str
    body: str
    kind: str = Field(
        default="equipment",
        description="Location doc kind: 'equipment', 'classes', or 'policies'.",
    )


class CreateMemberRequest(BaseModel):
    name: str
    home_location_id: str | None = None
    email: str | None = None
    goals: str | None = None
    experience: str = "beginner"
    injuries: str | None = None
    constraints: str | None = None
    target_visits_per_week: int = 3
    consent_coaching: bool = True
    consent_contact: bool = False
    consent_retention: bool = True


class MemberResponse(BaseModel):
    id: str
    name: str
    home_location_id: str | None
    goals: str | None
    experience: str
    target_visits_per_week: int
    consent_contact: bool


class CheckinImportRequest(BaseModel):
    location_id: str | None = None
    timestamps: list[str] = Field(
        description="ISO datetimes or YYYY-MM-DD dates of visits to import."
    )


class ProgramRequest(BaseModel):
    member_id: str
    weeks: int = 4


class SubstituteRequest(BaseModel):
    member_id: str
    exercise: str


class ChatRequest(BaseModel):
    member_id: str
    message: str


class ChatResponse(BaseModel):
    text: str
    escalated: bool
    used_llm: bool
    citations: list[str]


# ---- locations ------------------------------------------------------------


@app.post("/v1/locations", response_model=LocationResponse)
def create_location(
    req: CreateLocationRequest,
    tenant: Annotated[Tenant, Depends(require_tenant)],
) -> LocationResponse:
    loc = fitness.create_location(
        tenant, req.name, address=req.address, timezone=req.timezone
    )
    return LocationResponse(id=loc.id, name=loc.name, address=loc.address, timezone=loc.timezone)


@app.get("/v1/locations")
def list_locations(tenant: Annotated[Tenant, Depends(require_tenant)]) -> dict:
    return {
        "locations": [
            {"id": l.id, "name": l.name, "address": l.address, "timezone": l.timezone}
            for l in fitness.list_locations(tenant)
        ]
    }


@app.post("/v1/locations/{location_id}/documents", response_model=IngestDocumentResponse)
def ingest_location_document(
    location_id: str,
    req: LocationDocumentRequest,
    tenant: Annotated[Tenant, Depends(require_tenant)],
) -> IngestDocumentResponse:
    if fitness.get_location(tenant, location_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")
    namespace = fitness.location_namespace(location_id, req.kind)
    doc = Vault(tenant).add(title=req.title, body=req.body, namespace=namespace)
    return IngestDocumentResponse(document_id=doc.id, namespace=doc.namespace)


# ---- members --------------------------------------------------------------


@app.post("/v1/members", response_model=MemberResponse)
def create_member(
    req: CreateMemberRequest,
    tenant: Annotated[Tenant, Depends(require_tenant)],
) -> MemberResponse:
    member = fitness.create_member(
        tenant,
        req.name,
        home_location_id=req.home_location_id,
        email=req.email,
        goals=req.goals,
        experience=req.experience,
        injuries=req.injuries,
        constraints=req.constraints,
        target_visits_per_week=req.target_visits_per_week,
        consent_coaching=req.consent_coaching,
        consent_contact=req.consent_contact,
        consent_retention=req.consent_retention,
    )
    return MemberResponse(
        id=member.id,
        name=member.name,
        home_location_id=member.home_location_id,
        goals=member.goals,
        experience=member.experience,
        target_visits_per_week=member.target_visits_per_week,
        consent_contact=member.consent_contact,
    )


@app.get("/v1/members")
def list_members(tenant: Annotated[Tenant, Depends(require_tenant)]) -> dict:
    return {
        "members": [
            {
                "id": m.id,
                "name": m.name,
                "home_location_id": m.home_location_id,
                "experience": m.experience,
                "consent_contact": m.consent_contact,
            }
            for m in fitness.list_members(tenant)
        ]
    }


@app.post("/v1/members/{member_id}/checkins")
def import_checkins(
    member_id: str,
    req: CheckinImportRequest,
    tenant: Annotated[Tenant, Depends(require_tenant)],
) -> dict:
    _member_or_404(tenant, member_id)
    count = fitness.record_checkins(
        tenant, member_id, req.timestamps, location_id=req.location_id
    )
    return {"member_id": member_id, "imported": count}


@app.get("/v1/members/{member_id}/program")
def get_member_program(
    member_id: str,
    tenant: Annotated[Tenant, Depends(require_tenant)],
) -> dict:
    _member_or_404(tenant, member_id)
    program = fitness.latest_program(tenant, member_id)
    if program is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No program yet")
    return {"id": program.id, "body": program.body, "created_at": program.created_at}


# ---- coach agent ----------------------------------------------------------


@app.post("/v1/agents/coach/program")
def coach_build_program(
    req: ProgramRequest,
    tenant: Annotated[Tenant, Depends(require_tenant)],
) -> dict:
    member = _member_or_404(tenant, req.member_id)
    result = Coach(tenant).build_program(member, weeks=req.weeks)
    return {
        "program_id": result.program_id,
        "body": result.body,
        "equipment_docs": result.equipment_docs,
    }


@app.post("/v1/agents/coach/substitute", response_model=ChatResponse)
def coach_substitute(
    req: SubstituteRequest,
    tenant: Annotated[Tenant, Depends(require_tenant)],
) -> ChatResponse:
    member = _member_or_404(tenant, req.member_id)
    reply = Coach(tenant).substitute(member, req.exercise)
    return ChatResponse(
        text=reply.text,
        escalated=reply.escalated,
        used_llm=reply.used_llm,
        citations=reply.citations,
    )


@app.post("/v1/agents/coach/chat", response_model=ChatResponse)
def coach_chat(
    req: ChatRequest,
    tenant: Annotated[Tenant, Depends(require_tenant)],
) -> ChatResponse:
    member = _member_or_404(tenant, req.member_id)
    reply = Coach(tenant).chat(member, req.message)
    return ChatResponse(
        text=reply.text,
        escalated=reply.escalated,
        used_llm=reply.used_llm,
        citations=reply.citations,
    )


# ---- accountability agent -------------------------------------------------


@app.get("/v1/agents/accountability/assess/{member_id}")
def accountability_assess(
    member_id: str,
    tenant: Annotated[Tenant, Depends(require_tenant)],
) -> dict:
    member = _member_or_404(tenant, member_id)
    a = Accountability(tenant).assess(member)
    return {
        "member_id": a.member_id,
        "score": a.score,
        "band": a.band,
        "days_since_last": a.days_since_last,
        "visits_last_7": a.visits_last_7,
        "visits_last_14": a.visits_last_14,
        "visits_last_28": a.visits_last_28,
        "target_visits_per_week": a.target_visits_per_week,
        "reasons": a.reasons,
    }


@app.post("/v1/agents/accountability/sweep")
def accountability_sweep(
    tenant: Annotated[Tenant, Depends(require_tenant)],
    send: bool = True,
) -> dict:
    result = Accountability(tenant).run_sweep(send=send)
    return {
        "assessed": result.assessed,
        "nudged": result.nudged,
        "escalated": result.escalated,
        "skipped_no_consent": result.skipped_no_consent,
        "nudges": [
            {"member_id": n.member_id, "band": n.band, "score": n.score, "message": n.message}
            for n in result.nudges
        ],
    }


# ---- owner dashboard ------------------------------------------------------


@app.get("/v1/dashboard")
def dashboard(tenant: Annotated[Tenant, Depends(require_tenant)]) -> dict:
    """Everything the owner-facing dashboard needs in one call."""
    members = fitness.list_members(tenant)
    acct = Accountability(tenant)
    roster = []
    bands = {"low": 0, "medium": 0, "high": 0}
    for m in members:
        a = acct.assess(m)
        bands[a.band] += 1
        roster.append(
            {
                "member_id": m.id,
                "name": m.name,
                "home_location_id": m.home_location_id,
                "band": a.band,
                "score": a.score,
                "days_since_last": a.days_since_last,
                "visits_last_28": a.visits_last_28,
                "consent_contact": m.consent_contact,
                "reasons": a.reasons,
            }
        )
    roster.sort(key=lambda r: -r["score"])
    return {
        "tenant": tenant.name,
        "member_count": len(members),
        "risk_bands": bands,
        "roster": roster,
        "recent_nudges": fitness.list_nudges(tenant, limit=25),
        "open_escalations": fitness.list_escalations(tenant, unresolved_only=True),
    }
