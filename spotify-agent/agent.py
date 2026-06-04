"""
agent.py  —  Command-line version of the Spotify AI agent.

Authenticates Spotify locally (browser pops open once, token is cached to
.spotify_token_cache) and runs requests through the shared agent loop.

For the multi-user WEB version, see webapp.py.

Run it:
    python agent.py
    python agent.py "Find me 5 upbeat workout songs"   # one-shot mode
"""

from __future__ import annotations

import json
import os
import sys

import anthropic
import spotipy
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

from agent_core import run_agent

load_dotenv()

_SCOPES = "playlist-modify-public playlist-modify-private"


def local_spotify_client() -> spotipy.Spotify:
    """Build a Spotify client using the local interactive OAuth flow."""
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "Missing Spotify credentials. Set SPOTIFY_CLIENT_ID and "
            "SPOTIFY_CLIENT_SECRET in your .env file."
        )
    return spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
            scope=_SCOPES,
            cache_path=".spotify_token_cache",
            open_browser=True,
        )
    )


def _print_tool(name: str, tool_input: dict) -> None:
    print(f"  [tool] {name}({json.dumps(tool_input)})")


def main() -> None:
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    sp = local_spotify_client()

    # One-shot mode: `python agent.py "your request"`
    if len(sys.argv) > 1:
        request = " ".join(sys.argv[1:])
        print(f"\nYou: {request}")
        print(f"\nAgent: {run_agent(client, sp, request, on_tool=_print_tool)}\n")
        return

    # Interactive mode.
    print("🎵 Spotify AI Agent. Type a request, or 'quit' to exit.\n")
    while True:
        try:
            request = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if request.lower() in {"quit", "exit", "q"}:
            break
        if not request:
            continue
        print(f"\nAgent: {run_agent(client, sp, request, on_tool=_print_tool)}\n")


if __name__ == "__main__":
    main()
