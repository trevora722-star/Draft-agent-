# Blockers

Open questions and missing credentials that need a human decision before
TreadAgent can go live. Nothing here blocks continued development —
everything not listed as blocked keeps being built against the
`console`/offline adapters and a mocked NocoDB base.

## Missing credentials (expected — no live account exists yet)

None of these block development; they block *deploying* a live shop.
`.env.example` documents every one. Fill in `.env` when accounts exist:

- `NOCODB_BASE_URL` / `NOCODB_API_TOKEN` / `NOCODB_BASE_ID` — no NocoDB
  instance has been provisioned on DigitalOcean Toronto yet.
- `ANTHROPIC_API_KEY` — needed for Phase 2 drafting/summarization agents.
- `RESEND_API_KEY`, `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN` — needed for
  live send; `ConsoleAdapter`/dev mode covers local testing without them.
- `STRIPE_SECRET_KEY` + three price IDs — needed for live billing.
- `SPACES_ACCESS_KEY_ID`/`SPACES_SECRET_ACCESS_KEY` — needed for live
  file storage (tread photos, notice PDFs, evidence bundles).

## Decisions made without a human (documented so they can be revisited)

- **Repo layout**: this repo already contained an unrelated project
  (NPOAgent/PiledUp) at the root on this branch. Per explicit user
  instruction, TreadAgent is built self-contained under `treadagent/`
  rather than at the repo root, leaving the existing project untouched.
- **NocoDB table/field creation via API**: NocoDB's REST API for schema
  management varies by version (v1 `/api/v1/db/meta/...` vs v2
  `/api/v2/meta/...`). `scripts/provision-nocodb.js` targets the v2 meta
  API (current NocoDB) and is written defensively (checks for existing
  tables/fields before creating), but it has not been run against a real
  instance — verify against your actual NocoDB version before relying on
  it in production, and run with `--dry-run` first.
- **Regulations seed values**: seeded from the figures given in the spec
  (1.6mm legal minimum, BC 3.5mm/M+S/3PMSF winter designation, BC
  Oct 1–Apr 30 season). **These must be verified against current
  provincial authority sources before any shop goes live** — see the
  comment block at the top of the regulations seed in
  `scripts/provision-nocodb.js`.
- **Stripe tier pricing** ($249/$549/$1,149 CAD) is stored as seed
  config, not hardcoded, per the spec's "treat as placeholders" note —
  confirm final pricing before launch.
