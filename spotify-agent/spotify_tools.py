"""
spotify_tools.py
================
The Spotify integration layer for the agent.

This module knows how to talk to Spotify and nothing about Claude.
`agent_core.py` exposes the functions below to the model as "tools".

Each tool takes an authenticated `spotipy.Spotify` client as its first
argument (`sp`). This keeps the tools stateless and multi-user friendly:
the CLI passes a locally-authenticated client, and the web app passes a
client built from the logged-in visitor's own OAuth token.

Exports:
  - search_spotify_tracks(sp, query, limit)
  - create_spotify_playlist(sp, name, track_ids, description, public)
  - TOOL_SCHEMAS  : the JSON tool definitions Claude reads
  - execute_tool(sp, name, tool_input) : dispatch a tool call by name
"""

from __future__ import annotations


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
def search_spotify_tracks(sp, query: str, limit: int = 10) -> dict:
    """Search Spotify's catalog and return matching tracks.

    Args:
        sp: An authenticated spotipy.Spotify client.
        query: Free-text search, e.g. "chill lo-fi" or "artist:Nujabes".
        limit: Max number of tracks to return (1-50).

    Returns:
        A small, readable dict (query + list of track summaries) that fits
        comfortably back into the model's context.
    """
    limit = max(1, min(int(limit), 50))
    results = sp.search(q=query, type="track", limit=limit)

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
    sp,
    name: str,
    track_ids: list[str],
    description: str = "Created by my Spotify AI agent.",
    public: bool = True,
) -> dict:
    """Create a new playlist for the logged-in user and add tracks to it.

    Args:
        sp: An authenticated spotipy.Spotify client.
        name: The playlist's display name.
        track_ids: Track IDs (or URIs/URLs) to add — typically from a search.
        description: Optional playlist description.
        public: Whether the playlist is publicly visible.

    Returns:
        A dict with the new playlist's id, name, track count, and shareable URL.
    """
    user_id = sp.current_user()["id"]

    playlist = sp.user_playlist_create(
        user=user_id,
        name=name,
        public=public,
        description=description,
    )

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
# Note: `sp` is injected by execute_tool() and is NOT part of the schema.
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

# name -> function lookup used to dispatch tool calls.
_TOOL_FUNCTIONS = {
    "search_spotify_tracks": search_spotify_tracks,
    "create_spotify_playlist": create_spotify_playlist,
}


def execute_tool(sp, name: str, tool_input: dict) -> dict:
    """Run a tool by name, injecting the authenticated Spotify client."""
    if name not in _TOOL_FUNCTIONS:
        raise ValueError(f"Unknown tool: {name}")
    return _TOOL_FUNCTIONS[name](sp, **tool_input)
