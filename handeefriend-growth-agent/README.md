# HandeeFriendGrowthAgent

Autonomous 26-agent growth system for [handeefriend.com](https://handeefriend.com).
Targets 1,000 subscribers in 6 months. Built with Node.js (ESM), Claude Sonnet 4
(`claude-sonnet-4-20250514`), NocoDB, Make.com webhooks, Resend, and Stripe (CAD).

## Quick start

```bash
npm install
cp .env.example .env
# fill in ANTHROPIC_API_KEY, NOCODB_*, RESEND_*, STRIPE_*, etc.

# Create all NocoDB tables (one-time)
npm run setup

# Boot the runner (Make.com hits this for scheduled runs)
npm run runner

# Run an individual agent locally (any of 01-26)
npm run 01           # TikTok scripts
npm run 03 -- --sequence=welcome
npm run 26           # Growth Commander

# Validate every agent end-to-end without sending email/writing to NocoDB
npm run dry-run-all
```

Every agent supports `--dry-run`. Runner endpoints accept `{ "dry_run": true }`.

## Agents

| #  | Cadence    | Purpose |
|----|------------|---------|
| 01 | weekly     | TikTok script variants |
| 02 | per-brief  | 900-word blog post |
| 03 | on-demand  | Email sequences (welcome, upgrade_nudge, etc.) |
| 04 | on-approve | TikTok → YouTube Shorts repurpose |
| 05 | monthly    | Facebook/Instagram ad copy deck |
| 06 | monthly    | Case study drafts (review-queued, never auto-sent) |
| 07 | weekly     | Realtor outreach via Apollo (50/run) |
| 08 | bi-weekly  | Inspector outreach (CSV or Apollo, 25/run) |
| 09 | monthly    | Trades outreach (40/run) |
| 10 | daily      | Reddit community monitor (queues replies for review) |
| 11 | daily      | Outreach follow-ups + 90-day re-touch |
| 12 | weekly     | Keyword research + GSC pulls |
| 13 | monthly    | On-page SEO audit |
| 14 | weekly     | Content brief generator (auto-triggers Agent 02) |
| 15 | monthly    | Backlink prospecting + draft outreach |
| 16 | daily      | Lead scoring (auto-triggers Agent 17) |
| 17 | event      | Personalized upgrade trigger w/ Stripe promo code |
| 18 | daily      | Churn detection + save sequences |
| 19 | weekly     | Re-engagement (signup_ghost, one_and_done, cancelled_60) |
| 20 | weekly     | Weekly metrics report |
| 21 | weekly     | Content performance insights |
| 22 | monthly    | Funnel analysis (extended thinking) |
| 23 | event      | Referral program (Stripe webhook driven) |
| 24 | monthly    | Partner dashboard + commission credits |
| 25 | monthly    | CASL/ASC/BC compliance check |
| 26 | weekly     | Growth Commander executive brief |

## Web interfaces

Self-contained HTML files in `interfaces/`:

- `dashboard.html` — Growth Commander dashboard
- `partner-portal.html` — Realtor/Inspector partner portal
- `review-queue.html` — Human approval queue (Agents 06 + 10)

Each prompts for the NocoDB connection details on first load and stores them
in `localStorage`. Drop them into Netlify, S3, or any static host.

## Architecture

- **Source of truth:** NocoDB. Every agent reads/writes via REST.
- **Scheduling:** Make.com calls `POST /run/:agent` on the runner with a Bearer token.
- **Output files:** `output/{agent-name}/{YYYY-MM-DD}.{json|html}`
- **Logging:** Every run writes a row to NocoDB `agent_logs`.
- **Compliance:** All sender-pulled emails include `{{unsubscribe_url}}` and the West Kelowna footer.
- **Safety:** Agents 06 and 10 never auto-publish. They queue for human approval.

## Stripe webhook

Point Stripe to `POST /webhooks/stripe` (raw body, not JSON). Handles:

- `customer.subscription.created` / `.updated` → updates `users.plan_tier`
- `customer.subscription.deleted` → reverts user to free, sets churn flag
- `checkout.session.completed` → triggers Agent 23 if metadata contains a referral source

## Make.com schedule (suggested)

| When            | Webhook                |
|-----------------|------------------------|
| Mon 06:00       | `/run/12` then `/run/14` (chains to `/run/02`) |
| Mon 09:00       | `/run/01`              |
| Mon 06:00       | `/run/20`              |
| Daily 07:00     | `/run/16`              |
| Daily 08:00     | `/run/10`              |
| Daily 09:00     | `/run/18`              |
| Daily 09:30     | `/run/11`              |
| Wed 10:00       | `/run/21`              |
| Sun 21:00       | `/run/26`              |
| 1st of month    | `/run/05` `/run/06` `/run/13` `/run/15` `/run/22` `/run/24` `/run/25` |
| Weekly Sun      | `/run/19`              |
| Bi-weekly       | `/run/08`              |
| Monthly         | `/run/09`              |

## Notes

- Apollo, Reddit, GSC integrations gracefully degrade when their env vars are
  missing — agents still produce content from prompts and warn in the log.
- The Custom Search API used by Agent 14 (Google Programmable Search) is
  optional; without it, briefs are built from Claude's prior knowledge only.
- For local development, `--dry-run` skips NocoDB writes, Stripe charges, and
  Resend sends, but still calls Claude (so requires `ANTHROPIC_API_KEY`).
