# FDAF Consulting — website + AI agents

In-home personal training website for FDAF Consulting (West Kelowna, BC).
A static site plus five small AI agents, all wired to one Netlify deploy.

## What's here

```
fdaf-consulting/
├── index.html                   # public homepage + chat widget
├── intake.html                  # new-client intake form (noindex)
├── program.html                 # trainer's program generator (noindex)
├── social.html                  # trainer's social-post drafter (noindex)
├── netlify.toml                 # deploy config + routing
├── netlify/functions/
│   ├── _shared.py               # CORS, helpers, optional notify (webhook/email)
│   ├── chat.py                  # /api/chat    — site chatbot
│   ├── lead.py                  # /api/lead    — drafts reply email from contact form
│   ├── intake.py                # /api/intake  — summarizes intake into a brief
│   ├── program.py               # /api/program — 4-week in-home program
│   ├── social.py                # /api/social  — 3 social post drafts
│   └── requirements.txt
└── README.md
```

No build step. Whole site is plain HTML/CSS/JS plus a handful of stateless
Python serverless functions.

## Pages

| URL              | Audience           | Purpose                                              |
|------------------|--------------------|------------------------------------------------------|
| `/`              | Public             | Marketing homepage with the chat widget              |
| `/intake.html`   | New clients        | 5-minute health & goals intake (PAR-Q+ style)        |
| `/program.html`  | **Trainer only**   | Generate a custom 4-week in-home program             |
| `/social.html`   | **Trainer only**   | Generate 3 social post drafts for IG/FB              |

`intake.html`, `program.html`, and `social.html` carry `<meta name="robots"
content="noindex">` so they don't show up in search. There's no hard auth on
the trainer tools — they're just unlinked from the public site. Bookmark
them in the browser.

## The agents

All five run on **Claude Haiku 4.5** (cheap, fast, fits inside Netlify's
sync function ceiling). Each is configured via a system prompt at the top
of its function file — tune in place.

### 1. Site chatbot · `/api/chat`
Floating "Chat with us" widget on every page load. Knows the services,
pricing, service area, and intake flow from the site copy. Routes warm
leads to the free consult / contact form / email. Tuned to defer rather
than invent when asked something it doesn't know.

### 2. Lead-intake autoresponder · `/api/lead`
The contact form on `/` no longer uses `mailto:`. It POSTs to this agent,
which:
1. Drafts a personalized reply email from the trainer's voice.
2. Hands the lead + draft to the trainer via the configured channels
   (see below). She reviews and sends from her Gmail.
3. Returns "Thanks!" to the page.

The lead never sees the draft. This keeps her tone in the loop and
prevents accidental auto-replies to spam.

### 3. Pre-session intake brief · `/api/intake`
`/intake.html` is a single-page intake covering: contact info, PAR-Q+ style
health screening, current habits, goals, equipment, and logistics.
On submit, the agent summarizes it into a one-page brief structured for
a trainer to read in the car before walking in:

- CLIENT
- WHY THEY'RE HERE
- SAFETY FLAGS (flags "PHYSICIAN CLEARANCE RECOMMENDED" when warranted)
- PROGRAMMING NOTES
- LOGISTICS
- FIRST-SESSION SUGGESTION

The brief + raw intake go to the trainer via the configured channels.

### 4. Program generator · `/api/program`
`/program.html` is a trainer-only tool. Punch in a client name, goals,
injuries, equipment, frequency, session length, and length in weeks
(2/4/6). The agent drafts a complete in-home program in Markdown:

- "At a glance" summary + coaching principles
- Week 1 detailed (every day, every set/rep)
- Weeks 2–N progression bullets
- Notes for the client

