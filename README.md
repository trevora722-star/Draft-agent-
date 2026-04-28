# NPOAgent — multi-tenant SaaS for nonprofits

A multi-tenant agent platform for Canadian nonprofits. One shared engine,
isolated tenant vaults, custom personas. The MVP ships with a **Grant
Scout & Drafter** module (the easiest agent to clear with a Board of
Directors) and a **Policy Navigator** module (RAG Q&A over the org's
own HR / Safety / compliance binder). Donor Concierge and Bookkeeper
plug into the same harness — see `src/npo_agent/agents/`.

## Architecture

```
┌──────────────────────── shared core ────────────────────────┐
│  Anthropic SDK · prompt caching · adaptive thinking         │
│  PII anonymizer (email, phone, SIN, PHN, postal, DOB, names)│
│  Per-tenant Knowledge Vault (TF-IDF; swap to voyage-3 prod) │
│  Tenant + persona + API-key authentication                  │
└──────────────────────────────────────────────────────────────┘
       │                                       │
       ▼                                       ▼
┌──── BCSS ────┐                     ┌── Food Bank ──┐
│ namespace:   │                     │ namespace:    │
│ tenant:abc   │                     │ tenant:def    │
│ persona:     │                     │ persona:      │
│ clinical-    │                     │ warm-         │
│ empathetic   │                     │ grassroots    │
└──────────────┘                     └───────────────┘
```

Tenant isolation is enforced at two layers:

1. **SQL boundary** — every query filters by `tenant_id`. There is no
   accessor that looks across tenants.
2. **Vault namespace** — the retriever is constructed per request from
   the tenant's documents only; it never sees another tenant's corpus.

The PII anonymizer scrubs Canadian-relevant identifiers (BC PHNs, SINs,
postal codes, phones, emails, addresses, DOBs, person names) before any
text reaches the LLM. Where the agent must produce personalized output
(e.g., a donor letter), placeholders are rehydrated from a per-call
mapping that is held only in memory.

Data residency is set via `NPO_DATA_RESIDENCY` (default `ca-central`).
For production deployments, point the Anthropic SDK at the Bedrock
client in `ca-central-1` or the Vertex client in `northamerica-northeast1`.

## Modules

| Module               | Status | Notes |
|----------------------|--------|-------|
| Grant Writer         | MVP    | Opus 4.7 + adaptive thinking; ~16K max output; cached system+persona prefix |
| Policy Navigator     | MVP    | Haiku 4.5; cheap RAG; cites source doc by title |
| Donor Concierge      | TBD    | Plugs into the same Agent base class |
| Automated Bookkeeper | TBD    | Plugs into the same Agent base class |

## Demo mode (BCSS walkthrough)

There's a built-in demo mode that auto-seeds a `BCSS Demo` tenant with realistic
program + policy docs and serves a clean HTML page at `/` for a non-technical
walkthrough. Two ways to run it:

### Option A — Render (one-link deploy, share the URL)

The repo ships with a `render.yaml` blueprint. Free tier; the service sleeps
after ~15 min idle and wakes in ~10s.

1. Push the branch to GitHub.
2. In Render: **New + → Blueprint** → point at this repo.
3. Set `ANTHROPIC_API_KEY` in the dashboard. Everything else is auto-set
   by the blueprint.
4. Render gives you a URL like `npoagent-bcss-demo.onrender.com`. Send that link.

### Option B — Laptop + ngrok (sit-on-the-couch walkthrough)

```bash
# install
pip install -e ".[dev]"

# minimal env — only ANTHROPIC_API_KEY is required
export ANTHROPIC_API_KEY=sk-ant-...
export NPO_DEMO_MODE=1

# boot
npo-agent init-db
npo-agent serve --port 8000
# → open http://localhost:8000 in your browser. Demo seeds itself on first hit.

# in another shell, expose it publicly
ngrok http 8000
# → ngrok prints a public https URL. Send that to her.
```

The demo UI has three tabs:

1. **Grant Drafter** — pre-filled BC Gaming brief; "Preview what gets sent"
   shows the PII-scrubbed payload before "Draft application" runs the agent.
2. **Policy Navigator** — quick-ask buttons for WHMIS, director changes,
   privacy breach, plus an off-policy question to show the agent refusing.
3. **Privacy filter** — paste any intake-style text (names, BC PHN, SIN,
   addresses, DOBs) and see exactly what does and doesn't leave the server.
   This is the tab to land on for a board-level discussion about PIPA / FOIPPA
   exposure.

## Production / multi-tenant quick start

For real multi-tenant operation (creating tenants, ingesting their docs,
issuing API keys), demo mode is irrelevant — use the admin endpoints:

```bash
pip install -e ".[dev]"

export ANTHROPIC_API_KEY=sk-ant-...
export NPO_ADMIN_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")

npo-agent init-db
npo-agent serve --port 8000
```

In another shell:

```bash
# create a tenant
curl -s -X POST http://localhost:8000/admin/tenants \
  -H "X-Admin-Token: $NPO_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"BCSS","persona":"clinical-empathetic"}'
# → returns a tenant_id and api_key (api_key is shown once)

# ingest a program description
curl -s -X POST http://localhost:8000/v1/documents \
  -H "X-API-Key: $TENANT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"title":"Crisis Line","body":"Our 24/7 crisis line...","namespace":"programs"}'

# draft a grant application
curl -s -X POST http://localhost:8000/v1/agents/grant-writer/draft \
  -H "X-API-Key: $TENANT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"funder":"BC Gaming","opportunity_title":"Community Gaming Grant 2025","funder_brief":"Supports mental health programs..."}'
```

## Pricing model

| Tier                | Implementation              | Notes |
|---------------------|-----------------------------|-------|
| Seat replacement    | Flat monthly fee per agent  | Price ≈ 10% of equivalent human hire |
| Implementation fee  | One-time setup + ingestion  | Often paid via tech grants |
| Usage-based         | Per draft / per family      | For provincial-scale orgs (BCSS) |

## Tests

```bash
pytest -q
```

Tests do not call Anthropic. Agent tests monkey-patch `llm.complete()`
so isolation, persona threading, PII rehydration, and namespace filtering
are all verified offline.

## Production hardening checklist

The MVP is small on purpose. Before serving real NPOs:

- [ ] Swap TF-IDF for `voyage-3` embeddings (interface stays the same)
- [ ] Move SQLite to Postgres with row-level security on `tenant_id`
- [ ] Add per-tenant rate limits (Anthropic API calls + ingest)
- [ ] Add structured audit logging (who saw what document when)
- [ ] Pin Anthropic SDK to a tested version in `pyproject.toml`
- [ ] Per-tenant Bedrock/Vertex routing if a tenant requires data residency
      in a region other than `ca-central`
