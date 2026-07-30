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

## Venue white-label tier (wineries, golf courses, event spaces)

Any account can create one venue from the dashboard's self-serve **brand
studio** — no sales call, no configuration by us:

- **Their assets, uploaded by them**: logo (accent color is auto-extracted
  from its dominant color), up to 12 showcase photos, tagline, headline,
  and about copy — all editable in place.
- **The brand agent** (✨): with `ANTHROPIC_API_KEY` set, one click sends
  their logo (vision) + venue type + notes to Claude Opus 5, which writes
  the tagline/headline/about and picks a matching accent — instantly
  applied, still editable.
- **Their own domain**: the venue page answers at
  `their-slug.<base domain>` and at any custom domain they enter
  (CNAME → base domain; add the domain on the host for TLS). Tenant
  routing resolves events first, then venues.
- **Branded events**: events can be assigned to the venue at creation;
  their galleries and guest-login pages inherit the venue's accent color
  and show a "Hosted at <venue>" badge linking back. The venue page lists
  all its event albums.
- Storage: `data/venues/<id>/` (logo.png + photos/), rows in `venues`,
  `events.venue_id` links events.

## Trade show kiosk (virtual booth agent)

Set `CR_KIOSK_KEY` and open `/kiosk?key=<key>` once on the booth tablet
(the device stays unlocked for a 3-day show weekend). The kiosk shows:

