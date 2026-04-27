"""Anthropic client wrapper.

Centralizes:
  - Model defaults (claude-opus-4-7 for drafting, claude-haiku-4-5 for cheap RAG)
  - Adaptive thinking + effort (we want quality over speed for grant work)
  - Prompt caching with stable system prefix → volatile user content layout
  - Streaming for any call with max_tokens > 16K (per SDK guidance)
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable

import anthropic

from .config import get_settings


@lru_cache
def get_client() -> anthropic.Anthropic:
    settings = get_settings()
    if not settings.anthropic_api_key:
        # Don't fail at import time — only when an agent actually tries to call.
        return anthropic.Anthropic(api_key="")
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


@dataclass(frozen=True)
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    stop_reason: str | None


def _join_text_blocks(blocks: Iterable) -> str:
    parts: list[str] = []
    for block in blocks:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "".join(parts)


def complete(
    *,
    system_prompt: str,
    persona: str,
    user_content: str,
    model: str | None = None,
    max_tokens: int = 16_000,
    use_thinking: bool = True,
    effort: str = "high",
) -> LLMResponse:
    """One-shot completion with the standard NPO-agent prompt layout.

    Layout (matches prefix-match invariant for cache hits):
        system = [
            { "text": <stable agent system prompt>, cache_control: ephemeral },
            { "text": <stable per-tenant persona>,  cache_control: ephemeral },
        ]
        messages = [{ "role": "user", "content": <volatile request> }]

    Both system blocks are cacheable; the persona block carries the breakpoint
    so the (system + persona) prefix caches together. Tools render before
    system, so adding tools later won't invalidate this cache.
    """
    settings = get_settings()
    client = get_client()
    model = model or settings.model_default

    # Adaptive thinking is the only on-mode for Opus 4.7. The Haiku fallback
    # uses no thinking — it's used for cheap RAG answering where the model
    # doesn't need to reason hard.
    thinking_param: dict | None = None
    output_config: dict | None = None
    if use_thinking and model.startswith("claude-opus-4-7"):
        thinking_param = {"type": "adaptive"}
        output_config = {"effort": effort}
    elif use_thinking and model.startswith(("claude-opus-4-6", "claude-sonnet-4-6")):
        thinking_param = {"type": "adaptive"}
        output_config = {"effort": effort}

    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [
            {
                "type": "text",
                "text": system_prompt,
            },
            {
                "type": "text",
                "text": f"NPO persona instructions:\n{persona}",
                "cache_control": {"type": "ephemeral"},
            },
        ],
        "messages": [{"role": "user", "content": user_content}],
    }
    if thinking_param is not None:
        kwargs["thinking"] = thinking_param
    if output_config is not None:
        kwargs["output_config"] = output_config

    # Stream when we'd otherwise risk an HTTP timeout — SDK guidance is
    # to stream for max_tokens above ~16K. Use get_final_message() so the
    # rest of the code path is identical to the non-streaming case.
    if max_tokens > 16_000:
        with client.messages.stream(**kwargs) as stream:
            message = stream.get_final_message()
    else:
        message = client.messages.create(**kwargs)

    return LLMResponse(
        text=_join_text_blocks(message.content),
        input_tokens=message.usage.input_tokens,
        output_tokens=message.usage.output_tokens,
        cache_read_tokens=getattr(message.usage, "cache_read_input_tokens", 0) or 0,
        cache_creation_tokens=getattr(message.usage, "cache_creation_input_tokens", 0) or 0,
        stop_reason=message.stop_reason,
    )
