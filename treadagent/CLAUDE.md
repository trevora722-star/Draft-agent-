# CLAUDE.md — TreadAgent

Read this file first, every session, before touching code in `treadagent/`.

TreadAgent is a multi-agent tire storage, tread-tracking, and seasonal
changeover platform sold as SaaS to independent Canadian tire shops. It
lives in the `treadagent/` subdirectory of this repository, alongside an
unrelated pre-existing project (NPOAgent/PiledUp) at the repo root — do
not touch files outside `treadagent/` when working on this project.

## The six non-negotiable constraints

1. **Node.js with ESM only.** `"type": "module"` in `package.json`. No
   CommonJS (`require`/`module.exports`), no TypeScript, no `.cjs`.
2. **All web interfaces are self-contained single-file HTML.** One
   `.html` file each, inline `<style>` and `<script>`, no React/JSX/Vue,
   no bundler, no npm frontend dependencies. Charts are hand-rolled SVG
   or inline `<canvas>`.
3. **NocoDB is the database** (DigitalOcean Toronto, Canadian data
   residency for all customer PII). `src/lib/nocodb.js` is the *only*
   module that talks to the NocoDB REST API. Every other module goes
   through it.
4. **SHA-256 append-only audit ledger.** Every state change and every
   outbound action writes a ledger row chained to the previous row's
   hash via `src/lib/ledger.js`. The ledger is never updated or deleted,
   only appended to and verified.
5. **Full Review human gate before any outbound action.** No SMS,
   email, notice, or quote leaves the system without a shop staff
   member approving it in the review queue. This is enforced in
   `src/lib/review.js`'s `assertApproved()`, called from the send path
   itself (`sendOutbound()` in `src/lib/consent.js`) — not just the UI.
   A send attempt on an unapproved record must throw.
6. **No LLM in any safety or math path.** Tread depth arithmetic,
   wear-rate projection, threshold comparison, capacity math, and
   statutory deadline calculation are deterministic JavaScript in
   `src/lib/tread-math.js` and `src/lib/capacity-math.js`, unit tested.
   The Claude API (`src/lib/claude.js`) is used only for natural-language
   drafting, prioritization narrative, and summarization — never to
   compute a number or a legal/safety threshold decision.

## CASL compliance

Enforced structurally in `src/lib/consent.js`, not as a UI disclaimer:

- `sendOutbound()` throws if `consent_type` is `none`, `unsubscribed_at`
  is set, the message record isn't `approved`, or shop-local time is in
  quiet hours.
- Unsubscribe (STOP/ARRÊT/UNSUBSCRIBE/DÉSABONNEMENT) is processed
  immediately and logged to the ledger on *receipt*, not on processing.
- Every outreach row stores `consent_snapshot_json` — consent state at
  send time — for audit survivability.
- See `COMPLIANCE.md` for the transactional-vs-commercial message
  judgment call (system treats all customer messaging as CEM).

## Tread thresholds live in config, not code

Provincial tread thresholds and winter-season windows live in the
`regulations` NocoDB table (seeded by `scripts/provision-nocodb.js`),
never hardcoded in agent or math modules. **The seed values must be
verified against the current provincial authority before any shop goes
live** — the seed script says so in a comment; do not remove it.
Measurements are captured and stored as integer 32nds of an inch;
millimetre display is a conversion at render time only, never a stored
float.

## Tread capture is gauge-only

Never build photo- or computer-vision-based tread measurement. Three
manual readings per tire (outer/centre/inner) from a digital depth
gauge are the only measurement source. A photo may be attached as
corroborating evidence only.

## The 30-second budget

`web/tech-capture.html` must be usable in under 30 seconds per set, on a
phone, with cold hands, in a shop bay: large tap targets, numeric
keypad input, no scrolling between tires, no login beyond a
device-remembered token, offline-first local queueing with retry. This
constraint governs every UI decision in that file — if a change adds
friction, it's wrong.

## Agent contract

Every agent in `src/agents/` exports a single `run(context)` function,
is independently invocable from the CLI, logs a row to `agent_runs`,
and writes any proposed outbound action to `review_queue` — it never
calls `sendOutbound()` directly.

## Dormancy ladder

The evidence bundle sealed at stage 5 is the product's differentiator.
TreadAgent produces a documentary record; it never asserts that a
disposal is lawful — that disclaimer must appear in every notice
template and in the dormancy console UI.