- A pitch panel with the three offers and a QR code to a live demo album
  (`/kiosk?key=...&event=<slug>` points the QR at any event — pair it with
  that event's `/stream` on the booth TV for the full effect).
- **The booth agent**: a Claude-powered chat (quick-reply chips + free
  text) that identifies whether the visitor is a couple, planner, or venue,
  pitches the right offering from a grounded product brief, and calls its
  `save_lead` tool when someone shares contact details. Leads land in the
  `leads` table; export with `/kiosk/leads.csv?key=<key>`.
- Rate limiting per device, a no-AI fallback message, and an idle reset
  that clears the conversation between visitors.

## Proms & grad nights (tagged events)

A safety-first mode for schools, selectable at event creation ("🎓 Prom /
grad night"). It flips the privacy model of a normal event:

- **Personal codes instead of a shared password.** The organizer pastes the
  class roster on the dashboard; every student gets a 6-character access
  code (downloadable as a CSV for printing on ticket cards). The event
  password becomes the **staff upload code** instead of a guest password.
- **Staff-only uploads.** Students can't upload at all. The school's
  designated staff member (e.g. the Vice Principal) signs in with the
  upload code and gets a `staff` role: upload photos and tag the students
  in each shot (🏷 button → checkbox picker) — nothing else (no deletes,
  no roster, no event-wide book). The event owner (admin) can do all of
  that too. Tag data lives in each photo's meta JSON
  (`tagged: [member ids]`).
- **Tagging gates visibility, not just organization.** A student sees only
  photos they're tagged in — enforced on the listing *and* on the
  photo/thumbnail routes, so an untagged photo ID 404s even if known. The
  live big-screen slideshow and the all-photos event book are owner-only
  on tagged events.
- **Each student picks their own book.** Students star favourites (☆/★ on
  tiles, stored in `selections`) and one click composes a personal
  keepsake book PDF from their picks (all their tagged photos if they
  haven't picked yet) at `/my-book.pdf` — their night, chosen by them.

**Business model:** the school pays one flat set-up fee per event (the
Prom Night package, $199 — roster, codes, staff uploads, and every
student's book builder included; no per-student charges). Students and
parents order printed keepsake books directly from the platform at the
regular book price, so the school never handles money and book revenue
stays 100% with the platform.

Tables: `members` (per-event roster + codes) and `selections` (member ×
photo picks); personal books are stored at
`data/events/<id>/books/member-<member id>.pdf`. Sessions use distinct
`m.<event>:<member>` (student) and `s.<event>` (staff) tokens in the
guest cookie, signed the same way as every other session.

## Close the album → announce the book → sell the book

The host's finish-line flow, built into every event card:

- **🔒 Close album** freezes uploads (guests see "the host has closed the
  album"; the host can still add last-minute shots, and can reopen any
  time). Close it, then compose the keepsake book knowing it's final.
- **Email capture at the door**: the guest sign-in page has an optional
  email field ("get the album & keepsake book when it's ready") — stored
  per event in `subscribers`, duplicates ignored.
- **📣 Email the book**: once the book exists, one click emails everyone
  who signed up: the book is ready, the album link, and how to order the
  printed hardcover. Idempotent — each subscriber is emailed once.
  Email sends via Resend (`RESEND_API_KEY`) or any SMTP relay
  (`SMTP_HOST`/`SMTP_PORT`/`SMTP_USER`/`SMTP_PASS` + `MAIL_FROM`); with
  neither configured the dashboard shows who's waiting and the exact
  message to copy-send manually.
- **🛒 Order printed book**: a button in the gallery toolbar for guests
  and hosts (`POST /api/book-order`) that opens Stripe Checkout for the
  $59 printed book ("free PDF today" beta message without Stripe keys).
  **One copy per order, by design**: quantity is locked at 1 and each
  checkout collects its own shipping address — so any number of copies
  can ship to the same address, each as its own quick order (checkout
  returns to the gallery with an "order another copy" prompt).

## Host editing & guest book picks

- **Photo editing (host)**: in the lightbox, admins get rotate left/right,
  one-click auto-enhance (brightness/contrast/color/sharpness via Pillow),
  and ✏️ caption/credit editing (captions work for videos too). Thumbs are
  regenerated and cache-busted via `edited_at`.
- **Click-to-book after close**: once the host closes the album, every
  guest sees ☆ on tiles — starring a photo votes it into the keepsake
  book (per-browser voter id, `book_picks` table). The host sees vote
  counts, and book generation uses exactly the starred set when any picks
  exist (falling back to the whole album otherwise).

## Family reunions & birthdays page

`/celebrations` — a dedicated vertical page (linked from the landing nav)
pitching reunions, birthdays, and anniversaries: no-app sharing for every
generation, the living-room slideshow, AI search, and the reunion book.

## Promo codes

`billing.PROMO_CODES` maps codes to free packages — one redemption per
account, granted via the dashboard's "Have a code?" box (`POST
/api/redeem`, recorded as a $0 purchase + credits). The built-in
**armstrong** code grants a free Celebration (family & friends). Add more
via `CR_PROMO_CODES="code:package,code2:package2"`.

## Sample keepsake books (Gemini)

`make_samples.py` generates three complete sample Heirloom books —
a vineyard wedding, a 50th-birthday pool party, and a high school prom —
using Gemini image generation (`GEMINI_API_KEY`; model override
`GEMINI_IMAGE_MODEL`, default `gemini-2.5-flash-image`) with a detailed,
diverse shot list per event, then composes real PDFs with `book.py`. Output
lands in `static/samples/`, and the landing page links "Flip through a
sample book" automatically when the PDFs exist. Images cache in
`samples_work/` (gitignored) so re-runs only fill gaps.

## Pricing packages & billing (Stripe)

Packages are defined once in `billing.py` and rendered everywhere (landing
page, dashboard, `/api/packages`):

| Package | Price | What it is |
|---|---|---|
| Starter | Free | 1 event, 100 photos, 3-month gallery |
| Celebration | $49/event | Unlimited media, AI features, live wall, book PDF, 12 months |
| Heirloom | $99/event | Celebration + printed book + custom domain + 24 months |
| Prom Night | $199/event | School flat fee: tagged safety mode, roster codes, staff uploads, student book builders |
| Partner 10-Pack | $245 | 10 Celebration credits at ~50% off — pros resell at their price |
| Printed book | $59 | 8×8" hardcover (fulfillment via print-on-demand, next build) |
| Venue Boutique | $99/mo | White-label, up to 2 hosted events/month |
| Venue Estate | $199/mo | White-label, up to 6 hosted events/month |
| Venue Grand | $399/mo | White-label, unlimited events |

Set `STRIPE_SECRET_KEY` + `STRIPE_WEBHOOK_SECRET` and checkout goes live:
`POST /api/checkout/<package>` creates a Stripe Checkout Session, and
`/stripe/webhook` (point a Stripe webhook at it for
`checkout.session.completed`) records the purchase and grants event
credits idempotently (`purchases` + `credits` tables; balance shown on
the dashboard). Without keys, checkout answers `{"beta": true}` and the UI
says everything is free during the beta.

**Partner economics** (swept across all copy): referral partners earn
**50% of each referred client's first package**; wholesale 10-packs let
pros set their own retail; book revenue stays 100% with the platform.
`CR_PARTNER_RATE` / `CR_REFERRAL_FEE` (default $25/referred event) control
the outreach pitch copy and dashboard estimates.

## Booth avatar & partner outreach engine

- **Callie, the booth avatar**: the kiosk chat is now fronted by an animated
  SVG character who blinks, bobs, looks up while thinking, and lip-syncs
  while *speaking replies aloud* via the browser's built-in speech engine
  (free/offline; 🔊 toggle for loud floors). A 🎙️ mic button (Chrome/Edge)
  lets visitors talk instead of type. Swapping in a photoreal streaming
  avatar (HeyGen Interactive Avatar / D-ID Agents) later only touches the
  kiosk template.
- **Outreach engine** (`/outreach?key=<CR_KIOSK_KEY>`): a virtual
  pitch-to-partner system for recruiting photographers, planners, and
  venues. Paste prospect lists (name, business, type, email, city, notes),
  and the AI writes a personalized recruitment email per prospect with a
  type-specific angle (photographers = complementary guest photos; planners
  = referral income; venues = white-label). One-click copy or open-in-email,
  status pipeline (new → pitched → replied → joined), CSV export. The
  offered rate is `CR_PARTNER_RATE` (default "20%") so repositioning to a
  50% headline offer is an env change plus page copy.

## Keepsake book (heirloom PDF album)

One click on a dashboard event ("📖 Keepsake book") composes a print-ready
square 8×8" PDF album from the whole gallery:

- A cover in the event's accent color (venue-branded when applicable),
  with title, date, and "Hosted at <venue>".
- "The Story of the Day" foreword — the AI recap (reused if already
  generated, generated on the fly when the API key is set).
- One photo per page, chronological, with the AI caption and "shared by"
  credit; if the album exceeds 150 photos, the best-scored ones are kept
  while preserving chronology.
- A closing "With love and thanks" page naming every contributor.

The owner gets a share URL (`<event host>/book.pdf`); guests with the
event password can download it from the gallery toolbar, so couples can
send it to family and the wedding party or hand it to any print shop or
photo-book service. Built with reportlab; stored at
`data/events/<id>/book.pdf` and regenerated on demand. Print-on-demand
fulfillment (Lulu/Peecho API) is the natural next step.

## Live venue stream (big-screen slideshow)

Every event has a live slideshow at `<event host>/stream` — made for the
TV or projector at the venue:

- Full-screen crossfading slideshow with a slow Ken Burns zoom, showing AI
  captions and who shared each photo.
- Polls the album every 7 seconds; **new uploads jump the queue** and flash
  a "✨ Just added" badge — guests see their photo on the big screen moments
  after sharing it.
- A corner panel shows the event QR code and address ("scan to add your
  photos"), so the screen itself recruits contributors all night.
- Venue-branded: accent color, logo, and "Hosted at <venue>" line.
- Same auth as the gallery (guest password or host session); click toggles
  fullscreen. Reachable from the gallery toolbar and each dashboard event
  card ("📺 Live slideshow").

## Partner program pages & assets

- `/partners` — public partner landing page: the transparent "20% on every
  event" pitch, how-it-works steps, downloadable partner badges, and the
  charity option. Linked from the main nav ("For planners").
- **Referral QR code** — every account can download a QR of their referral
  link from the dashboard (`/api/referral-qr.png`) for business cards and
  venue displays.
- **Partner badges** — light/dark "Official Partner" SVGs in `static/`.
- **Charity option** — partners can name a charity on the dashboard; their
  commissions are flagged for donation instead of payout (`users.charity`).

The landing and partner pages use hero photography hot-linked from
Unsplash's CDN (free to use under the Unsplash license). Every image has a
gradient-tile fallback via `onerror`, so a removed or unreachable image
degrades gracefully. To swap in your own photos, replace the
`images.unsplash.com` URLs in `templates/landing.html` and
`templates/partners.html`.

## Referral program (wedding & event planners)

Every account gets a referral link (`/signup?ref=<code>`), shown on the
dashboard with live stats. Signups through the link are attributed
(`users.referred_by`), and each event a referred host creates accrues an
estimated commission of `CR_REFERRAL_FEE` (default $10 ≈ 20% of a ~$50
package — competitor one-time pricing runs $19–149). Payouts are marked
"pending" until billing launches; the attribution data is being recorded
now so no referral is lost. The landing page pitches the program to
planners directly.

## Photo ownership & privacy ("your photos are never ours")

The stance, stated on the landing page FAQ and enforced in the product:

- Uploaded media belongs to the host and their guests. Files are stored
  only to serve the private gallery, for the gallery's lifetime.
- No AI training on customer photos, no marketing use, no selling or
  sharing. The AI agents *read* photos to caption them for the owner's own
  album — outputs stay in the album.
- **Delete forever**: every dashboard event card has a permanent-delete
  button (`POST /api/events/{id}/delete`) that removes the event row,
  roster, selections, and the whole `data/events/<id>/` tree — photos,
  thumbnails, meta, books, and trash included. Nothing is retained.
- Expired galleries are permanently deleted, not archived (automated
  retention sweep is on the roadmap; deletes are manual until then).

## Sign in with Google (identity only)

Set `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` (a standard OAuth web
client; authorized redirect URI `https://<base domain>/auth/google/callback`)
and "Continue with Google" buttons appear on the sign-in and sign-up pages.

Scopes are deliberately minimal — `openid email profile` — so the platform
receives the person's name and verified email and *nothing else*: no Google
Photos, Drive, Gmail, or contacts access exists to be granted. The email is
what reminder notices (gallery expiry, book deadlines) will be sent to.
Google-created accounts have no password; the same email can later be
claimed with a password reset once that flow exists. Referral attribution
(`?ref=`) is carried through the OAuth `state` parameter, HMAC-signed.
Without the env vars the buttons simply don't render.

## Environment variables

| Variable | Required | What it does |
|---|---|---|
| `CR_BASE_DOMAIN` | yes in prod | The platform's base domain, e.g. `confettiroll.com` |
| `CR_DATA_DIR` | no | Where the database + media live (default `./data`) |
| `CR_SECRET_KEY` | no | Cookie-signing secret; auto-generated and persisted if unset |
| `ANTHROPIC_API_KEY` | no | Enables the AI curator + recap agents |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | no | Enables "Sign in with Google" (identity only) |
| `RESEND_API_KEY` (or `SMTP_HOST` + `MAIL_FROM`) | no | Enables book-announcement emails |
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
