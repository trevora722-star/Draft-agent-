# FDAF Consulting — website + AI chat

Static single-page site for FDAF Consulting (in-home personal training for
couples and small groups in West Kelowna, BC), with a built-in AI chat
assistant powered by Claude.

## What's here

```
fdaf-consulting/
├── index.html                  # the entire website + embedded chat widget
├── netlify.toml                # one-click Netlify deploy config
├── netlify/functions/
│   ├── chat.py                 # /api/chat — Claude-backed intake assistant
│   └── requirements.txt
└── README.md
```

No build step. The whole site is one HTML file plus one serverless function.

## Preview locally (without the chat)

```bash
# from this directory
python3 -m http.server 8000
# then open http://localhost:8000
```

The chat widget will appear, but the network call will fail because the
function isn't running. To run the function locally, use the Netlify CLI:

```bash
npm install -g netlify-cli
netlify dev          # serves the site + functions at localhost:8888
```

Set `ANTHROPIC_API_KEY` in a `.env` file at the repo root (Netlify CLI picks
it up automatically), or export it before running `netlify dev`.

## Deploy (Netlify — recommended)

1. Push this branch to GitHub.
2. In Netlify → **Add new site → Import existing project** → pick the repo.
3. **Site settings → Build & deploy → Base directory** → set to
   `fdaf-consulting`.
4. **Site settings → Environment variables** → add `ANTHROPIC_API_KEY`.
5. Trigger a deploy. Netlify gives you a `*.netlify.app` URL.
6. (Optional) Point `fdafconsulting.ca` (or whatever domain you buy) at
   the Netlify site in **Domain management**.

## The AI chat assistant

A floating "Chat with us" button sits bottom-right on every page load. It
opens a panel that runs on **Claude Haiku 4.5** (cheap, fast, fits inside
Netlify's 10s sync function timeout).

The assistant is briefed via the system prompt in
`netlify/functions/chat.py` to:

- Know the services, pricing, service area, and policies from the site copy.
- Answer in a warm, concise tone that matches the brand.
- **Never invent facts.** If asked something not in its briefing
  (custom pricing, specific availability, certification names), it defers
  to the free 20-minute consult or the contact form.
- Route motivated visitors to: free consult → contact form →
  `hello@fdafconsulting.ca`.
- Avoid medical advice; redirect health concerns to a physician/physio.

Conversation state lives in the browser only — the function is stateless
and gets the recent history with each request (capped at the last 20
turns to keep costs predictable).

### Tuning the assistant

Edit `SYSTEM_PROMPT` in `netlify/functions/chat.py`. Common tweaks:

- **Add real prices** once locked in.
- **Specify certifications** ("Certified through canfitpro / NSCA / …").
- **Add the trainer's first name** so it can introduce her.
- **Set a tighter service area** if travel fees apply.

### Costs

Haiku 4.5 is roughly $1 / $5 per million input / output tokens. A typical
chat exchange (a few hundred tokens each way) is well under a tenth of a
cent. The site also uses prompt caching on the system message, so repeated
hits in a 5-minute window are even cheaper.

## Things to swap before going live

Search `index.html` for these and replace:

| Placeholder                       | Where               | Replace with                     |
|-----------------------------------|---------------------|----------------------------------|
| `hello@fdafconsulting.ca`         | contact + form + chat | Real email address             |
| `(250) 000-0000`                  | contact + footnote  | Real phone number                |
| "Photo coming soon" portrait      | About section       | A real headshot (`<img>`)        |
| `From $95 / $40 / $120`           | Services pricing    | Real prices, or remove           |
| Testimonial block                 | Quote section       | A real client quote (with consent) |
| Certifications line               | About section       | Specific certification names     |

Also update `SYSTEM_PROMPT` in `netlify/functions/chat.py` so the chat
agent's answers match.

## Notes

- Mobile-first, no JS framework, no external CSS. Page weight is ~17 KB
  excluding fonts.
- Fonts load from Google Fonts (Fraunces + Inter).
- The contact form uses `mailto:` so it works without a backend. For a
  production form, swap the `action` to a form service (Formspree, Basin,
  Netlify Forms).
