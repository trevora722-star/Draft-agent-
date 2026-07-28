"""Sign in with Google (OpenID Connect, server-side authorization code flow).

Identity only, on purpose: the requested scopes are `openid email profile`,
which gives us the person's name and verified email — enough to create their
account and send gallery reminders. We never request Google Photos, Drive,
Gmail, or any other data scope, so there is nothing to accidentally touch.

Degrades gracefully: with no GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET set,
`enabled()` is False and the buttons simply don't render.
"""

from __future__ import annotations

import os
import urllib.parse

import httpx

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
SCOPES = "openid email profile"


def enabled() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID")) and bool(
        os.environ.get("GOOGLE_CLIENT_SECRET")
    )


def auth_url(redirect_uri: str, state: str) -> str:
    params = {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "prompt": "select_account",
    }
    return f"{AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"


def exchange(code: str, redirect_uri: str) -> dict | None:
    """Trade the authorization code for the user's identity.

    Returns {"email": ..., "name": ...} or None on any failure.
    """
    try:
        with httpx.Client(timeout=10) as client:
            token_res = client.post(TOKEN_ENDPOINT, data={
                "code": code,
                "client_id": os.environ["GOOGLE_CLIENT_ID"],
                "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            })
            token_res.raise_for_status()
            access_token = token_res.json().get("access_token")
            if not access_token:
                return None
            info_res = client.get(
                USERINFO_ENDPOINT,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            info_res.raise_for_status()
            info = info_res.json()
    except (httpx.HTTPError, ValueError, KeyError):
        return None
    email = (info.get("email") or "").strip().lower()
    if not email or not info.get("email_verified", True):
        return None
    return {"email": email, "name": (info.get("name") or "").strip()[:80]}
