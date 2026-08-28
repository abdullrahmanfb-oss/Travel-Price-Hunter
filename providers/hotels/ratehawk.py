"""
RateHawk (Emerging Travel Group) — hotels. BOOKABLE, and it exposes
per-market rates directly.

This is the hotel equivalent of a direct-contract flight source: it returns
net rates that genuinely differ by residency/market, and it supports
booking. Requires a partner account (approval usually takes days, not
minutes) — so `available()` will return False until you have one, and the
registry simply skips it.

Env: RATEHAWK_KEY_ID, RATEHAWK_KEY
Docs: https://docs.emergingtravel.com
"""
import os

import requests

from core import clock
from providers.base import blank_hotel

NAME = "ratehawk"
KIND = "hotel"
BOOKABLE = True

BASE = "https://api.worldota.net/api/b2b/v3"


def available() -> bool:
    return bool(os.environ.get("RATEHAWK_KEY_ID")
                and os.environ.get("RATEHAWK_KEY"))


def _auth():
    return (os.environ["RATEHAWK_KEY_ID"], os.environ["RATEHAWK_KEY"])


def search(req: dict) -> list[dict]:
    """
    `residency` is the lever here — the same room genuinely prices
    differently by guest residency, and RateHawk takes it as a parameter
    rather than inferring it from your connection.
    """
    body = {
        "checkin": req["checkin"],
        "checkout": req["checkout"],
        "residency": (req.get("residency") or "sa").lower(),
        "language": "en",
        "guests": [{"adults": req.get("adults", 2),
                    "children": req.get("children", [])}
                   for _ in range(req.get("rooms", 1))],
        "region_id": req.get("region_id"),
        "currency": req.get("currency", "SAR"),
    }
    if not body["region_id"]:
        raise ValueError("ratehawk needs region_id — resolve via "
                         "/search/multicomplete first")

    r = requests.post(f"{BASE}/search/serp/region/", auth=_auth(),
                      json=body, timeout=60)
    r.raise_for_status()
    hotels = r.json().get("data", {}).get("hotels", [])
    out = []
    for h in hotels:
        for rate in h.get("rates", []):
            out.append(_normalise(h, rate, req))
    return out


def _normalise(hotel, rate, req):
    o = blank_hotel(NAME, bookable=True)
    pay = (rate.get("payment_options", {}) or {}) \
        .get("payment_types", [{}])[0]
    cancel = (pay.get("cancellation_penalties", {}) or {})
    free_until = cancel.get("free_cancellation_before")

    o.update({
        "offer_id": rate.get("book_hash"),
        "amount": float(pay.get("amount", 0)),
        "currency": pay.get("currency_code", req.get("currency", "SAR")),
        "hotel_name": hotel.get("id"),
        "hotel_id": hotel.get("id"),
        "stars": hotel.get("star_rating"),
        "board": (rate.get("meal") or "").replace("-", " ").title() or None,
        "room_name": rate.get("room_name"),
        "free_cancellation": bool(free_until),
        "cancel_by": free_until,
        "residency": body_residency(req),
        "checkin": req["checkin"],
        "checkout": req["checkout"],
        "conditions_raw": cancel,
        "fetched_at": clock.iso(),
    })
    if not free_until:
        o["flags"].append("non-refundable rate")
    return o


def body_residency(req):
    return (req.get("residency") or "sa").lower()
