"""
spotify_tools.py
================
The Spotify integration layer for the agent.

This module is deliberately self-contained: it knows how to talk to Spotify
and nothing about Claude. `agent.py` imports the functions below and exposes
them to the model as "tools" (function calling).

Two pieces live here:
  1. The plain Python functions the agent can call:
       - search_spotify_tracks(query, limit)
       - create_spotify_playlist(name, track_ids, description, public)
  2. TOOL_SCHEMAS — the JSON descriptions Claude reads to decide *when* and
     *how* to call those functions.
"""

from __future__ import annotations

import os
from functools import lru_cache

import spotipy
from spotipy.oauth2 import SpotifyOAuth

# Scopes describe what we're allowed to do on the user's behalf.
# Creating playlists requires both the public and private modify scopes so the
# user can choose either visibility.
_SCOPES = "playlist-modify-public playlist-modify-private"


@lru_cache(maxsize=1)
def _client() -> spotipy.Spotify:
    """Return a cached, authenticated Spotify client.

    We build it lazily (only when a tool is first used) so that simply
    importing this module never triggers a login. On the first real call,
    Spotipy opens a browser for you to authorize the app, then caches the
    token locally (see .spotify_token_cache) so you won't be asked again.
    """
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

    if not client_id or not client_secret:
        raise RuntimeError(
            "Missing Spotify credentials. Set SPOTIFY_CLIENT_ID and "
            "SPOTIFY_CLIENT_SECRET in your .env file."
        )

    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=_SCOPES,
        cache_path=".spotify_token_cache",
        open_browser=True,
    )
    return spotipy.Spotify(auth_manager=auth_manager)


def _normalize_track_id(track: str) -> str:
    """Accept a bare ID, a spotify:track:ID URI, or an open.spotify.com URL
    and return the bare 22-character track ID that the API expects."""
    track = track.strip()
    if track.startswith("spotify:track:"):
        return track.split(":")[-1]
    if "open.spotify.com/track/" in track:
        return track.split("/track/")[-1].split("?")[0]
    return track


# --------------------------------------------------------------------------- #
# Tool 1: search for tracks
# --------------------------------------------------------------------------- #
def search_spotify_tracks(query: str, limit: int = 10) -> dict:
    """Search Spotify's catalog and return matching tracks.

    Args:
        query: Free-text search, e.g. "chill lo-fi" or "artist:Nujabes".
        limit: Max number of tracks to return (1-50).

    Returns:
        A dict with the original query and a list of track summaries, each
        containing id, name, artists, album and uri. Designed to be small and
        readable so it fits comfortably back into the model's context.
    """
    limit = max(1, min(int(limit), 50))
    results = _client().search(q=query, type="track", limit=limit)

    tracks = []
    for item in results.get("tracks", {}).get("items", []):
        tracks.append(
            {
                "id": item["id"],
                "name": item["name"],
                "artists": ", ".join(a["name"] for a in item["artists"]),
                "album": item["album"]["name"],
                "uri": item["uri"],
            }
        )

    return {"query": query, "count": len(tracks), "tracks": tracks}


# --------------------------------------------------------------------------- #
# Tool 2: create a playlist
# --------------------------------------------------------------------------- #
def create_spotify_playlist(
    name: str,
    track_ids: list[str],
    description: str = "Created by my Spotify AI agent.",
    public: bool = True,
) -> dict:
    """Create a new playlist for the logged-in user and add tracks to it.

    Args:
        name: The playlist's display name.
        track_ids: Track IDs (or URIs/URLs) to add — typically taken from a
            prior search_spotify_tracks call.
        description: Optional playlist description.
        public: Whether the playlist is publicly visible.

    Returns:
        A dict with the new playlist's id, name, track count, and shareable URL.
    """
    sp = _client()
    user_id = sp.current_user()["id"]

    playlist = sp.user_playlist_create(
        user=user_id,
        name=name,
        public=public,
        description=description,
    )

    # Convert anything the model passed (IDs, URIs, URLs) into clean URIs.
    uris = [f"spotify:track:{_normalize_track_id(t)}" for t in track_ids if t]
    if uris:
        # The add endpoint accepts at most 100 tracks per call.
        for i in range(0, len(uris), 100):
            sp.playlist_add_items(playlist["id"], uris[i : i + 100])

    return {
        "id": playlist["id"],
        "name": playlist["name"],
        "tracks_added": len(uris),
        "url": playlist["external_urls"]["spotify"],
    }


# --------------------------------------------------------------------------- #
# Tool schemas — how Claude "sees" the functions above.
# The keys here mirror each function's parameters exactly.
# --------------------------------------------------------------------------- #
TOOL_SCHEMAS = [
    {
        "name": "search_spotify_tracks",
        "description": (
            "Search the Spotify catalog for tracks matching a free-text query. "
            "Call this whenever you need real track IDs before building a playlist, "
            "or when the user asks to find/discover songs. Returns a list of tracks "
            "with their IDs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search text, e.g. 'chill lo-fi beats' or 'jazz piano'.",
                },
                "limit": {
                    "type": "integer",
                    "description": "How many tracks to return (1-50). Default 10.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "create_spotify_playlist",
        "description": (
            "Create a new playlist on the logged-in user's account and add the given "
            "tracks to it. Use the track IDs returned by search_spotify_tracks. "
            "Returns the new playlist's shareable URL."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The playlist name, e.g. 'Smooth Woodturning Beats'.",
                },
                "track_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Spotify track IDs to add (from a search call).",
                },
                "description": {
                    "type": "string",
                    "description": "Optional playlist description.",
                },
                "public": {
                    "type": "boolean",
                    "description": "Whether the playlist is public. Default true.",
                },
            },
            "required": ["name", "track_ids"],
        },
    },
]


# A simple name -> function lookup the agent uses to dispatch tool calls.
TOOL_FUNCTIONS = {
    "search_spotify_tracks": search_spotify_tracks,
    "create_spotify_playlist": create_spotify_playlist,
}
