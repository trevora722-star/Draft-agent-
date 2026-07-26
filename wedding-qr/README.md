# Wedding Photo QR — agentic guest photo sharing

Guests scan one QR code (on a table card, the invite, or a reception sign),
land on a no-login upload page, and their photos flow through a small pipeline
of agents into a live gallery the couple can watch during the reception — with
a curated "Best Of" highlight reel ready by the time they're back from the
honeymoon.

## Why agents, not just an upload form

A plain upload form gets you a folder of 3,000 unsorted photos, some blurry,
some duplicates, a few that shouldn't be shown on the big screen next to
grandma. The agentic layer is what turns "guests dropped files somewhere"
into "the couple has a live, safe, curated gallery":

1. **Moderation** — screens content so the live gallery (which may be
   projected at the reception) never shows something inappropriate, without
   requiring a human to watch every upload in real time.
2. **Captioning & tagging** — every photo gets a caption, a moment tag
   (ceremony / vows / reception / dancing / cake / candid), and a quality
   score, without asking guests to fill out metadata.
3. **Curation** — reasons over the *whole* collection (not one photo at a
   time) to pick a diverse, non-redundant highlight reel — the one artifact
   the couple actually wants, since nobody is looking at all 3,000 photos.

Each stage is a distinct agent because each reasons over a different scope
(single image vs. whole collection) and a different cost/latency budget —
splitting them keeps the expensive vision call to exactly one per photo.

## Architecture

```
        ┌─────────────┐         printed / projected
        │  QR code    │  ◄───── at each table / on the invite
        └──────┬──────┘
               │ scan
               ▼
   ┌────────────────────────┐
   │ Guest upload page       │  mobile web, no login — capability
   │ (public/upload.html)    │  token in the QR URL scopes to 1 event
   └───────────┬─────────────┘
               │ POST photo + optional name/caption
               ▼
   ┌─────────────────────────────────────────────────────────┐
   │                     FastAPI service                      │
   │                                                            │
   │   store original + thumbnail  ──►  PhotoAnalysisAgent      │
   │                                     (Claude vision, 1 call)│
   │                                     · is_appropriate       │
   │                                     · is_blurry            │
   │                                     · caption + tags       │
   │                                     · moment + quality 1-10│
   │                                          │                  │
   │                     ┌────────────────────┼───────────────┐ │
   │                     ▼                    ▼               │ │
   │              status=approved      status=flagged /       │ │
   │              (public gallery)     rejected (review queue) │ │
   └──────────────────────┬─────────────────────┬─────────────┘ │
                           │                     │                │
                           ▼                     ▼                │
                 ┌──────────────────┐   ┌──────────────────────┐  │
                 │ Live gallery      │   │ Moderator review page │  │
                 │ (public/gallery)  │   │ (public/review.html)  │  │
                 │ guests + couple   │   │ couple / wedding party│  │
                 └──────────────────┘   │ approve / reject      │  │
                                         └──────────────────────┘  │
                                                                    │
                 ┌──────────────────────────────────────────────┐  │
                 │ CurationAgent (on demand or scheduled)         │◄┘
                 │ reasons over ALL approved photos' metadata     │
                 │ (captions/tags/quality/timestamps — no images) │
                 │ → highlight reel: diverse, non-redundant,      │
                 │   ordered narrative (ceremony → reception)     │
                 └──────────────────────┬───────────────────────┘
                                        ▼
                 ┌──────────────────────────────────────────────┐
                 │ NotifierAgent — composes the digest text       │
                 │ ("42 new photos, 3 need a look, here's the     │
                 │  highlight reel") → pluggable to email/SMS      │
                 └──────────────────────────────────────────────┘
```

## Agent roster

| Agent | Trigger | Input scope | Model | Output |
|---|---|---|---|---|
| `PhotoAnalysisAgent` | every upload | one image | Claude vision (Haiku by default — cheap, one call/photo; swap to Sonnet for higher-fidelity moderation) | structured JSON: `is_appropriate`, `is_blurry`, `caption`, `tags[]`, `moment`, `quality_score` |
| `CurationAgent` | on demand (`/digest`) or a schedule | all approved photos' metadata (text only, no re-vision) | Sonnet/Haiku, text-only, cheap | ranked highlight list with a short narrative order |
| `NotifierAgent` | after curation, or after N new uploads | curation output + review-queue count | text composition, no model call required (template) or Haiku for a warmer tone | digest message body, ready to hand to an email/SMS/Slack sender |

Splitting analysis (per-photo, vision) from curation (per-collection, text-only)
means the collection can grow to thousands of photos without re-running vision
on anything — curation only ever reads the metadata each photo already has.

## Moderation policy

Guests should never feel like their photo vanished with no explanation, and
the couple should never have to babysit a moderation queue during their own
reception. The policy balances both:

- **`is_appropriate=true` and `is_blurry=false`** → auto-approved, appears in
  the live gallery immediately. This is the overwhelming majority case.
