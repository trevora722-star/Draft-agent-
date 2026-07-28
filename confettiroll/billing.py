"""Billing — packages, Stripe checkout, and webhook parsing.

Everything degrades gracefully: with no STRIPE_SECRET_KEY the packages are
still advertised and checkout endpoints answer {"beta": true} so the UI can
say "free during the beta". With keys set, checkout creates a real Stripe
Checkout Session and the webhook grants event credits / records purchases.

Set:
  STRIPE_SECRET_KEY      sk_live_... / sk_test_...
  STRIPE_WEBHOOK_SECRET  whsec_...   (from the Stripe dashboard webhook)
"""

from __future__ import annotations

import os

try:
    import stripe

    STRIPE_LIB = True
except ImportError:  # pragma: no cover - optional dependency
    STRIPE_LIB = False

# One source of truth for the storefront. Prices in USD cents.
PACKAGES = {
    "starter": {
        "name": "Starter",
        "price_cents": 0,
        "credits": 1,
        "kind": "event",
        "tagline": "Try it with a small gathering",
        "features": [
            "1 event gallery",
            "Up to 100 photos",
            "QR code + password access",
            "3-month gallery life",
        ],
    },
    "celebration": {
        "name": "Celebration",
        "price_cents": 4900,
        "credits": 1,
        "kind": "event",
        "tagline": "Everything for one unforgettable day",
        "features": [
            "Unlimited photos & HD videos",
            "AI captions, search & highlights",
            "Live big-screen slideshow",
            "AI recap + keepsake book PDF",
            "12-month gallery life",
        ],
    },
    "heirloom": {
        "name": "Heirloom",
        "price_cents": 9900,
        "credits": 1,
        "kind": "event",
        "tagline": "The full legacy package",
        "features": [
            "Everything in Celebration",
            "A printed keepsake book included",
            "Your own custom domain",
            "24-month gallery life",
            "Priority support",
        ],
    },
    "wholesale10": {
        "name": "Partner 10-Pack",
        "price_cents": 24500,
        "credits": 10,
        "kind": "wholesale",
        "tagline": "10 Celebration events at ~50% off — bundle them into your packages at your price",
        "features": [
            "10 full Celebration galleries",
            "Resell or bundle at any price",
            "Your clients, your markup",
            "Credits never expire",
        ],
    },
    "printed_book": {
        "name": "Printed Keepsake Book",
        "price_cents": 5900,
        "credits": 0,
        "kind": "book",
        "tagline": "8×8\" hardcover of the whole album, shipped",
        "features": [],
    },
}

VENUE_MONTHLY_CENTS = 7900  # white-label license, billed via subscription


def price_label(key: str) -> str:
    cents = PACKAGES[key]["price_cents"]
    return "Free" if cents == 0 else f"${cents // 100}"


def stripe_enabled() -> bool:
    return STRIPE_LIB and bool(os.environ.get("STRIPE_SECRET_KEY"))


def create_checkout(package_key: str, user_id: int, base_url: str,
                    event_id: str = "") -> str:
    """Create a Stripe Checkout Session; returns its URL."""
    package = PACKAGES[package_key]
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{
            "price_data": {
                "currency": "usd",
                "product_data": {"name": f"ConfettiRoll — {package['name']}"},
                "unit_amount": package["price_cents"],
            },
            "quantity": 1,
        }],
        success_url=f"{base_url}/dashboard?paid=1",
        cancel_url=f"{base_url}/dashboard",
        metadata={
            "user_id": str(user_id),
            "package": package_key,
            "event_id": event_id,
        },
    )
    return session.url


def parse_webhook(payload: bytes, sig_header: str):
    """Verify and parse a Stripe webhook; raises on bad signature."""
    return stripe.Webhook.construct_event(
        payload, sig_header, os.environ["STRIPE_WEBHOOK_SECRET"]
    )
