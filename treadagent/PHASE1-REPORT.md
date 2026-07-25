# Phase 1 Report — Deterministic Core

Status: **complete**. 166 tests passing (`npm test`), zero LLM calls anywhere in this phase.

## What works

### Data access & audit
- **`src/lib/nocodb.js`** — the sole NocoDB client. Retry with exponential backoff and `Retry-After` support on 429/5xx, table-name→id resolution with caching, a `where`-clause query builder, full CRUD, and an injectable transport (`__setFetch`) that every test in the suite uses instead of a live instance.
- **`src/lib/ledger.js`** — SHA-256 append-only chain (`append`/`verifyChain`/`sealBundle`), canonical (sorted-key) JSON payloads, genesis hash for row 1. Tamper detection covers a mutated payload, a forged hash, and a deleted middle row (sequence-gap detection) — all three are distinguishable failure modes in `verifyChain()`'s output.
- **`scripts/provision-nocodb.js`** — idempotent schema creation/repair against `src/config/schema.js` (single source of truth for all 20 tables), plus regulations seeding. `--dry-run` needs no credentials at all. Verified idempotent via an automated double-run test, not just by inspection.
- **`scripts/verify-ledger.js`** — CLI, green/red per shop, exits non-zero on any break, `--shop=<id>` to scope.

