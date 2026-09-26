"""
StayAPI (Google Hotels search) — hotels. PRICE DISCOVERY, link-only.

Google Hotels already compares the big booking sites, so one call returns
the best public rate per property. What it does NOT give us: a point of
sale (prices don't vary by market, only the currency does), cancellation
terms, room names or board — so a `--refundable-only` watch drops every
StayAPI offer, because unknown never counts as refundable.

Quota: the free tier is ~50 calls and every search is one call, so this
provider answers ONLY the home (SA) market. `search.py` still asks it once
per warm market; the other markets get an empty list and cost nothing.
Don't "fix" that by fanning out — 28 identical quotes per scan would spend
the month's allowance in two days.

Env: STAYAPI_KEY   Docs: https://stayapi.com/docs/endpoints/google-hotels/search
"""
import os
import sys
from datetime import date
from urllib.parse import quote_plus

import requests

from core import clock, countries
from providers.base import blank_hotel

NAME = "stayapi"
KIND = "hotel"
BOOKABLE = False

BASE = os.environ.get("STAYAPI_BASE") or "https://api.stayapi.com/v1"
SEARCH_PATH = "/google_hotels/search"

# Google wants a place name, but `hunt.py hotel --city` stores the IATA
# city code the Amadeus provider needs. Codes this project has tracked so
# far; anything else falls back to the stored text title-cased, so
# `--city Lisbon` (stored LISBON) works too.
CITY_NAMES = {
    "LIS": "Lisbon", "OPO": "Porto", "RUH": "Riyadh", "JED": "Jeddah",
    "DMM": "Dammam", "DXB": "Dubai", "AUH": "Abu Dhabi", "DOH": "Doha",
    "LON": "London", "LHR": "London", "PAR": "Paris", "CDG": "Paris",
    "IST": "Istanbul", "BKK": "Bangkok", "KUL": "Kuala Lumpur",
    "SIN": "Singapore", "DPS": "Bali", "ALA": "Almaty", "MOW": "Moscow",
    "SVO": "Moscow", "EDI": "Edinburgh", "PVG": "Shanghai", "SHA": "Shanghai",
    "MAD": "Madrid", "BCN": "Barcelona", "ROM": "Rome", "FCO": "Rome",
    "MIL": "Milan", "AMS": "Amsterdam", "FRA": "Frankfurt", "MUC": "Munich",
    "VIE": "Vienna", "ATH": "Athens", "CAI": "Cairo", "AMM": "Amman",
    "TYO": "Tokyo", "NRT": "Tokyo", "SEL": "Seoul", "ICN": "Seoul",
    "NYC": "New York", "JFK": "New York", "LAX": "Los Angeles",
    "MLE": "Maldives", "CMB": "Colombo", "BOM": "Mumbai", "DEL": "Delhi",
    "HKT": "Phuket", "MNL": "Manila", "SGN": "Ho Chi Minh City",
}


# Booking.com hotel URLs are /hotel/{cc}/{slug}.html — the country code
# comes from the watch's city.
CITY_COUNTRY = {
    "LIS": "pt", "OPO": "pt", "RUH": "sa", "JED": "sa", "DMM": "sa",
    "DXB": "ae", "AUH": "ae", "DOH": "qa", "LON": "gb", "LHR": "gb",
    "EDI": "gb", "PAR": "fr", "CDG": "fr", "IST": "tr", "BKK": "th",
    "HKT": "th", "KUL": "my", "SIN": "sg", "DPS": "id", "ALA": "kz",
    "MOW": "ru", "SVO": "ru", "PVG": "cn", "SHA": "cn", "MAD": "es",
    "BCN": "es", "ROM": "it", "FCO": "it", "MIL": "it", "AMS": "nl",
    "FRA": "de", "MUC": "de", "VIE": "at", "ATH": "gr", "CAI": "eg",
    "AMM": "jo", "TYO": "jp", "NRT": "jp", "SEL": "kr", "ICN": "kr",
    "NYC": "us", "JFK": "us", "LAX": "us", "MLE": "mv", "CMB": "lk",
    "BOM": "in", "DEL": "in", "MNL": "ph", "SGN": "vn",
}


def available() -> bool:
    return bool(os.environ.get("STAYAPI_KEY"))


class QuotaExhausted(RuntimeError):
    """The key is rejected or its allowance is spent (HTTP 401/402/429).
    Every further call this scan would fail the same way, so stop calling
    out — `search._one` checks `exhausted()` before each request."""
    label = "STAYAPI key rejected or quota spent - check STAYAPI_KEY"


_EXHAUSTED = False


def exhausted() -> bool:
    return _EXHAUSTED


def _check_quota(r):
    global _EXHAUSTED
    if r.status_code in (401, 402, 429):
        _EXHAUSTED = True
        raise QuotaExhausted(f"{QuotaExhausted.label} (HTTP {r.status_code})")


