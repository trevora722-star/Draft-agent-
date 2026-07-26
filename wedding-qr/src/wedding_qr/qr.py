"""QR code generation for the guest upload link."""

from __future__ import annotations

import io

import qrcode

from .config import get_settings


def guest_upload_url(event_id: str, guest_token: str) -> str:
    settings = get_settings()
    return f"{settings.base_url}/static/index.html?event={event_id}&token={guest_token}"


def dashboard_url(event_id: str, moderator_token: str) -> str:
    settings = get_settings()
    return f"{settings.base_url}/static/dashboard.html?event={event_id}&token={moderator_token}"


def qr_png_bytes(url: str) -> bytes:
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
