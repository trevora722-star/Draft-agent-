"""Shared rentals module: catalog, pricing, and lightweight booking store.

Mirrors the SKU/price table in public/catalog.js. Bookings live in /tmp on
Lambda (the only writable dir) and persist across warm invocations of the
same instance. A cold start loses them; in production this would be Postgres.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# ---- catalog -------------------------------------------------------------

CATALOG: dict[str, dict[str, Any]] = {
    "mtb-full":     {"name": "Mountain Bike — Full Suspension", "price": 89, "kind": "bike"},
    "ebike":        {"name": "E-Bike",                          "price": 99, "kind": "ebike"},
    "kayak-single": {"name": "Kayak — Single",                  "price": 49, "kind": "kayak"},
    "kayak-tandem": {"name": "Kayak — Tandem",                  "price": 79, "kind": "kayak"},
    "sup":          {"name": "Stand-Up Paddleboard",            "price": 49, "kind": "sup"},
    "canoe":        {"name": "Canoe",                           "price": 69, "kind": "canoe"},
    "gear":         {"name": "Backcountry Gear Bundle",         "price": 39, "kind": "gear"},
}


def days_between(start: str, end: str) -> int:
    """Inclusive-of-start, exclusive-of-end day count, min 1."""
    from datetime import date
    a = date.fromisoformat(start)
    b = date.fromisoformat(end)
    return max(1, (b - a).days)


# ---- quote ---------------------------------------------------------------

@dataclass
class QuoteLine:
    sku: str
    name: str
    qty: int
    price: int   # per-day per-unit, dollars
    line: int    # total for this line, dollars

@dataclass
class Quote:
    days: int
    items: list[QuoteLine]
    subtotal: int
    discount: int
    discount_pct: float
    tax: int
    total: int


def quote(items: list[dict], start: str, end: str) -> Quote:
    """Server-side mirror of booking.js quote(). Returns dollars (ints)."""
    d = days_between(start, end)
    lines: list[QuoteLine] = []
    subtotal = 0
    for it in items:
        sku = it.get("sku")
        qty = int(it.get("qty") or 0)
        if not sku or qty <= 0:
            continue
        if sku not in CATALOG:
            raise ValueError(f"unknown sku: {sku}")
        meta = CATALOG[sku]
        line = qty * meta["price"] * d
        subtotal += line
        lines.append(QuoteLine(sku=sku, name=meta["name"], qty=qty, price=meta["price"], line=line))
    if not lines:
        raise ValueError("no items selected")

    if d >= 7:
        disc_pct = 0.20
    elif d >= 3:
        disc_pct = 0.10
    else:
        disc_pct = 0.0
    discount = round(subtotal * disc_pct)
    taxable = subtotal - discount
    tax = round(taxable * 0.05)
    total = taxable + tax
    return Quote(
        days=d, items=lines,
        subtotal=subtotal, discount=discount, discount_pct=disc_pct,
        tax=tax, total=total,
    )


# ---- booking store -------------------------------------------------------

_STORE_PATH = Path(os.environ.get("SA_STORE_PATH", "/tmp/sa_bookings.json"))


def _load() -> dict:
    if _STORE_PATH.exists():
        try:
            return json.loads(_STORE_PATH.read_text())
        except Exception:
            return {}
    return {}


def _save(store: dict) -> None:
    _STORE_PATH.write_text(json.dumps(store))


def create_booking(*, start: str, end: str, items: list[dict], customer: dict) -> dict:
    q = quote(items, start, end)
    booking_id = "SAR-" + secrets.token_hex(3).upper()
    record = {
        "booking_id": booking_id,
        "start_date": start,
        "end_date": end,
        "days": q.days,
        "items": [asdict(li) for li in q.items],
        "subtotal": q.subtotal,
        "discount": q.discount,
        "discount_pct": q.discount_pct,
        "tax": q.tax,
        "total": q.total,
        "customer": {
            "name": customer.get("name", "")[:120],
            "email": customer.get("email", "")[:120],
            "phone": customer.get("phone", "")[:40],
            "notes": customer.get("notes", "")[:1000],
        },
        "status": "pending_payment",
        "created_at": int(time.time()),
    }
    store = _load()
    store[booking_id] = record
    _save(store)
    return record


def get_booking(booking_id: str) -> dict | None:
    return _load().get(booking_id)


def mark_paid(booking_id: str, *, last4: str) -> dict:
    store = _load()
    rec = store.get(booking_id)
    if not rec:
        raise ValueError("booking not found")
    if rec["status"] == "paid":
        return rec
    rec["status"] = "paid"
    rec["paid_at"] = int(time.time())
    rec["receipt_id"] = "RCPT-" + secrets.token_hex(4).upper()
    rec["card_last4"] = last4
    store[booking_id] = rec
    _save(store)
    return rec