- **`is_appropriate=false`** (nudity, violence, clearly offensive content) →
  auto-hidden from the public gallery, goes to the review queue. Never
  silently deleted — the couple/wedding party can override a false positive.
- **Uncertain / blurry / low quality** → still stored and visible in an
  "all uploads" admin view, but excluded from the *curated* highlight reel by
  the `CurationAgent` (low `quality_score`), not hard-rejected. A blurry
  photo of the first dance is still a memory worth keeping — it's just not
  reel material.
- The guest-facing response is always a friendly "thanks, it's uploading!" —
  moderation status is never exposed to the uploader, so a false positive
  doesn't turn into an awkward moment at the reception.

## Data model (SQLite, one file per deployment)

```
events  (id, couple_names, event_date, guest_token, moderator_token, created_at)
photos  (id, event_id, guest_name, guest_caption, storage_path, thumbnail_path,
         status[pending|approved|rejected|flagged], ai_caption, ai_tags(json),
         moment, quality_score, is_appropriate, is_blurry, created_at)
```

`guest_token` is the capability embedded in the QR code — anyone with the
link can upload to that event and view the public gallery, but only the
`moderator_token` (given to the couple/wedding party, not printed on the QR
code) can see the review queue or override a moderation decision. No guest
accounts, no PII beyond an optional first name.

## API surface

```
POST   /events                          create a wedding event → {event_id, guest_token, moderator_token}
GET    /events/{event_id}/qr.png        QR code PNG encoding the guest upload URL
POST   /events/{event_id}/photos        guest upload (multipart: file, guest_name?, caption?) — requires guest_token
GET    /events/{event_id}/gallery       approved photos, newest first — requires guest_token
GET    /events/{event_id}/review        flagged/rejected queue — requires moderator_token
POST   /events/{event_id}/photos/{id}/review   {decision: approve|reject} — requires moderator_token
GET    /events/{event_id}/digest        run CurationAgent + NotifierAgent → highlight reel + digest text — requires moderator_token
```

## Pages

- `public/upload.html` — mobile-first, camera-first (`<input capture="environment">`),
  optional name/caption fields, works over the token in the URL query string.
- `public/gallery.html` — polls the gallery endpoint every few seconds; can be
  left open on a laptop/TV at the reception as a live slideshow.
- `public/review.html` — approve/reject buttons for the moderator queue.

## Why this shape (design decisions)

- **One vision call per photo, not per agent.** Moderation and captioning are
  both "look at this image and describe it" — asking the model twice doubles
  cost and latency for no benefit, so `PhotoAnalysisAgent` returns both in one
  structured response.
- **Curation is text-only.** Once every photo has a caption/tag/quality score,
  picking the best 30 out of 1,000 is a text-reasoning problem, not a vision
  problem — this is what keeps re-curation (e.g., "make me a shorter reel")
  cheap enough to re-run on demand.
- **Capability tokens, not accounts.** Wedding guests will not create an
  account to upload three photos. A token in the URL is the right amount of
  auth for "anyone who was physically at the wedding."
- **Never silently reject.** A moderation false positive on someone's photo
  of their kid is a bad guest experience; hiding-but-keeping plus a human
  override queue is the safer default than hard deletion.

## Testing strategy

Same pattern as this repo's other agents: the Claude call is a single
function (`wedding_qr.llm.analyze_photo` / `wedding_qr.llm.curate`) that
tests monkeypatch, so agent logic (status routing, digest assembly, token
scoping) is fully covered offline — no Anthropic calls in CI.

```bash
cd wedding-qr
pip install -e ".[dev]"
pytest -q
```

## Running it

```bash
cd wedding-qr
pip install -e ".[dev]"
export ANTHROPIC_API_KEY=sk-ant-...
uvicorn wedding_qr.api:app --reload --port 8001
```

```bash
# create the event
curl -s -X POST http://localhost:8001/events \
  -H "Content-Type: application/json" \
  -d '{"couple_names":"Alex & Jordan","event_date":"2026-09-12"}'
# → {"event_id": "...", "guest_token": "...", "moderator_token": "..."}

# print/display the QR code (encodes the guest upload URL with guest_token)
open http://localhost:8001/events/<event_id>/qr.png
```

Print the QR code on table cards; keep the `moderator_token` link for the
couple/wedding party only.

## Production hardening checklist

- [ ] Swap local disk storage (`storage.py`) for S3/GCS — interface is already
      an abstraction (`save`, `url_for`) for a drop-in swap
- [ ] Move SQLite to Postgres for concurrent write load during peak reception
      upload bursts
- [ ] Rate-limit uploads per `guest_token` (avoid one phone flooding the queue)
- [ ] Wire `NotifierAgent` output to a real channel (SendGrid/Twilio/Slack
      webhook) instead of returning digest text from the API
- [ ] Add image dedup (perceptual hash) before the vision call to skip
      re-analyzing near-identical burst-mode shots
- [ ] Expiring tokens (auto-disable uploads N days after the event date)
