"""Outbound email — book-ready announcements to guests.

Graceful like billing and the AI agents: with no provider configured,
enabled() is False and callers fall back to showing the host the message
and recipient list instead of sending. Configure ONE of:

  RESEND_API_KEY          (simplest — https://resend.com, generous free tier)
  SMTP_HOST + MAIL_FROM   (any SMTP relay; optional SMTP_PORT/SMTP_USER/SMTP_PASS)

MAIL_FROM sets the sender for both, e.g. "ConfettiRoll <hello@confettiroll.com>".
"""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

import httpx


def enabled() -> bool:
    return bool(os.environ.get("RESEND_API_KEY")) or bool(os.environ.get("SMTP_HOST"))


def _sender() -> str:
    return os.environ.get("MAIL_FROM", "ConfettiRoll <hello@confettiroll.com>")


def send(to: str, subject: str, text: str) -> bool:
    """Send one plain-text email; returns True on success, False otherwise."""
    try:
        if os.environ.get("RESEND_API_KEY"):
            res = httpx.post(
                "https://api.resend.com/emails",
                timeout=15,
                headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}"},
                json={"from": _sender(), "to": [to], "subject": subject, "text": text},
            )
            return res.status_code < 300
        if os.environ.get("SMTP_HOST"):
            msg = EmailMessage()
            msg["From"] = _sender()
            msg["To"] = to
            msg["Subject"] = subject
            msg.set_content(text)
            port = int(os.environ.get("SMTP_PORT", "587"))
            with smtplib.SMTP(os.environ["SMTP_HOST"], port, timeout=15) as smtp:
                smtp.starttls()
                if os.environ.get("SMTP_USER"):
                    smtp.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASS", ""))
                smtp.send_message(msg)
            return True
    except Exception:
        return False
    return False
