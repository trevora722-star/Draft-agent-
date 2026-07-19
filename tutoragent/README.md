# TutorAgent 📚

A single-user AI tutoring platform for a first-year science student at Kwantlen Polytechnic
University. Six specialized Claude-powered agents, Socratic by design: a study tool, not a
homework-completion tool.

- **Backend:** Node.js (ESM) + Express, Claude API (`claude-sonnet-4-6`, vision for work photos)
- **Database:** NocoDB via REST API (auto-creates its tables on first boot)
- **Frontend:** one self-contained `public/index.html` — inline CSS + vanilla JS, no build step
- **Email:** Resend weekly digest (Sundays 5pm Pacific, no tracking pixels)
- **Files:** DigitalOcean Spaces (Toronto) for outline PDFs and work photos
- **Residency:** everything stays in Canada; PIPEDA-conscious defaults (private uploads, no third-party analytics)

## The six agents

| Rail name | Agent | What it does |
|---|---|---|
| Set Up My Courses | Onboarding / Syllabus | Phase 1: seeds courses with generic first-year topic sequences. Phase 2: ingests course-outline PDFs → topics by week, exam dates, grading weights, textbook — course flips to `enriched`. |
| Explain It To Me | Concept Coach | Socratic explainer with scaffolds for sig figs, stoichiometry, free-body diagrams, limit intuition, dimensional analysis. |
| Help Me With a Problem | Problem Walker | Never gives the final answer first. One step at a time; photo uploads of handwritten work go to Claude vision and coaching starts at the exact line the attempt went wrong. Graded questions get a parallel example instead. |
| Lab Reports | Lab Report Assistant | Coaches structure, sig figs, uncertainty, graphing conventions. Reviews drafts; never writes submission-ready prose. |
| Quiz Me | Exam Prep | Practice questions, SM-2-lite spaced repetition (1→3→7→14→30 days; <70% resets), study plans weighted by grading weights, weak-point flagging. |
| My Progress | Progress Tracker | Weekly digest data reflected back in chat + Sunday email. |

## Quick start

```bash
cd tutoragent
npm install
cp .env.example .env    # fill in your keys
npm start               # http://localhost:3000
```

Any missing env key logs a clear warning naming the disabled feature — the server still starts.
Without NocoDB configured it falls back to in-memory storage (fine for trying it out; data is
lost on restart). `npm run setup-nocodb` creates the tables explicitly; normal boot does the
same automatically.

Run tests: `npm test` (spaced-repetition ladder, seeding, enrichment, tag protocol, digest data).

## How Phase 1 → Phase 2 works

1. September rolls around. In **Set Up My Courses**, tap 📎 and upload a course outline PDF.
2. The server extracts text (`pdf-parse`), asks Claude for structured JSON (topics by week,
   dated assessments, grading weights, textbook, lab-report format notes), stores the PDF in
   Spaces, and upserts everything into NocoDB. The course's `status` flips to `enriched` and its
   spaced-rep queue reseeds.
3. Each course upgrades independently — uploading one outline never touches the others.
4. Textbooks are recorded by **title/edition only** (tell the agent, or it emits an
   `update_course` tag). Textbook files are never ingested — copyright.

## Architecture notes

- **Agent side effects** use machine-readable tags in the model's reply
  (`<setup_courses>`, `<quiz_result>`, `<update_course>`). The server parses them after each
  turn, applies them to NocoDB, and streams a small "meta" event to the UI; the frontend strips
  the tags from the rendered chat.
- **Conversation state** is in-memory per browser session, per agent — the last ~40 turns are
  sent to Claude each call. Nothing conversational is persisted except the one-line
  `study_sessions` summaries written on "Wrap up this session" (`/api/session/close`).
- **Streaming** is SSE over a POST fetch; the frontend renders markdown (marked) + LaTeX
  (KaTeX) with a plain-text fallback if the CDNs are unreachable.
- **Academic integrity guardrails** are baked into every system prompt and are described to the
  student as part of the product — they cannot be toggled off.

## API

```
POST /api/auth                  passphrase → HTTP-only session cookie
POST /api/agent/:name           chat turn (SSE); server injects live student context
POST /api/upload/outline        PDF → Spaces → extraction → NocoDB enrichment
POST /api/upload/work-photo     image → Spaces → vision input for the next turn
POST /api/session/close         write study_sessions summary, mark topics covered
GET  /api/dashboard             next dates, current topics, due reviews
GET  /api/digest/preview        this week's digest HTML
POST /api/digest/send           send the digest now (manual trigger)
```

## Deploying

**Backend (DigitalOcean Toronto droplet):**

```bash
# on the droplet
git clone <repo> && cd Draft-agent-/tutoragent
npm install --omit=dev
cp .env.example .env && $EDITOR .env
# keep it alive with systemd or pm2:
pm2 start server.js --name tutoragent
```

Put nginx/caddy in front for TLS. The session cookie is `secure` behind HTTPS automatically
(`x-forwarded-proto` aware).

**Frontend:** served statically by Express at `/` — nothing extra needed. To host it on
Netlify instead, deploy `public/` and proxy `/api/*` to the droplet in `netlify.toml`:

```toml
[[redirects]]
  from = "/api/*"
  to = "https://your-droplet-domain/api/:splat"
  status = 200
```