### The three enforcement points (CLAUDE.md constraints #4/#5/#7)
- **`src/lib/review.js`** — Full Review gate. `assertApproved()` is what the send path calls; a message with no approved `review_queue` row cannot send, full stop. Reject requires non-empty decision notes.
- **`src/lib/consent.js`** — `sendOutbound()` is the CASL choke point: throws on unapproved review, `consent_type: none`, unsubscribed customers, or shop-local quiet hours, all before the injected transport adapter is ever invoked. Unsubscribe keyword matching (STOP/ARRÊT/UNSUBSCRIBE/DÉSABONNEMENT, accent- and case-insensitive) is immediate and idempotent — logged to the ledger on receipt, exactly one confirmation ever. A cross-agent test confirms a message that's fully consented and outside quiet hours *still* cannot send without review approval — the gates are independent, not redundant.
- **`src/lib/dates.js`** — every quiet-hours/deadline calculation goes through here, built on Node's Intl/ICU rather than a fixed-offset assumption, so DST transitions in a shop's real timezone are handled correctly (tested explicitly against Toronto's March/November transitions).

### Math (`src/lib/tread-math.js`) — zero I/O, zero LLM
`minDepth`, `wearRate`, `projectThreshold`, `classify`, `irregularWear`, plus 32nds↔mm display conversion. `classify()`'s severity ordering is legal-minimum > winter-designation > practical-replacement > monitor — a stricter regulatory check always preempts a shop heuristic. `irregularWear()` only ever produces a flag and an inspection note, never a diagnosis. 33 table-driven boundary tests.

### Agents (deterministic subset: 1, 2, 6, 9)
All four follow the same contract: `run(context)`, log to `agent_runs` via `src/lib/agent-run.js`, never call `sendOutbound()` directly, independently CLI-invocable.
- **01-intake** — customer/vehicle dedup by phone/plate, tire_set + tires creation, first-fit rack assignment, consent capture at the point of intake.
- **02-tread** — records a validated reading, delegates all math to lib/tread-math.js, writes `wear_projections` once there's enough dated history.
- **06-rack** — assign/release/transfer (each logs a `movements` row + ledger entry) and a read-only `suggestConsolidation()` for rack-map.html.
- **09-compliance** — ledger `verifyChain`, a consent audit, and critically: an *independent* re-check of every SENT message's frozen `consent_snapshot_json`, catching anything that slipped past `sendOutbound()` some other way (manual DB edit, bug) rather than just trusting the enforcement point it's auditing.

### Web tools (`web/*.html`) — self-contained, no build step, verified in a real browser
All three were exercised end-to-end with Playwright against the mock NocoDB transport behind a real `node:http` server (`src/server/api.js`) — not just visually inspected:
- **tech-capture.html** — rack-tag lookup, four-tire-card layout with numeric-keypad inputs, auto-advance, live colour-coded min-depth feedback (client-side approximation only; agent 2's server-side `classify()` on submit is authoritative), offline localStorage queue that survives a dropped connection and retries on reconnect, device-remembered token. Confirmed: full submit flow writes real `tread_readings`; unknown tag and empty-slot states render correctly; below-legal reading correctly triggers the red state.
- **rack-map.html** — site/zone grid, occupancy + dormancy-stage colour coding, search-driven highlighting, click-through slot detail, consolidation candidates. Confirmed correct after finding and fixing a real bug (below).
- **review-queue.html** — risk-grouped cards with full rendered preview, approve/reject (reject requires notes, enforced both client- and server-side), bulk-approve restricted to low-risk. Confirmed: reject blocks without notes; approve actually removes the item from the pending list server-side.

### Onboarding & sales asset
- **`scripts/import-csv.js`** — hand-rolled CSV parser (quoted fields, escaped quotes), per-row validation report, in-file duplicate detection on phone/plate, `--dry-run`. Every importable row goes through the *real* `agents/01-intake` run — not a bulk-insert shortcut — so imported data gets identical ledger/consent treatment to a live intake.
- **`scripts/seed-demo-shop.js`** — 600 tire sets / 300 customers / 580 rack locations, 2-4 seasons of drive-type-weighted wear-curve history, exactly ~8% dormancy across a weighted stage-1-through-5 distribution, and a changeover-window appointment book staggered across 6 weeks (the capacity-smoothing story, made visible in the data itself rather than asserted in a slide). Runs the full 600-set generation in under 200ms against the mock.
- **`scripts/print-rack-tags.js`** — printable HTML sheet with real embedded SVG QR codes per active rack location, verified with a Playwright screenshot (8/8 tags rendered, scannable, correctly labelled).

## A bug found and fixed along the way

`test/helpers/mock-nocodb.js`'s `seed()` originally returned the *entire* table's row array instead of just the newly inserted rows — invisible in every committed test (none happened to seed the same table twice within one test) but would have silently returned stale records to any Phase 2 test that did. Found via an ad-hoc smoke script, fixed, full suite re-verified green (141→166 as later work landed).

## What's stubbed / explicitly deferred to Phase 2

- **No LLM calls exist yet** — `src/lib/claude.js` doesn't exist. Agents 3, 4, 5, 7, 8, 10, 11, 12 are not built.
- **`src/index.js`** wires up the HTTP API only; cron registration and webhook routes (`src/webhooks/*`) are Phase 2.
- **Photo attachment** in tech-capture.html sets a `pending-upload` placeholder — real DigitalOcean Spaces upload (`src/lib/storage/spaces.js`) doesn't exist yet.
- **SMS/email adapters** (`src/lib/sms/*`, `src/lib/email/resend.js`) don't exist yet — `sendOutbound()`'s `transport` parameter is fully wired for them but nothing currently implements it beyond test mocks.
- **Stripe billing, dormancy evidence bundles, Make.com blueprints** — all Phase 2.
- `scripts/provision-nocodb.js` has never run against a live NocoDB instance (none exists yet — see BLOCKERS.md). The v2 meta/data API shapes it targets are correct per current NocoDB documentation, and it's been exercised thoroughly against a mock that implements the same request/response contract, but that's not the same as a live confirmation.

## Test coverage

```
npm test
# 49 suites, 166 tests, 0 failures
```

Every `src/lib/*.js` and every Phase 1 agent has direct unit tests. `src/server/api.js` and all three `web/*.html` tools have both automated tests (mocked transport) and manual browser verification (Playwright, real HTTP server, real DOM interaction). `scripts/*.js` are all tested via their exported `main()`/helper functions against the mock, not just described.

See `BLOCKERS.md` for credentials/decisions that need a human before going live.
