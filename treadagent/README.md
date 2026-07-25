# TreadAgent

Multi-agent tire storage, tread-tracking, and seasonal changeover SaaS
for independent Canadian tire shops and auto service centres.

See `CLAUDE.md` for the non-negotiable engineering constraints this
project is built under, `COMPLIANCE.md` for CASL/data-residency notes,
and `BLOCKERS.md` for open questions and missing credentials.

## Quick start

```bash
cd treadagent
npm install
cp .env.example .env   # fill in NocoDB credentials at minimum
npm run provision:dry-run   # preview schema changes, no writes
npm run provision           # create/verify NocoDB tables (idempotent)
npm run seed:demo           # realistic 600-set demo shop
npm test                    # node --test
npm start                   # service entry: cron + webhooks
```

## Layout

- `src/lib/` — shared modules. `nocodb.js` is the only file that talks
  to the database. `ledger.js`, `review.js`, `consent.js` are the three
  enforcement points every agent and send path must go through.
- `src/agents/` — the 12 agents, each a single `run(context)` export.
- `web/` — self-contained single-file HTML tools (no build step): tech
  capture, rack map, review queue, owner dashboard, dormancy console,
  customer booking.
- `scripts/` — provisioning, seeding, CSV import, rack tag printing,
  ledger verification — all CLI-invocable.
- `make/blueprints/` — Make.com scenario documentation (markdown, not
  JSON exports).
- `test/` — `node --test` suite, no external test framework.

## Status

See `PHASE1-REPORT.md` (deterministic core) and `PHASE2-REPORT.md`
(agents, outreach, billing) once each phase completes, plus
`DEMO-SCRIPT.md` for the 8-minute sales walkthrough.
