"""
agent_core.py
=============
The shared agent loop used by BOTH the CLI (`agent.py`) and the web app
(`webapp.py`). It turns a natural-language request into Spotify actions via
Anthropic's tool-use (function calling).

Keeping this in one place means the CLI and the web server behave identically;
they differ only in how they authenticate Spotify and how they display output.
"""

from __future__ import annotations

import json
from typing import Callable, Optional

from spotify_tools import TOOL_SCHEMAS, execute_tool

MODEL = "claude-opus-4-8"
MAX_TOKENS = 4096

SYSTEM_PROMPT = (
    "You are a helpful music assistant that manages the user's Spotify account. "
    "When a request needs real tracks, first call search_spotify_tracks to get track "
    "IDs, then call create_spotify_playlist using those IDs. Pick tracks that genuinely "
    "fit the user's described vibe. After acting, reply briefly with what you did and "
    "include the playlist link when you created one."
)


def run_agent(
    anthropic_client,
    sp,
    user_request: str,
    on_tool: Optional[Callable[[str, dict], None]] = None,
) -> str:
    """Drive one request to completion via a manual tool-use loop.

    Args:
        anthropic_client: An anthropic.Anthropic() instance.
        sp: An authenticated spotipy.Spotify client (the acting user).
        user_request: The natural-language request.
        on_tool: Optional callback(name, input) invoked for each tool call,
            handy for printing progress in the CLI.

    Returns:
        The agent's final natural-language reply.
    """
    messages: list[dict] = [{"role": "user", "content": user_request}]

    while True:
        response = anthropic_client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},  # let Claude decide how much to reason
            system=SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            return "".join(b.text for b in response.content if b.type == "text")

        # Claude wants to use tools. Preserve its full turn (incl. thinking blocks).
        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            if on_tool:
                on_tool(block.name, block.input)
            try:
                result = execute_tool(sp, block.name, block.input)
                content = json.dumps(result)
                is_error = False
            except Exception as exc:  # surface failures back to the model
                content = f"Error running {block.name}: {exc}"
                is_error = True
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                    "is_error": is_error,
                }
            )

        messages.append({"role": "user", "content": tool_results})
