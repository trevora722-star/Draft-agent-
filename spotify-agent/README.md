# 🎵 Spotify AI Agent

A lightweight terminal AI agent built with the **official Anthropic SDK** and
the **Spotify Web API**. Type a request in plain English and the agent decides
which Spotify actions to take using Claude's tool-use (function calling).

Example:

> "Create a playlist called 'Smooth Woodturning Beats' with some chill lo-fi tracks."

The agent searches Spotify for matching tracks, creates the playlist on your
account, and replies with a shareable link.

---

## What's in here

| File                | Purpose                                                        |
| ------------------- | -------------------------------------------------------------- |
| `agent.py`          | The agent: reads your request, talks to Claude, runs tools.    |
| `spotify_tools.py`  | The two Spotify tools + their schemas (the Claude integration).|
| `requirements.txt`  | Python dependencies.                                           |
| `.env.example`      | Template for your API keys — copy to `.env`.                   |

---

## 1. Environment setup

You need **Python 3.10+**.

```bash
# From inside this folder:

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Create your secrets file
cp .env.example .env             # Windows: copy .env.example .env
```

Then open `.env` and fill in the four values (next two sections explain how to
get them).

---

## 2. Spotify auth setup

Create a (free) app in the **Spotify Developer Dashboard** so the agent can act
on your account:

1. Go to <https://developer.spotify.com/dashboard> and log in.
2. Click **Create app**. Give it any name/description.
3. For **Redirect URI**, add exactly:
   ```
   http://127.0.0.1:8888/callback
   ```
   > ⚠️ Spotify requires a loopback IP (`127.0.0.1`), **not** `localhost`.
   > This must match `SPOTIFY_REDIRECT_URI` in your `.env` character-for-character.
4. Save, then open the app's **Settings** and copy:
   - **Client ID** → `SPOTIFY_CLIENT_ID`
   - **Client Secret** → `SPOTIFY_CLIENT_SECRET`

The agent uses the OAuth **Authorization Code** flow, requesting the
`playlist-modify-public` and `playlist-modify-private` scopes — enough to search
and to create playlists on your behalf. The **first time** you run a tool, a
browser window opens asking you to authorize the app; after that the token is
cached in `.spotify_token_cache` and you won't be asked again.

---

## 3. Anthropic key

Grab an API key from <https://console.anthropic.com/> (Settings → API Keys) and
put it in `.env` as `ANTHROPIC_API_KEY`.

---

## 4. Run it

```bash
# Interactive mode
python agent.py

# One-shot mode
python agent.py "Create a playlist called 'Smooth Woodturning Beats' with some chill lo-fi tracks."
```

You'll see the tool calls the agent makes, then a short summary with your new
playlist link. Open the link in Spotify to hear the result.

---

## How the agent works

1. Your text goes to Claude (`claude-opus-4-8`) along with two tool definitions.
2. Claude calls **`search_spotify_tracks`** to find real track IDs.
3. Claude calls **`create_spotify_playlist`** with those IDs.
4. Each tool result is fed back to Claude, which loops until it's done, then
   replies in plain English.

Adding a new capability is easy: write a Python function in `spotify_tools.py`,
add a matching entry to `TOOL_SCHEMAS`, and register it in `TOOL_FUNCTIONS`.

---

## Sharing this project

This folder is self-contained — zip it and send it to a friend:

```bash
cd ..
zip -r spotify-agent.zip spotify-agent -x "spotify-agent/.venv/*" "spotify-agent/.env" "spotify-agent/.spotify_token_cache"
```

Your friend just needs their own `.env` (their own Anthropic key and their own
Spotify app), then `pip install -r requirements.txt` and `python agent.py`.
Never share your `.env` or token cache — those are your personal secrets.
