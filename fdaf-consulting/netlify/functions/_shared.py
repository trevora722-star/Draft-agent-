"""Shared helpers for FDAF Consulting Netlify Functions.

- Uniform CORS + JSON response shape.
- Anthropic client construction with a clear error if the key isn't set.
- Optional delivery hooks (webhook + Resend email) so the lead, intake,
  program, and social agents can hand their output off to the trainer.

Env vars (all optional except ANTHROPIC_API_KEY for any LLM-backed function):

  ANTHROPIC_API_KEY         — required for chat / lead / intake / program / social
  FDAF_NOTIFY_EMAIL         — where notifications go (defaults to fdafconsulting@gmail.com)
  FDAF_WEBHOOK_URL          — POSTed JSON for each notification (Zapier/Make/Slack)
  RESEND_API_KEY            — if set, notifications also go out as email via Resend
  RESEND_FROM               — defaults to "FDAF Consulting <onboarding@resend.dev>"
"""

from __future__ import annotations

import json
import os
import traceback
import urllib.request
import urllib.error
from typing import Any

import anthropic

# ---- response helpers ----------------------------------------------------

_CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


def ok(body: dict) -> dict:
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json", **_CORS},
        "body": json.dumps(body),
    }


def err(status: int, message: str) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", **_CORS},
        "body": json.dumps({"error": message}),
    }


def cors_preflight() -> dict:
    return {"statusCode": 204, "headers": _CORS, "body": ""}


def parse_json(event: dict) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64

        raw = base64.b64decode(raw).decode("utf-8")
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return {}


def safe_handler(handler):
    """Wrap a handler with CORS + uniform error reporting."""

    def wrapped(event, context):
        if event.get("httpMethod") == "OPTIONS":
            return cors_preflight()
        try:
            return handler(event, context)
        except Exception as exc:  # pragma: no cover - error path
            traceback.print_exc()
            return err(500, f"{type(exc).__name__}: {exc}")

    return wrapped


# ---- anthropic client ----------------------------------------------------


def anthropic_client():
    """Return an Anthropic client or raise RuntimeError if no key is set."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Server is missing ANTHROPIC_API_KEY. Set it in Netlify → "
            "Site settings → Environment variables and redeploy."
        )
    return anthropic.Anthropic(api_key=api_key)


def extract_text(resp) -> str:
    return "".join(
        block.text
        for block in resp.content
        if getattr(block, "type", None) == "text"
    ).strip()


# ---- delivery hooks ------------------------------------------------------


def notify(subject: str, body: str, payload: dict | None = None) -> dict:
    """Deliver a notification to the trainer.

    Tries every configured channel; failures in one channel don't block the
    others. Returns a dict describing what was attempted.
    """
    results = {"webhook": None, "email": None}

    full_payload = {"subject": subject, "body": body, "data": payload or {}}

    # 1. Webhook (Zapier / Make / Slack / etc.)
    webhook = os.environ.get("FDAF_WEBHOOK_URL")
    if webhook:
        try:
            req = urllib.request.Request(
                webhook,
                data=json.dumps(full_payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                results["webhook"] = r.status
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            results["webhook"] = f"error: {e}"

    # 2. Email via Resend
    resend_key = os.environ.get("RESEND_API_KEY")
    if resend_key:
        to_addr = os.environ.get("FDAF_NOTIFY_EMAIL", "fdafconsulting@gmail.com")
        from_addr = os.environ.get(
            "RESEND_FROM", "FDAF Consulting <onboarding@resend.dev>"
        )
        try:
            req = urllib.request.Request(
                "https://api.resend.com/emails",
                data=json.dumps(
                    {
                        "from": from_addr,
                        "to": [to_addr],
                        "subject": subject,
                        "text": body,
                    }
                ).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {resend_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=8) as r:
                results["email"] = r.status
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            results["email"] = f"error: {e}"

    # 3. Fallback: log to function output (visible in Netlify dashboard)
    if not webhook and not resend_key:
        print(f"[notify] {subject}\n{body}\npayload={json.dumps(payload or {})}")
        results["log"] = "stdout"

    return results
