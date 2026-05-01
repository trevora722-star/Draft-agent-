# Sea-to-Sky ATV Co. — AI Ops Team

A working sketch of a five-agent AI system that runs the back-of-house
of a small ATV rental shop in Squamish, BC. Built to show what an
"AI agentic system" actually looks like for a hands-on outdoor business
— not a chatbot bolted onto a website, but a coordinated team of
specialists, each with their own job.

**Live demo:** open `public/demo.html` in a browser, or deploy the repo
to Netlify (config below) and share the URL.

## The team

| Agent | Role | What it does |
|-------|------|--------------|
| **Cascade** | Booking Concierge | 24/7 replies on Instagram DMs, website chat, SMS, email. Checks availability, sends quotes, collects deposits, sends waivers. |
| **Granite** | Reviews & Reputation | Texts customers post-ride for Google reviews. Drafts owner replies in your voice. Flags 1–3★ reviews fast. |
| **Alder** | Marketing | Weather-aware Instagram posts, Google Ads copy for shoulder seasons, SEO blog posts targeting Squamish-search traffic. |
| **Mamquam** | Operations | Fleet health, engine-hour tracking, maintenance scheduling, guide rostering, 5am daily ops briefing. |
| **Tantalus** | Partnerships | Squamish/Whistler hotel & retreat-planner pipeline. Drafts personalized outreach. Tracks who replied. |

## How they work together

```
┌──────────────── Inputs ────────────────┐
│  Instagram DMs · Website chat · SMS    │
│  Email · Booking system · Google Biz   │
│  Weather + trail conditions            │
└─────────────────────┬──────────────────┘
                      ▼
┌──────────────── Agents ────────────────┐
│  Cascade   Granite   Alder             │
│  Mamquam   Tantalus                    │
│       (shared business state)          │
└─────────────────────┬──────────────────┘
                      ▼
┌──────────────── Outputs ───────────────┐
│  Confirmed bookings · Posted social    │
│  Replied reviews · Sent partner intros │
│  Daily ops brief · Human escalations   │
└────────────────────────────────────────┘
```

The agents share a common picture of the business — bookings, fleet,
customers, partners. When **Cascade** books a ride, **Granite** is
queued to follow up for a review, **Mamquam** assigns a guide and
ATV, and **Alder** has another happy customer to feature. Each agent
escalates to a human when something falls outside its playbook
(unusual weather, a complaint, a corporate request).

## What's in this repo

```
public/demo.html        ← the interactive showcase (open in any browser)
netlify.toml            ← static deploy config
```

The demo page is fully self-contained — every interaction
(chat, post generation, review re-drafting, partner-email rewrite)
runs offline with realistic canned responses, so you can demo it
on a laptop with no Wi-Fi or hand it to the owners as a link.

## Deploying it for your friends

### Option A — Netlify (recommended; free, one click)

1. Push this branch to GitHub.
2. In Netlify: **Add new site → Import an existing project** → pick the repo.
3. Click deploy. You'll get a `*.netlify.app` URL. Send that to them.

There's no API key needed, no build step, no environment variables.

### Option B — Just open the file

```bash
open public/demo.html        # macOS
xdg-open public/demo.html    # Linux
start public/demo.html       # Windows
```

### Option C — GitHub Pages

Push to `main`, enable Pages on the `public/` folder, point at `demo.html`. Done.

## What the demo is (and isn't)

**It is:** a working, clickable sketch that shows the owners exactly
what the system would look like, what each agent does, and what they'd
see day-to-day. The data is made up but realistic — Squamish trail
names, real-feeling bookings, real Squamish-Whistler hotel partners,
weather-aware social copy.

**It isn't:** wired to a real Claude API or their real booking system
— yet. Every canned response in the demo maps cleanly to a real prompt
to a real agent. The path to "live" is well-defined:

| Demo response | Live equivalent |
|---|---|
| Cascade's chat replies | Anthropic SDK + tool use against Checkfront/FareHarbor availability |
| Granite's review drafts | Claude reading the review, given owner-voice few-shot examples |
| Alder's social posts | Claude + a weather API + the shop's content guidelines |
| Mamquam's fleet status | Claude reading from a maintenance log + Google Calendar |
| Tantalus's emails | Claude + a partner CRM (Airtable / HubSpot free) |

## Next steps if the friends say yes

1. **Pick the highest-leverage agent first** — almost always Cascade
   (after-hours bookings = recovered revenue). Wire it to their booking
   system and Instagram DMs. Two-week build.
2. **Add Granite** once Cascade is shipping bookings, because then
   there's a fresh stream of happy customers to ask for reviews.
3. **Alder, Mamquam, Tantalus** layer on over the following month —
   each agent's value compounds with the previous ones.

A polished single agent ships value on day one. Five half-working
agents ship nothing. The demo shows the destination; the build order
matters more than the destination.
