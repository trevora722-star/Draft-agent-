# 🎵 Spotify AI Agent

A natural-language agent for Spotify, built with the **official Anthropic SDK**
and the **Spotify Web API**. Ask in plain English and Claude decides which
Spotify actions to take using tool-use (function calling).

Example:

> "Create a playlist called 'Smooth Woodturning Beats' with some chill lo-fi tracks."

It comes in two flavours that share the same agent brain:

- **CLI** (`agent.py`) — runs in your terminal, single user (you).
- **Web app** (`webapp.py`) — a deployable, **multi-user** site where each
  visitor logs in with **their own** Spotify account and gets playlists on it.

---

## What's in here

| File                 | Purpose                                                          |
| -------------------- | --------------------------------------------------------------- |
| `agent_core.py`      | The shared agent loop (Claude + tool-use). Used by both apps.   |
| `spotify_tools.py`   | The two Spotify tools + their schemas.                          |
| `agent.py`           | CLI entrypoint (local Spotify login).                           |
| `webapp.py`          | FastAPI web app (multi-user Spotify OAuth + chat).             |
| `static/index.html`  | The web chat UI.                                                |
| `requirements.txt`   | Python dependencies.                                            |
| `.env.example`       | Template for your keys — copy to `.env`.                        |
| `render.yaml` / `Procfile` | Deployment configs.                                       |

---

## 1. Install

You need **Python 3.10+**.

```bash
cd spotify-agent
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # Windows: copy .env.example .env
```

Then fill in `.env` (next sections explain each value).

---

## 2. Spotify app setup (Developer Dashboard)

1. Go to <https://developer.spotify.com/dashboard> and log in.
2. **Create app** — give it any name/description.
3. Add **Redirect URIs** (you can add several). Add the one(s) you'll use:
   - CLI: `http://127.0.0.1:8888/callback`
   - Web, local: `http://127.0.0.1:8000/callback`
   - Web, deployed: `https://YOUR-APP-DOMAIN/callback`
   > ⚠️ Spotify requires a loopback IP (`127.0.0.1`), **not** `localhost`,
   > and each URI must match `SPOTIFY_REDIRECT_URI` character-for-character.
4. From **Settings**, copy **Client ID** → `SPOTIFY_CLIENT_ID` and
   **Client Secret** → `SPOTIFY_CLIENT_SECRET`.

The agent requests the `playlist-modify-public` and `playlist-modify-private`
scopes — enough to search and create playlists.

> 🔑 **Multi-user note:** a brand-new Spotify app is in **Development Mode**,
> which only lets **up to 25 users you explicitly add** log in. In the
> dashboard go to **Settings → User Management** and add each friend's Spotify
> account email. To open it to anyone, submit a **quota extension request** to
> Spotify.

---

## 3. Anthropic key

Get an API key at <https://console.anthropic.com/> (Settings → API Keys) and put
it in `.env` as `ANTHROPIC_API_KEY`. On the web app, **your** key pays for
everyone's usage — keep it server-side (never in the browser) and watch usage.

---

## 4a. Run the CLI

```bash
python agent.py
python agent.py "Create a playlist called 'Smooth Woodturning Beats' with some chill lo-fi tracks."
```

The first tool call opens a browser to authorize Spotify; the token is cached to
`.spotify_token_cache` so you're only asked once.

## 4b. Run the web app locally

```bash
# Generate a session secret and put it in .env as SESSION_SECRET:
python -c "import secrets; print(secrets.token_urlsafe(32))"

uvicorn webapp:app --reload --port 8000
```

Open <http://127.0.0.1:8000>, you'll see the **landing page**; click
**Log in with Spotify**, then you're taken into the chat app.
(Make sure `http://127.0.0.1:8000/callback` is in your Spotify Redirect URIs and
in `.env` as `SPOTIFY_REDIRECT_URI`.)

**Routes:** `/` is the public landing page · `/app` is the chat UI (redirects to
`/` if you're not logged in) · `/login`, `/callback`, `/logout` handle Spotify
auth · `POST /api/chat` runs the agent.

---

## 5. Put it online for free (so a friend just visits a URL)

**Is it free?** Hosting is free (Render's free tier), Spotify is free. The only
cost is the AI itself: each request calls Claude on *your* Anthropic key — a few
cents each. Keep the user list small and it stays tiny.

### Free deploy on Render (≈5 minutes)

This repo's root has another project's `render.yaml`, so use a **manual web
service** pointed at the `spotify-agent` folder (don't use the Blueprint option):

1. Push this repo to GitHub (your branch is already pushed).
2. Go to <https://render.com>, sign up (free), then **New + → Web Service** and
   connect this GitHub repo.
3. Configure:
   - **Root Directory:** `spotify-agent`
   - **Runtime:** Python
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn webapp:app --host 0.0.0.0 --port $PORT`
   - **Instance Type:** **Free**
4. Under **Environment**, add these variables:
   | Key | Value |
   | --- | --- |
   | `ANTHROPIC_API_KEY` | your Anthropic key |
   | `SPOTIFY_CLIENT_ID` | from the Spotify dashboard |
   | `SPOTIFY_CLIENT_SECRET` | from the Spotify dashboard |
   | `SPOTIFY_REDIRECT_URI` | `https://YOUR-APP.onrender.com/callback` |
   | `SESSION_SECRET` | a long random string (`python -c "import secrets; print(secrets.token_urlsafe(32))"`) |
   | `ENV` | `production` |
5. Click **Create Web Service**. Render gives you a URL like
   `https://YOUR-APP.onrender.com` (you'll know the exact name on this screen —
   use it for `SPOTIFY_REDIRECT_URI` above; update and redeploy if needed).
6. In the **Spotify Dashboard → your app → Settings**, add that exact
   `https://YOUR-APP.onrender.com/callback` to **Redirect URIs**.
7. **Spotify Dashboard → User Management**: add each friend's Spotify account
   email (Development Mode allows up to 25 people).
8. Send your friend the URL. 🎉

> ⏳ **Free-tier note:** Render's free service goes to sleep after ~15 minutes
> idle, so the *first* visit after a quiet spell takes ~30–50s to wake up. After
> that it's fast. Fine for a friends project; upgrade the instance if you want it
> always-on.

Other free-ish hosts (Railway, Fly.io) work the same way — set the same env vars
and run `uvicorn webapp:app --host 0.0.0.0 --port $PORT` (see `Procfile`).

---

## How the agent works

1. Your text goes to Claude (`claude-opus-4-8`, adaptive thinking) with two tool
   definitions.
2. Claude calls **`search_spotify_tracks`** to find real track IDs.
3. Claude calls **`create_spotify_playlist`** with those IDs.
4. Tool results are fed back to Claude, which loops until done, then replies.

The web app builds a **separate** Spotify client per logged-in user from their
own OAuth token (auto-refreshed and kept in their signed session cookie), so
everyone acts on their own account. Adding a capability is easy: write a function
in `spotify_tools.py`, add a matching `TOOL_SCHEMAS` entry, and register it in
`_TOOL_FUNCTIONS`.

> ℹ️ Chat requests are handled independently (no cross-message memory), which
> keeps deployment simple and safe across multiple server workers. Each request
> like "build me a playlist for X" is fully self-contained.

---

## Security notes

- Never commit `.env` or `.spotify_token_cache` (both are git-ignored).
- The session cookie is **signed** (tamper-proof) and HTTPS-only in production;
  it holds each user's Spotify token. Set a strong `SESSION_SECRET`.
- Your Anthropic key funds all web usage — keep the user list small (Development
  Mode's 25-user cap helps) or add your own rate limiting before going public.