The page renders the Markdown and shows a **Print / Save as PDF** button
(uses the browser's built-in PDF export — no PDF library needed). A
**Copy text** button is also there for emailing.

### 5. Social post drafter · `/api/social`
`/social.html` is a trainer-only tool. Pick platform (IG / FB / both),
theme, tone, CTA, what to include (hashtags, emoji, hook, local mention),
and a topic. The agent returns **three distinct drafts** — short, medium,
and story-style — each with a one-click copy button.

## Deploy

1. Push this branch to GitHub.
2. In Netlify → **Add new site → Import existing project** → pick the repo.
3. **Site settings → Build & deploy → Base directory** → set to
   `fdaf-consulting`.
4. **Site settings → Environment variables** → add:
   - `ANTHROPIC_API_KEY` (required for all five agents)
   - (Optional) lead/intake delivery — see next section
5. Trigger a deploy.
6. (Optional) Point a domain at the site in **Domain management**.

### Delivering leads & intakes to her Gmail

The agents are stateless — they don't store leads/intakes. They hand them
off via whichever of these channels you configure:

| Env var              | What it does                                                  |
|----------------------|---------------------------------------------------------------|
| `FDAF_WEBHOOK_URL`   | Each notification is POSTed as JSON to this URL.              |
| `RESEND_API_KEY`     | Notifications also sent as email via [Resend](https://resend.com). |
| `FDAF_NOTIFY_EMAIL`  | Where Resend sends to. Defaults to `fdafconsulting@gmail.com`.|
| `RESEND_FROM`        | Override the Resend "from" header. Default is fine.           |

**Easiest path (no Resend account needed):**

1. Create a free Zapier account.
2. Make a new Zap with trigger = **Webhooks by Zapier → Catch Hook**.
   Zapier gives you a URL.
3. Set the Zap's action to **Gmail → Send Email**, with the trainer's
   Gmail as the recipient and the webhook fields as the body.
4. Set `FDAF_WEBHOOK_URL` in Netlify to the Zapier URL. Redeploy.

**Even simpler (one API key, no Zapier):**

1. Sign up at [resend.com](https://resend.com) (free tier: 3,000 emails/mo,
   100/day). No domain verification needed for the free `onboarding@resend.dev`
   sender.
2. Set `RESEND_API_KEY` in Netlify. Redeploy.

If neither is configured, notifications are written to Netlify's function
logs (visible in the dashboard). Nothing is lost, just less convenient.

## Local development

```bash
# Install Netlify CLI (one-time)
npm install -g netlify-cli

# From this directory:
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
netlify dev          # serves the site + functions at localhost:8888
```

For UI-only changes (no agents), `python3 -m http.server 8000` works too.

## Things to swap before going live

Search the codebase for these placeholders:

| Placeholder                       | Where                          | Replace with                     |
|-----------------------------------|--------------------------------|----------------------------------|
| `fdafconsulting@gmail.com`        | index.html, chat.py, README    | Real email once Gmail is claimed |
| `(250) 000-0000`                  | index.html (contact + chat)    | Real phone number                |
| "Photo coming soon" portrait      | index.html About section       | A real headshot (`<img>`)        |
| `From $95 / $40 / $120`           | index.html Services            | Real prices, or remove           |
| Testimonial block                 | index.html Quote section       | A real client quote (with consent) |
| Certifications line               | index.html About + chat.py     | Specific certification names     |

The chat prompt in `chat.py` and the lead/intake/program/social prompts
should match the site copy. Skim them when you change pricing or services.

## Costs

Haiku 4.5 is ~$1 / $5 per million input / output tokens. Realistic per-use:

| Agent          | Avg cost per call |
|----------------|-------------------|
| Chat (one turn)| < $0.001          |
| Lead reply     | ~$0.002           |
| Intake brief   | ~$0.005           |
| Program (4-wk) | ~$0.01            |
| Social (3x)    | ~$0.005           |

A busy month with 100 leads + 50 intakes + 20 programs + 30 social drafts
runs well under $1 in API costs.

## Notes

- Mobile-first, no JS framework, no external CSS.
- Fonts load from Google Fonts (Fraunces + Inter).
- The chatbot conversation lives in the browser only; the function is
  stateless and gets the recent history with each request (capped at 20
  turns).
- The program generator targets ~1500-word output to fit inside the 15s
  function timeout. Output is Markdown, rendered + printable client-side.
