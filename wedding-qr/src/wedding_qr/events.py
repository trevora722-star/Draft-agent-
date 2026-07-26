from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass

from .db import connect


@dataclass(frozen=True)
class Event:
    id: str
    couple_names: str
    event_date: str | None
    guest_token: str
    moderator_token: str


def create_event(couple_names: str, event_date: str | None = None) -> Event:
    event = Event(
        id=uuid.uuid4().hex,
        couple_names=couple_names,
        event_date=event_date,
        guest_token=secrets.token_urlsafe(16),
        moderator_token=secrets.token_urlsafe(16),
    )
    with connect() as conn:
        conn.execute(
            "INSERT INTO events (id, couple_names, event_date, guest_token, moderator_token) "
            "VALUES (?, ?, ?, ?, ?)",
            (event.id, event.couple_names, event.event_date, event.guest_token, event.moderator_token),
        )
    return event


def get_event(event_id: str) -> Event | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    if row is None:
        return None
    return Event(
        id=row["id"],
        couple_names=row["couple_names"],
        event_date=row["event_date"],
        guest_token=row["guest_token"],
        moderator_token=row["moderator_token"],
    )


def check_guest_token(event: Event, token: str) -> bool:
    return secrets.compare_digest(event.guest_token, token)


def check_moderator_token(event: Event, token: str) -> bool:
    return secrets.compare_digest(event.moderator_token, token)
