# ConfettiRoll

Multi-tenant photo & video sharing for events — weddings, birthdays,
reunions, corporate parties. Organizers sign up, create events, and share a
link + password with guests. Every event gets its own subdomain
(`anna-and-james.confettiroll.com`) or the organizer's own custom domain.

## How it fits together

- **Main site** (`confettiroll.com`): landing page, organizer signup/login,
  and a dashboard to create events. Each event card shows its URL, the guest
  password, and a downloadable QR code for table cards and invitations.
- **Event sites** (`<slug>.confettiroll.com` or a custom domain): the guest
  gallery. Guests enter the event password once, then can view everything
  and upload photos (30 MB) and videos (200 MB). iPhone HEIC photos are
  converted to JPEG automatically.
- **Hosts are admins**: an organizer signed in on the main site
  automatically gets delete buttons in their own events' galleries (the
  session cookie is scoped to `.confettiroll.com`). Deletes are soft — files
  move to a per-event trash folder.

Tenancy is resolved from the Host header; accounts and events live in a
SQLite database and media on disk, both under `CR_DATA_DIR`.

## AI features (agents)

With `ANTHROPIC_API_KEY` set, two agents come alive (both built on the
Anthropic API with Claude Opus 5; server-side refusal fallbacks are enabled
so rare safety-classifier declines transparently retry on Anthropic's
recommended fallback model):

- **Album curator** — every uploaded photo is captioned, tagged, and given a
  1-10 highlight score in a background task. This powers the gallery's
  search box ("cake", "dancing", a guest's name) and the ✨ Highlights
  filter (score ≥ 8). Captions appear on tiles and in the lightbox.
- **Recap writer** — the "✨ AI recap" button on each dashboard event reads
  the whole album's captions and uploaders and writes a warm, shareable
  story of the day for the host to send with the album link.

Without the key everything else works normally — the AI toolbar simply
stays hidden. Rough cost: a few cents per hundred photos captioned.

## Referral program (wedding & event planners)

Every account gets a referral link (`/signup?ref=<code>`), shown on the
dashboard with live stats. Signups through the link are attributed
(`users.referred_by`), and each event a referred host creates accrues an
estimated commission of `CR_REFERRAL_FEE` (default $10 ≈ 20% of a ~$50
package — competitor one-time pricing runs $19–149). Payouts are marked
"pending" until billing launches; the attribution data is being recorded
now so no referral is lost. The landing page pitches the program to
planners directly.

## Environment variables

| Variable | Required | What it does |
|---|---|---|
| `CR_BASE_DOMAIN` | yes in prod | The platform's base domain, e.g. `confettiroll.com` |
| `CR_DATA_DIR` | no | Where the database + media live (default `./data`) |
| `CR_SECRET_KEY` | no | Cookie-signing secret; auto-generated and persisted if unset |
| `ANTHROPIC_API_KEY` | no | Enables the AI curator + recap agents |
| `CR_AI_MODEL` | no | Model for the agents (default `claude-opus-5`) |
| `CR_REFERRAL_FEE` | no | Estimated commission per referred event, in dollars (default `10`) |

## DNS setup (one-time)

1. Buy `confettiroll.com` and point it at the app host:
   - `confettiroll.com` -> your Render service (A/ALIAS record)
   - `*.confettiroll.com` -> the same service (wildcard CNAME). The wildcard
     is what makes every event subdomain work with zero per-event setup.
2. On Render, add both `confettiroll.com` and `*.confettiroll.com` as custom
   domains on the service so TLS certificates are issued for them.

## Customer custom domains

An organizer can enter their own domain (e.g. `photos.smithwedding.com`)
when creating an event; routing works as soon as they add a CNAME from that
name to `confettiroll.com`. For HTTPS on their domain, the domain must also
be added to the hosting service (Render dashboard or API). Automating that
via the Render API is the first post-MVP task.

## Run locally

```bash
cd confettiroll
pip install -r requirements.txt
uvicorn app:app --reload
```

Open http://localhost:8000 — sign up, create an event, and its gallery is
at `http://<slug>.localhost:8000` (browsers resolve `*.localhost` locally).

## Tests

```bash
cd confettiroll
python -m pytest test_app.py
```

## Current limitations / roadmap

- No billing yet ("free while in beta"); Stripe subscriptions are the
  obvious next step.
- No email verification or password reset.
- Custom-domain TLS needs the domain added on the host (automate via API).
- The guest password is stored in plain text by design — it's a shared,
  low-value secret the organizer needs to read back and share (organizer
  account passwords are properly hashed with scrypt).
