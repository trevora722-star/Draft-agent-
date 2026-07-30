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
        "tagline": "Free for 1 week - unlock once to keep it forever",
        "features": [
            "1 event gallery",
            "Up to 100 photos",
            "QR code + password access",
            "Free 1-week gallery - unlock to keep",
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
            "50% off your first printed book",
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
    "prom": {
        "name": "Prom Night",
        "price_cents": 19900,
        "credits": 1,
        "kind": "event",
        "tagline": "One flat set-up fee for the school — students order their own books",
        "features": [
            "Tagged safety mode: personal student codes",
            "Staff-only uploads (your Vice Principal runs the camera)",
            "Every student sees only their own photos",
            "Personal keepsake book builder for each student",
            "Students order printed books directly — no money through the school",
        ],
    },
}

# Printed keepsake books: priced by cover type and page count (8×8",
# shipping included). Heirloom buyers get 50% off their first book.
BOOK_PRICING = {
    "softcover": {
        "name": "Softcover",
        "blurb": "Flexible matte cover, premium satin pages",
        "tiers": [(40, 3900), (80, 4900), (150, 5900)],
    },
    "hardcover": {
        "name": "Hardcover",
        "blurb": "Rigid wrap cover, thick archival pages - the heirloom",
        "tiers": [(40, 5900), (80, 7900), (150, 9900)],
    },
}


def book_price_cents(cover: str, pages: int) -> int:
    tiers = BOOK_PRICING[cover]["tiers"]
    for limit, cents in tiers:
        if pages <= limit:
            return cents
    return tiers[-1][1]


def book_tier_label(cover: str, pages: int) -> str:
    for limit, _ in BOOK_PRICING[cover]["tiers"]:
        if pages <= limit:
            return f"up to {limit} pages"
    return f"up to {BOOK_PRICING[cover]['tiers'][-1][0]} pages"

# White-label venue license, tiered by hosted event volume (monthly billing).
VENUE_TIERS = {
    "boutique": {
        "name": "Boutique",
        "monthly_cents": 9900,
        "events_per_month": 2,
        "blurb": "Up to 2 hosted events a month — intimate venues and tasting rooms",
    },
    "estate": {
        "name": "Estate",
        "monthly_cents": 19900,
        "events_per_month": 6,
        "blurb": "Up to 6 hosted events a month — busy wedding venues in season",
    },
    "grand": {
        "name": "Grand",
        "monthly_cents": 39900,
        "events_per_month": None,  # unlimited
        "blurb": "Unlimited events — resorts, golf clubs, and multi-room properties",
    },
}


# Promo codes: code -> package granted free (one redemption per account).
# Extend via CR_PROMO_CODES="code:package,code2:package2".
PROMO_CODES = {"armstrong": "celebration"}
for _pair in os.environ.get("CR_PROMO_CODES", "").split(","):
    if ":" in _pair:
        _code, _pkg = _pair.split(":", 1)
        if _pkg.strip() in PACKAGES:
            PROMO_CODES[_code.strip().lower()] = _pkg.strip()


def promo_package(code: str) -> str | None:
    return PROMO_CODES.get(code.strip().lower())


def price_label(key: str) -> str:
    cents = PACKAGES[key]["price_cents"]
    return "Free" if cents == 0 else f"${cents // 100}"


def stripe_enabled() -> bool:
    return STRIPE_LIB and bool(os.environ.get("STRIPE_SECRET_KEY"))


def create_checkout(package_key: str, user_id: int, base_url: str,
                    event_id: str = "", success_url: str | None = None,
                    cancel_url: str | None = None) -> str:
    """Create a Stripe Checkout Session; returns its URL.

    Printed books: the buyer picks how many copies they want (1-20) in a
    single checkout, all shipped to the one address that order collects.
    Books for a different address are simply a separate order.
    """
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
        success_url=success_url or f"{base_url}/dashboard?paid=1",
        cancel_url=cancel_url or f"{base_url}/dashboard",
        metadata={
            "user_id": str(user_id),
            "package": package_key,
            "event_id": event_id,
        },
    )
    return session.url


def create_book_checkout(cover: str, pages: int, price_cents: int, user_id: int,
                         base_url: str, event_id: str,
                         success_url: str | None = None,
                         cancel_url: str | None = None) -> str:
    """Checkout for a printed book: buyer picks 1-20 copies, all shipped to
    the one address this order collects (another address = another order)."""
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    name = (f"Printed Keepsake Book — {BOOK_PRICING[cover]['name']}, "
            f"{book_tier_label(cover, pages)}")
    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{
            "price_data": {
                "currency": "usd",
                "product_data": {"name": f"ConfettiRoll — {name}"},
                "unit_amount": price_cents,
            },
            "quantity": 1,
            "adjustable_quantity": {"enabled": True, "minimum": 1, "maximum": 20},
        }],
        shipping_address_collection={
            "allowed_countries": ["US", "CA", "GB", "IE", "AU", "NZ"],
        },
        success_url=success_url or f"{base_url}/dashboard?paid=1",
        cancel_url=cancel_url or f"{base_url}/dashboard",
        metadata={
            "user_id": str(user_id),
            "package": f"book_{cover}",
            "event_id": event_id,
        },
    )
    return session.url


def parse_webhook(payload: bytes, sig_header: str):
    """Verify and parse a Stripe webhook; raises on bad signature."""
    return stripe.Webhook.construct_event(
        payload, sig_header, os.environ["STRIPE_WEBHOOK_SECRET"]
    )
