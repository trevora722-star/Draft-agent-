"""
agent.py
========
A lightweight terminal AI agent that turns natural-language requests into
Spotify actions, using Anthropic's tool-use (function calling).

How it works
------------
1. You type a request, e.g.
     "Create a playlist called 'Smooth Woodturning Beats' with some chill lo-fi tracks."
2. We send it to Claude along with our two tool definitions.
3. Claude decides which tools to call (search, then create), and we run them.
4. The tool results go back to Claude, which loops until it has a final answer.

Run it:
    python agent.py
    python agent.py "Find me 5 upbeat workout songs"   # one-shot mode
"""

from __future__ import annotations

import json
import sys

import anthropic
from dotenv import load_dotenv

from spotify_tools import TOOL_FUNCTIONS, TOOL_SCHEMAS

# Load ANTHROPIC_API_KEY / SPOTIFY_* from the local .env file into the environment.
load_dotenv()

MODEL = "claude-opus-4-8"
MAX_TOKENS = 4096

SYSTEM_PROMPT = (
    "You are a helpful music assistant that manages the user's Spotify account. "
    "When a request needs real tracks, first call search_spotify_tracks to get track "
    "IDs, then call create_spotify_playlist using those IDs. Pick tracks that genuinely "
    "fit the user's described vibe. After acting, reply briefly with what you did and "
    "include the playlist link when you created one."
)


def run_agent(client: anthropic.Anthropic, user_request: str) -> str:
    """Drive one request to completion via a manual tool-use loop.

    The loop keeps calling the model and executing any tools it requests until
    the model stops asking for tools (stop_reason == "end_turn").
    """
    messages: list[dict] = [{"role": "user", "content": user_request}]

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},  # let Claude decide how much to reason
            system=SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            messages=messages,
        )

        # Claude is done — return its final text.
        if response.stop_reason == "end_turn":
            return "".join(b.text for b in response.content if b.type == "text")

        # Otherwise it wants to use one or more tools. Record its turn first.
        messages.append({"role": "assistant", "content": response.content})

        # Execute every requested tool and collect the results.
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            print(f"  [tool] {block.name}({json.dumps(block.input)})")
            try:
                result = TOOL_FUNCTIONS[block.name](**block.input)
                content = json.dumps(result)
                is_error = False
            except Exception as exc:  # surface failures back to the model
                content = f"Error running {block.name}: {exc}"
                is_error = True
                print(f"  [error] {content}")

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                    "is_error": is_error,
                }
            )

        # Send the tool results back as the next user turn and loop again.
        messages.append({"role": "user", "content": tool_results})


def main() -> None:
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment

    # One-shot mode: `python agent.py "your request"`
    if len(sys.argv) > 1:
        request = " ".join(sys.argv[1:])
        print(f"\nYou: {request}")
        print(f"\nAgent: {run_agent(client, request)}\n")
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
        print(f"\nAgent: {run_agent(client, request)}\n")


if __name__ == "__main__":
    main()
