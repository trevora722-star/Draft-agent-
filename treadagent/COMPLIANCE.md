# Compliance notes

## CASL (Canada's Anti-Spam Legislation)

TreadAgent treats **every customer-facing SMS/email as a Commercial
Electronic Message (CEM)** and requires express consent before sending,
even though a plausible argument exists that a pure changeover reminder
("your winter tires are ready to swap") is transactional rather than
commercial. The system does not rely on that argument, because in
practice every reminder template TreadAgent sends can carry a
replacement-tire offer or a booking upsell, which makes the
transactional carve-out unsafe to lean on.

**This is a judgment call, not a legal opinion. Flag it for the
operator's own counsel to confirm before relying on it.**

Structural enforcement (see `src/lib/consent.js`, called from every send
path — not just documented, enforced in code):

- Every CEM includes the shop's legal name, mailing address, and a
  working phone or email, plus a working unsubscribe mechanism.
- `sendOutbound()` throws — refuses to send — if `consent_type` is
  `none`, `unsubscribed_at` is set, the outreach record is not
  `approved` in the review queue, or current shop-local time falls
  inside that shop's configured quiet hours.
- Unsubscribe requests (SMS `STOP`/`ARRÊT`/`UNSUBSCRIBE`/
  `DÉSABONNEMENT`, or an email unsubscribe link) are processed
  immediately: `unsubscribed_at` is set and logged to the audit ledger
  **on receipt**, not once some batch job gets to it, and exactly one
  confirmation message is sent in reply.
- Every `outreach_messages` row stores `consent_snapshot_json` — the
  full consent state as it existed at send time — independent of what
  the customer's consent record says later. This is what makes an
  audit survivable: a regulator asking "what did you know when you sent
  this" gets a frozen answer, not today's mutable record.
- Consent evidence and message records are retained a minimum of 3
  years (see retention policy enforcement in Agent 9 `compliance`).

## Dormancy / disposal notices

TreadAgent produces a documentary record of contact attempts, notices,
and deadlines for abandoned tire sets. **It does not provide legal
advice and does not assert that any particular disposal is lawful.**
Every notice template and the dormancy console UI carry this disclaimer.
The shop is responsible for confirming its own disposal obligations
(often provincial repair-and-storage-lien or warehouse-lien legislation,
which varies by province and by whether a written storage agreement
exists) with its own counsel before acting on a sealed evidence bundle.

## Data residency

All customer PII is stored in NocoDB hosted on DigitalOcean's Toronto
(`tor1`) region. File storage (tread photos, signed intake forms,
notice PDFs, evidence bundles) uses DigitalOcean Spaces, also `tor1`.
No customer PII is sent to any service outside Canada except the
minimum necessary payload to Anthropic (drafting text), Resend (email
delivery), Twilio (SMS delivery), and Stripe (billing) — each of which
is a necessary sub-processor for the feature it supports.
