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

from .agents import GrantWriter, PolicyNavigator
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
