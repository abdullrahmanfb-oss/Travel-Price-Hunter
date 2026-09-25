"""
StayAPI (Booking.com) — hotels. PRICE DISCOVERY per market.

The per-market hotel source. Booking.com prices by the visitor's country
and StayAPI exposes that as `country_market`, so this provider sends the
scan's point of sale on every call and the same room comes back priced
as a buyer from Egypt, India, Turkey, ... would see it. Link-only: the
digest gives a Booking.com link, never a hold.

Two modes, chosen by the watch:
  city watch   (`--city LIS`)              -> /booking/search per market:
               the cheapest properties in the destination.
  hotel watch  (`--hotel "..." [--room]`)  -> /booking/hotel/prices per
               market: every room type of ONE property with its own
               total, refundability and cancellation deadline. The room
               filter is applied by compare.apply_filters.

Quota: every call is one of the ~50 free requests, so `MARKETS`
(config.yaml `hotels.stayapi_markets`) caps which markets get a Booking
quote — the home market plus a short list of historically cheap ones, not
all 28. Widen it once on a paid plan. The destination lookup is cached
per process; the hotel-id lookup (5-12 s) is stored on the watch by
core.search so it runs once, ever.

Shares the key, quota flag and city maps with `stayapi.py` (the Google
Hotels source): one rejected/exhausted key stops both.

Env: STAYAPI_KEY   Docs: https://stayapi.com/docs/endpoints/booking/search
"""
import re
from datetime import datetime
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
URL_TO_ID_PATH = "/booking/hotel/url-to-id"
PRICES_PATH = "/booking/hotel/prices"
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


def _get(path, params, timeout=60):
    r = requests.get(f"{_g.BASE}{path}", headers=_g._headers(),
                     params=params, timeout=timeout)
    _g._check_quota(r)
    return r


def _dest(city) -> tuple | None:
    """(dest_id, dest_type) for a city, one lookup per process."""
    name = _g.location_name(city)
    if name in _dest_cache:
        return _dest_cache[name]
    r = _get(LOOKUP_PATH, {"query": name}, timeout=45)
    r.raise_for_status()
    d = r.json() or {}
    dest = (d["dest_id"], d.get("dest_type") or "CITY") \
        if d.get("dest_id") else None
    _dest_cache[name] = dest
    return dest


def hotel_slug(name) -> str:
    """'Ibis Styles Lisboa Aeroporto' -> 'ibis-styles-lisboa-aeroporto',
    which is how Booking.com names its hotel pages."""
    return re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")


def resolve_hotel_id(hotel, city) -> str | None:
    """Booking.com numeric id for a property name. Slow (5-12 s) and
    billed — core.search stores the answer on the watch. Returns None
    when Booking has no page under that slug; the user can then pass
    --hotel-id (or the exact slug) instead."""
    cc = _g.CITY_COUNTRY.get((city or "").upper(), "")
    slug = hotel_slug(hotel)
    if not slug:
        return None
    r = _get(URL_TO_ID_PATH, {"url": f"{cc}/{slug}" if cc else slug},
             timeout=90)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    hid = (r.json() or {}).get("hotel_id")
    return str(hid) if hid else None


def search(req: dict) -> list[dict]:
    """
    req: {city, checkin, checkout, adults, rooms, currency, pos_code,
          residency, refundable_only, hotel, room, hotel_id}
    """
    pos = (req.get("pos_code") or countries.HOME).upper()
    if pos not in MARKETS:
        return []           # not on the quota allowlist — costs nothing
    if req.get("hotel"):
        if not req.get("hotel_id"):
            return []       # lookup failed; core.search already logged it
        return _search_hotel(req, pos)
    return _search_city(req, pos)


def _search_city(req, pos):
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
    r = _get(SEARCH_PATH, params)
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


# ---------- one property, every room ----------

def _search_hotel(req, pos):
    params = {
        "hotel_id": req["hotel_id"],
        "check_in": req["checkin"],
        "check_out": req["checkout"],
        "adults": min(10, max(1, int(req.get("adults") or 2))),
        "rooms": min(10, max(1, int(req.get("rooms") or 1))),
        "currency": req.get("currency", "SAR"),
        "country_market": pos,
    }
    r = _get(PRICES_PATH, params)
    r.raise_for_status()
    body = r.json() or {}
    data = body.get("data") or {}
    if data.get("is_soldout"):
        return []
    hotel = data.get("hotel") or {}
    name = hotel.get("name") or req.get("hotel")
    link = _hotel_link(req)
    out = []
    for room in data.get("rooms") or []:
        o = _normalise_room(room, hotel, name, req, pos, link)
        if o is not None:
            out.append(o)
    return out


def _normalise_room(room, hotel, name, req, pos, link):
    total = room.get("total_price_value")
    if total is None:
        return None
    refundable = room.get("is_refundable")
    detail = room.get("cancellation_detail") or room.get("cancellation_policy")

    o = blank_hotel(NAME, bookable=False)
    o.update({
        "offer_id": room.get("block_id"),
        "amount": float(total),
        "currency": room.get("currency") or req.get("currency", "SAR"),
        "hotel_name": name,
        "hotel_id": str(hotel.get("id") or req["hotel_id"]),
        "stars": None,
        "room_name": room.get("room_name"),
        "board": room.get("meal_plan")
                 or ("Breakfast Included" if room.get("breakfast_included")
                     else None),
        "free_cancellation": refundable,
        "cancel_by": _cancel_by(detail),
        "deep_link": link,
        "residency": pos.lower(),
        "checkin": req["checkin"],
        "checkout": req["checkout"],
        "conditions_raw": {"cancellation": detail,
                           "prepayment": room.get("prepayment"),
                           "per_night": room.get("price_per_night_value"),
                           "country_market": pos},
        "fetched_at": clock.iso(),
    })
    if refundable is False:
        o["flags"].append("non-refundable rate")
    return o


def _cancel_by(detail):
    """'Free cancellation until March 28, 2026' -> '2026-03-28' (the digest
    prints the first 10 characters, so it must be ISO to be readable)."""
    m = re.search(r"until\s+(.+?)\s*$", str(detail or ""))
    if not m:
        return None
    txt = m.group(1).strip().rstrip(".")
    for fmt in ("%B %d, %Y", "%d %B %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(txt, fmt).date().isoformat()
        except ValueError:
            continue
    return txt


def _hotel_link(req):
    cc = _g.CITY_COUNTRY.get((req.get("city") or "").upper(), "")
    slug = hotel_slug(req.get("hotel"))
    base = f"https://www.booking.com/hotel/{cc}/{slug}.html" if cc \
        else f"https://www.booking.com/hotel/{slug}.html"
    return (f"{base}?checkin={req['checkin']}&checkout={req['checkout']}"
            f"&group_adults={req.get('adults') or 2}"
            f"&no_rooms={req.get('rooms') or 1}")
