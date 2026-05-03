"""POST /api/booking — create a pending-payment booking and return a quote."""

from __future__ import annotations

import re

from _shared import err, ok, parse_json, safe_handler  # type: ignore[import-not-found]
from _rentals import create_booking  # type: ignore[import-not-found]


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@safe_handler
def handler(event, context):
    body = parse_json(event)

    start = body.get("start_date")
    end = body.get("end_date")
    items = body.get("items")
    customer = body.get("customer") or {}

    if not (isinstance(start, str) and _DATE_RE.match(start)):
        return err(400, "start_date must be YYYY-MM-DD.")
    if not (isinstance(end, str) and _DATE_RE.match(end)):
        return err(400, "end_date must be YYYY-MM-DD.")
    if end <= start:
        return err(400, "end_date must be after start_date.")
    if not isinstance(items, list) or not items:
        return err(400, "items must be a non-empty list.")
    if not isinstance(customer, dict):
        return err(400, "customer must be an object.")
    if not (customer.get("name") or "").strip():
        return err(400, "customer.name is required.")
    if not _EMAIL_RE.match((customer.get("email") or "").strip()):
        return err(400, "customer.email is invalid.")
    if not (customer.get("phone") or "").strip():
        return err(400, "customer.phone is required.")

    try:
        record = create_booking(start=start, end=end, items=items, customer=customer)
    except ValueError as e:
        return err(400, str(e))

    return ok(record)
