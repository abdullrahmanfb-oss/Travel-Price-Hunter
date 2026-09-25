"""
StayAPI (Booking.com search) — hotels. PRICE DISCOVERY per market.

The per-market hotel source. Booking.com prices by the visitor's country
and StayAPI exposes that as `country_market`, so this provider sends the
scan's point of sale on every call and the same room comes back priced
as a buyer from Egypt, India, Turkey, ... would see it. Link-only: the
digest gives a Booking.com link, never a hold.

Two calls per city: a destination lookup (cached for the process, so
once per scan) then one search per market. Quota: every call is one of
the ~50 free requests, so `MARKETS` (config.yaml `hotels.stayapi_markets`)
caps which markets get a Booking quote — the home market plus a short
list of historically cheap ones, not all 28. Widen it once on a paid plan.

Shares the key, quota flag and city-name map with `stayapi.py` (the
Google Hotels source): one rejected/exhausted key stops both.

Env: STAYAPI_KEY   Docs: https://stayapi.com/docs/endpoints/booking/search
"""
from pathlib import Path

import requests
import yaml

from core import clock, countries
from providers.base import blank_hotel
from providers.hotels import stayapi as _g

NAME = "stayapi-booking"
KIND = "hotel"
BOOKABLE = False

LOOKUP_PATH = "/booking/destinations/lookup"
SEARCH_PATH = "/booking/search"
ROWS = 25

# Markets that get a Booking.com quote. Home first (the gap view needs it
# every scan) plus the cheapest points of sale the flight scans have
# found. Override in config.yaml -> hotels.stayapi_markets.
DEFAULT_MARKETS = ["SA", "EG", "IN", "TR", "AR", "PK"]


def _markets() -> set[str]:
    try:
        cfg = yaml.safe_load((Path(__file__).resolve().parents[2]
                              / "config.yaml").read_text()) or {}
        lst = (cfg.get("hotels") or {}).get("stayapi_markets")
    except Exception:
        lst = None
    codes = {str(c).upper() for c in (lst or DEFAULT_MARKETS)}
    codes.add(countries.HOME)
    return codes


MARKETS = _markets()

_dest_cache: dict[str, tuple] = {}


def available() -> bool:
    return _g.available()


def exhausted() -> bool:
    return _g.exhausted()


def _dest(city) -> tuple | None:
    """(dest_id, dest_type) for a city, one lookup per process."""
    name = _g.location_name(city)
    if name in _dest_cache:
        return _dest_cache[name]
    r = requests.get(f"{_g.BASE}{LOOKUP_PATH}", headers=_g._headers(),
                     params={"query": name}, timeout=45)
    _g._check_quota(r)
    r.raise_for_status()
    d = r.json() or {}
    dest = (d["dest_id"], d.get("dest_type") or "CITY") \
        if d.get("dest_id") else None
    _dest_cache[name] = dest
    return dest


def search(req: dict) -> list[dict]:
    """
    req: {city, checkin, checkout, adults, rooms, currency, pos_code,
          residency, refundable_only}
    """
    pos = (req.get("pos_code") or countries.HOME).upper()
    if pos not in MARKETS:
        return []           # not on the quota allowlist — costs nothing
    dest = _dest(req["city"])
    if not dest:
        return []
    params = {
        "dest_id": dest[0],
        "dest_type": dest[1],
        "checkin": req["checkin"],
        "checkout": req["checkout"],
        "adults": min(10, max(1, int(req.get("adults") or 2))),
        "rooms": min(10, max(1, int(req.get("rooms") or 1))),
        "currency": req.get("currency", "SAR"),
        "country_market": pos,
        "rows_per_page": ROWS,
    }
    r = requests.get(f"{_g.BASE}{SEARCH_PATH}", headers=_g._headers(),
                     params=params, timeout=60)
    _g._check_quota(r)
    r.raise_for_status()
    data = (r.json() or {}).get("data") or {}
    link = data.get("search_url")
    out = []
    for h in data.get("hotels") or []:
        o = _normalise(h, req, pos, link)
        if o is not None:
            out.append(o)
    return out


def _normalise(hotel, req, pos, link):
    price = hotel.get("price") or {}
    amount = price.get("amount")
    if amount is None or hotel.get("is_sold_out"):
        return None
    rating = hotel.get("rating") or {}
    free_cancel = hotel.get("free_cancellation")

    o = blank_hotel(NAME, bookable=False)
    o.update({
        "offer_id": hotel.get("hotel_id"),
        # Booking's result list shows the price for the whole stay, so
        # this compares directly with Amadeus totals and --target.
        "amount": float(amount),
        "currency": price.get("currency") or req.get("currency", "SAR"),
        "hotel_name": hotel.get("name"),
        "hotel_id": hotel.get("hotel_id"),
        "stars": hotel.get("star_rating"),
        "lat": hotel.get("latitude"),
        "lon": hotel.get("longitude"),
        "room_name": hotel.get("room_name"),
        "board": None,
        "free_cancellation": free_cancel,
        "cancel_by": None,
        "deep_link": link,
        "residency": pos.lower(),
        "checkin": req["checkin"],
        "checkout": req["checkout"],
        "conditions_raw": {"score": rating.get("score"),
                           "reviews": rating.get("review_count"),
                           "before_discount": price.get("before_discount"),
                           "country_market": pos},
        "fetched_at": clock.iso(),
    })
    if free_cancel is False:
        o["flags"].append("non-refundable rate")
    return o
