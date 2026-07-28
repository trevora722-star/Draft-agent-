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

## Environment variables

| Variable | Required | What it does |
|---|---|---|
| `CR_BASE_DOMAIN` | yes in prod | The platform's base domain, e.g. `confettiroll.com` |
| `CR_DATA_DIR` | no | Where the database + media live (default `./data`) |
| `CR_SECRET_KEY` | no | Cookie-signing secret; auto-generated and persisted if unset |

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
