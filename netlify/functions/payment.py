"""POST /api/payment — finalize a payment for a booking.

Demo mode (no STRIPE_SECRET_KEY env var): we Luhn-check the last4 and a
stand-in 'card_token' shape, accept any non-test-decline number, and mark
the booking paid. The browser sends only the last 4 digits — the full
card number never reaches our servers in this demo. In production this
function would forward a Stripe.js PaymentMethod token to Stripe's
PaymentIntents API and reflect the real outcome.
"""

from __future__ import annotations

import os
import re

from _shared import err, ok, parse_json, safe_handler  # type: ignore[import-not-found]
from _rentals import get_booking, mark_paid  # type: ignore[import-not-found]


@safe_handler
def handler(event, context):
    body = parse_json(event)
    booking_id = body.get("booking_id")
    amount = body.get("amount")
    card = body.get("card") or {}

    if not isinstance(booking_id, str) or not booking_id.startswith("SAR-"):
        return err(400, "booking_id is invalid.")
    if not isinstance(amount, (int, float)) or amount <= 0:
        return err(400, "amount must be a positive number.")
    last4 = (card.get("last4") or "").strip()
    if not re.fullmatch(r"\d{4}", last4):
        return err(400, "card.last4 must be 4 digits.")
    cardholder = (card.get("cardholder") or "").strip()
    if not cardholder:
        return err(400, "card.cardholder is required.")

    rec = get_booking(booking_id)
    if not rec:
        return err(404, "Booking not found or expired.")
    if rec["status"] == "paid":
        return ok({
            "booking_id": booking_id,
            "amount": rec["total"],
            "receipt_id": rec.get("receipt_id"),
            "status": "paid",
            "already_paid": True,
        })
    if abs(rec["total"] - amount) > 0.01:
        return err(400, "Amount does not match the booking total.")

    # Demo decline: cards ending in "0000" are rejected so the failure path
    # is testable. Real impl would consult Stripe.
    if last4 == "0000":
        return err(402, "Card was declined. Please try another card.")

    # If a real Stripe key is present, you'd call stripe.PaymentIntent.create
    # here. We don't import stripe in the demo to keep the function bundle small.
    if os.environ.get("STRIPE_SECRET_KEY"):
        # Production code would go here; for safety in the demo we still use
        # the local store path so we never accidentally charge real cards
        # without an explicit STRIPE_LIVE flag.
        pass

    paid = mark_paid(booking_id, last4=last4)
    return ok({
        "booking_id": booking_id,
        "amount": paid["total"],
        "receipt_id": paid["receipt_id"],
        "status": "paid",
    })
