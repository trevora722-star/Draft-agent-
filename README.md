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
walkthrough. Three ways to run it:

### Option A — Netlify (paid plan; one-link deploy)

The repo ships with `netlify.toml`, a `public/` static directory, and four
Python Netlify Functions in `netlify/functions/`. The static site serves
`demo.html`; the functions back the `/api/demo/*` endpoints.

Constraints baked into this deploy: the grant drafter runs on **Claude Haiku
4.5** with thinking disabled and `max_tokens=4000` so a full draft fits inside
the 10s sync function timeout. Tenants and any drafts she generates live in
Lambda's `/tmp` — they survive warm starts but reset on cold start (the BCSS
demo tenant re-seeds idempotently on first hit).

1. Push this branch to GitHub.
2. In Netlify: **Add new site → Import an existing project** → pick the repo.
3. **Site settings → Environment variables** → add `ANTHROPIC_API_KEY`.
4. Trigger a deploy. Netlify gives you a `*.netlify.app` URL — send that link.

If you want to test the functions locally before deploying, install the
Netlify CLI and run `netlify dev` from the repo root.

### Option B — Render (free tier; one-link deploy with full Opus 4.7)

For the same UI but Opus 4.7 + adaptive thinking on the grant drafter
(higher quality, ~30–90s per draft, doesn't fit a serverless timeout). The
repo ships with a `render.yaml` blueprint. Free tier sleeps after ~15 min
idle and wakes in ~10s.

1. Push the branch to GitHub.
2. In Render: **New + → Blueprint** → point at this repo.
3. Set `ANTHROPIC_API_KEY` in the dashboard. Everything else is auto-set.
4. Render gives you a URL like `npoagent-bcss-demo.onrender.com`. Send that link.

### Option C — Laptop + ngrok (sit-on-the-couch walkthrough)

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

## QAgent (Node) — crawler + intelligence pipeline

A separate Node tool that lives alongside the Python NPO platform: it crawls a
web application, collects evidence (screenshots, console errors, failed
requests, a11y/perf/security signals via the test battery), triages the raw
findings into deduplicated bugs with calibrated severity, and renders a
self-contained HTML report.

```bash
npm install
npx playwright install chromium
cp .env.example .env   # fill in ANTHROPIC_API_KEY
node run.js https://example.com
# → runs/<ts>/{inventory.json, findings.json, screenshots/, bugs.json, statistics.json, analysis.md, report.html}
```

### Crawler

The crawler (`crawler/explorer.js`) does a BFS, same-origin, depth-limited
traversal from the base URL. For each route it:

1. Navigates and waits for `networkidle` plus SPA hydration probes.
2. Captures HTTP status, page title, final URL after redirects.
3. Takes a full-page screenshot at 1440×900 and writes it to `screenshots/`.
4. Inventories interactive elements with stable selectors (id ➜ `data-testid`
   ➜ `aria-label` ➜ CSS path with `nth-of-type` segments).
5. Collects console errors and 4xx/5xx network failures during the visit.
6. Emits one Finding per console error / failed request / route ≥ 400.

`inventory.json` example entry:

```json
{
  "url": "https://example.com/login",
  "final_url": "https://example.com/login",
  "status": 200,
  "title": "Sign in — Example",
  "screenshot_path": "screenshots/route-003-example_com_login.png",
  "elements": [
    { "kind": "input", "selector": "#email", "type": "email", "name": "email" },
    { "kind": "input", "selector": "#password", "type": "password", "name": "password" },
    { "kind": "button", "selector": "button[type=submit]", "text": "Sign in" },
    { "kind": "link", "selector": "a[href='/forgot']", "text": "Forgot password?", "href": "https://example.com/forgot" }
  ],
  "errors": [],
  "depth": 1,
  "elapsed_ms": 842
}
```

`findings.json` is the **shared contract** consumed by Session B (battery) and
Session C (intelligence). Each entry is validated against the zod schema in
`lib/schema.js`. Categories: `console_error`, `network_error`, `http_status`,
`accessibility`, `performance`, `security`, `visual`, `content`,
`form_validation`, `navigation`, `auth`, `seo`. Severities: `critical`,
`high`, `medium`, `low`, `info`.

### Auth

```bash
# HTTP basic
node run.js https://staging.example.com --auth-mode basic --auth user:pass

# Form login — detects email/password/submit, or pass explicit selectors
node run.js https://app.example.com \
  --auth-mode form \
  --login-url https://app.example.com/login \
  --auth alice@example.com:hunter2
```

### Intelligence + Report

The intelligence pass runs automatically as part of `run.js`, but can be
invoked stand-alone against any `findings.json`:

```bash
# Render against the bundled fixture (30+ varied findings)
npm run fixtures:intel
# → runs/fixture/{bugs.json, statistics.json, analysis.md, report.html}
```

Pipeline:

1. **`intelligence/triage.js`** — exact-match dedup, then a Claude Opus pass
   for semantic merges, then a per-cluster severity recalibration with
   explicit reasoning and confidence. Outputs `bugs.json`.
2. **`intelligence/statistics.js`** — deterministic stats: distributions by
   severity / category / agent / route, top 10 selectors, Pearson `r` between
   form complexity and bug count per route, and a category-by-route
   co-occurrence matrix. Outputs `statistics.json`.
3. **`intelligence/hypothesis.js`** — one high-effort Claude Opus call asks
   for root-cause hypotheses, architectural inferences, risk-surface
   analysis, prioritization, and a confidence statement. Outputs
   `analysis.md`.
4. **`intelligence/report.js`** — renders `report/template.html` (Tailwind via
   CDN, Inter font, vanilla-SVG charts, vanilla-JS lightbox) with all data
   inlined. The result is a single self-contained `report.html`. The URL
   fragment `?bug=<id>` deep-links and auto-expands a specific bug.

## Production hardening checklist

The MVP is small on purpose. Before serving real NPOs:

- [ ] Swap TF-IDF for `voyage-3` embeddings (interface stays the same)
- [ ] Move SQLite to Postgres with row-level security on `tenant_id`
- [ ] Add per-tenant rate limits (Anthropic API calls + ingest)
- [ ] Add structured audit logging (who saw what document when)
- [ ] Pin Anthropic SDK to a tested version in `pyproject.toml`
- [ ] Per-tenant Bedrock/Vertex routing if a tenant requires data residency
      in a region other than `ca-central`
