"""
webapp.py  —  Multi-user web version of the Spotify AI agent.

Each visitor logs in with their OWN Spotify account (OAuth redirect flow);
playlists are created on their account. Your Anthropic API key lives on the
server and powers everyone's requests.

Endpoints:
    GET  /            -> the chat UI (static/index.html)
    GET  /login       -> redirect to Spotify's authorization page
    GET  /callback    -> exchange the code, store the token in the session
    GET  /logout      -> clear the session
    GET  /api/me      -> { logged_in, display_name }
    POST /api/chat    -> { reply }  (runs the agent as the logged-in user)

Run locally:
    uvicorn webapp:app --reload --port 8000
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import anthropic
import spotipy
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from spotipy.cache_handler import MemoryCacheHandler
from spotipy.oauth2 import SpotifyOAuth
from starlette.middleware.sessions import SessionMiddleware

from agent_core import run_agent

load_dotenv()

SCOPES = "playlist-modify-public playlist-modify-private"
REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8000/callback")
IS_PROD = os.environ.get("ENV", "").lower() == "production"
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Spotify AI Agent")

# Signed-cookie sessions. SESSION_SECRET MUST be set in production — if it
# changes, everyone is logged out. In prod the cookie is HTTPS-only.
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", "dev-only-insecure-secret-change-me"),
    same_site="lax",
    https_only=IS_PROD,
)

# One shared Anthropic client for the whole server (reads ANTHROPIC_API_KEY).
anthropic_client = anthropic.Anthropic()


class ChatIn(BaseModel):
    message: str


def make_oauth(token_info: dict | None = None) -> SpotifyOAuth:
    """Build a SpotifyOAuth helper. When given a token, it's seeded into an
    in-memory cache so spotipy can auto-refresh it without touching disk."""
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "Missing Spotify credentials. Set SPOTIFY_CLIENT_ID and "
            "SPOTIFY_CLIENT_SECRET in the environment."
        )
    return SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=REDIRECT_URI,
        scope=SCOPES,
        cache_handler=MemoryCacheHandler(token_info=token_info),
        show_dialog=False,
    )


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def landing() -> str:
    """Marketing landing page — the public front door."""
    return (STATIC_DIR / "landing.html").read_text(encoding="utf-8")


@app.get("/app", response_class=HTMLResponse)
def app_page(request: Request):
    """The chat app. Sends visitors to the landing page if not logged in."""
    if not request.session.get("token_info"):
        return RedirectResponse("/")
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Auth (Spotify OAuth Authorization Code flow)
# --------------------------------------------------------------------------- #
@app.get("/login")
def login(request: Request):
    oauth = make_oauth()
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    return RedirectResponse(oauth.get_authorize_url(state=state))


@app.get("/callback")
def callback(request: Request):
    params = request.query_params
    if params.get("error"):
        return RedirectResponse("/?error=" + params["error"])

    code = params.get("code")
    if not code or params.get("state") != request.session.get("oauth_state"):
        raise HTTPException(status_code=400, detail="Invalid OAuth state or missing code.")

    oauth = make_oauth()
    oauth.get_access_token(code, check_cache=False)  # exchanges code -> token, caches it
    token_info = oauth.cache_handler.get_cached_token()
    request.session["token_info"] = token_info

    # Remember a friendly display name for the UI.
    try:
        sp = spotipy.Spotify(auth=token_info["access_token"])
        request.session["display_name"] = sp.current_user().get("display_name")
    except Exception:
        request.session["display_name"] = None

    return RedirectResponse("/app")


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")


@app.get("/api/me")
def me(request: Request) -> dict:
    if not request.session.get("token_info"):
        return {"logged_in": False}
    return {"logged_in": True, "display_name": request.session.get("display_name")}


# --------------------------------------------------------------------------- #
# Chat (runs the agent as the logged-in user)
# --------------------------------------------------------------------------- #
@app.post("/api/chat")
def chat(request: Request, payload: ChatIn) -> dict:
    token_info = request.session.get("token_info")
    if not token_info:
        raise HTTPException(status_code=401, detail="Please log in with Spotify first.")

    message = (payload.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is empty.")

    # Build a Spotify client scoped to THIS user; spotipy auto-refreshes the
    # token via the in-memory cache, which we then persist back to the session.
    oauth = make_oauth(token_info)
    sp = spotipy.Spotify(auth_manager=oauth)
    try:
        reply = run_agent(anthropic_client, sp, message)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}")
    finally:
        refreshed = oauth.cache_handler.get_cached_token()
        if refreshed:
            request.session["token_info"] = refreshed

    return {"reply": reply}