def _headers():
    return {"X-API-Key": os.environ["STAYAPI_KEY"],
            "Accept": "application/json"}


# Markets this provider will actually call out for; search._one skips the
# rate-limiter wait (and the call) for any other market.
MARKETS = {countries.HOME}


def debug(msg):
    """STAYAPI_DEBUG=1: one line per HTTP call in the scan log (the
    sandbox can't reach StayAPI, so the CI log is the only way to see
    what the API really returned). Mirrors IGNAV_DEBUG."""
    if os.environ.get("STAYAPI_DEBUG"):
        print(f"[stayapi] {msg}", file=sys.stderr, flush=True)


def problem(r) -> str:
    """Short text for a non-2xx Problem-Details body."""
    try:
        d = r.json() or {}
        return f"{d.get('error_code') or d.get('title')}: {d.get('detail')}"
    except ValueError:
        return (r.text or "")[:200]


def location_name(city) -> str:
    code = (city or "").strip().upper()
    return CITY_NAMES.get(code) or code.title()


def _nights(req) -> int:
    try:
        return max(1, (date.fromisoformat(req["checkout"])
                       - date.fromisoformat(req["checkin"])).days)
    except (KeyError, ValueError, TypeError):
        return 1


def search(req: dict) -> list[dict]:
    """
    req: {city, checkin, checkout, adults, rooms, currency, pos_code,
          residency, refundable_only}
    """
    # Home market only — see the module docstring for why.
    if (req.get("pos_code") or countries.HOME).upper() != countries.HOME:
        return []
    # A one-property watch is priced by the Booking.com provider; a city
    # search here would spend a call on 20 hotels that all get filtered.
    if req.get("hotel"):
        return []

    params = {
        "location": location_name(req["city"]),
        "check_in": req["checkin"],
        "check_out": req["checkout"],
        "adults": min(10, max(1, int(req.get("adults") or 2))),
        "currency": req.get("currency", "SAR"),
    }
    r = requests.get(f"{BASE}{SEARCH_PATH}", headers=_headers(),
                     params=params, timeout=60)
    _check_quota(r)
    if r.status_code >= 400:
        debug(f"google search {params['location']} HTTP {r.status_code} "
              f"{problem(r)}")
    r.raise_for_status()
    data = r.json() or {}
    hotels = data.get("hotels") or []
    debug(f"google search {params['location']} {params['check_in']}: "
          f"HTTP {r.status_code}, {len(hotels)} hotels, "
          f"total_count={data.get('total_count')}")
    out = []
    for h in hotels:
        o = _normalise(h, req, data.get("location") or params["location"])
        if o is not None:
            out.append(o)
    return out


def _normalise(hotel, req, place):
    price = hotel.get("price") or {}
    current = price.get("current")
    if current is None:
        return None            # listed without a rate — nothing to compare
    nights = _nights(req)
    per_night = price.get("price_per_night")
    amount = float(current)
    # The docs' sample shows `current` == `price_per_night` for a 3-night
    # stay, so `current` looks per-night. Scale it to the whole stay so it
    # compares with Amadeus totals and the watch's --target. Verify against
    # the first live run; if `current` turns out to be the stay total this
    # branch simply never fires.
    if per_night and nights > 1 and abs(amount - float(per_night)) < 0.01:
        amount = float(per_night) * nights

    name = hotel.get("name")
    hotel_id = hotel.get("hotel_id")
    loc = hotel.get("location") or {}
    rating = hotel.get("rating") or {}

    o = blank_hotel(NAME, bookable=False)
    o.update({
        "offer_id": hotel_id,
        "amount": amount,
        "currency": price.get("currency") or req.get("currency", "SAR"),
        "hotel_name": name,
        "hotel_id": hotel_id,
        "stars": hotel.get("stars"),
        "lat": loc.get("latitude"),
        "lon": loc.get("longitude"),
        "room_name": None,
        "board": None,
        "free_cancellation": None,
        "cancel_by": None,
        # Google's hotel_id is sometimes the property's own site; otherwise
        # send the user to the Google Hotels page for this search.
        "deep_link": hotel_id if str(hotel_id or "").startswith("http")
        else _google_link(name, place, req),
        "checkin": req["checkin"],
        "checkout": req["checkout"],
        # keep the review score for the detail view; no policy text exists
        "conditions_raw": {"rating": rating.get("value"),
                           "votes": rating.get("votes"),
                           "per_night": per_night, "nights": nights},
        "fetched_at": clock.iso(),
    })
    o["flags"].append("cancellation terms unknown")
    if hotel.get("is_paid"):
        o["flags"].append("sponsored listing")
    return o


def _google_link(name, place, req):
    q = quote_plus(f"{name or ''} {place or ''}".strip())
    return (f"https://www.google.com/travel/search?q={q}"
            f"&checkin={req['checkin']}&checkout={req['checkout']}")
