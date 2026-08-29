"""
Ignav — flights. PRICE DISCOVERY + booking link.

Why this provider matters here: Ignav takes a `market` parameter (a
2-letter country code) that returns the market-local fare where one
exists. That is exactly the point-of-sale lever this whole system is
built around, and it maps 1:1 onto the POS codes in config.yaml.

Not bookable: Ignav returns a booking *link*, not a holdable offer, so
BOOKABLE stays False and the digest labels it link-only.

Scope: one-way (1 slice) and round trip (2 slices). Multi-city is not
attempted — see search().

Env:
    IGNAV_TOKEN     required
    IGNAV_BASE      optional, override the API host
    IGNAV_DEBUG     optional, set to 1 to dump the raw response shape

Docs: https://ignav.com/docs
"""
import json
import os
import sys
import time

import requests

from core import clock
from providers.base import blank_flight

NAME = "ignav"
KIND = "flight"
BOOKABLE = False           # returns a booking link, not a holdable order

# Confirmed against the published docs:
#   base            https://ignav.com/api
#   auth            X-Api-Key header
#   one-way         POST /fares/one-way
#   round trip      POST /fares/round-trip
#   booking links   POST /fares/booking-links  {"ignav_id": ...}
#
# Full request-body params per https://ignav.com/docs/search:
#   legs (max TWO, chronological — so multi-city is impossible by design),
#   adults / children / infants_in_seat / infants_on_lap,
#   cabin_class (economy | premium_economy | business | first),
#   min_carry_on_bags / min_checked_bags, max_price (market currency),
#   airlines_include / airlines_exclude,
#   allow_self_transfer (default true — we keep those, flagged),
#   market (2-letter pricing locale, default US).
# There is NO currency param (market decides) and no server-side
# max_stops — stops are filtered client-side in compare.apply_filters.
BASE = os.environ.get("IGNAV_BASE") or "https://ignav.com/api"
ONE_WAY_PATH = "/fares/one-way"
ROUND_TRIP_PATH = "/fares/round-trip"

CABIN_MAP = {"economy": "economy", "premium": "premium_economy",
             "business": "business", "first": "first"}

# Markets Ignav's strict validation rejects outright (HTTP 400 naming
# `market`), confirmed live in run #52. Skip them here instead of
# removing them from config.yaml — other providers may still serve them.
UNSUPPORTED_MARKETS = {"KZ"}


def available() -> bool:
    return bool(os.environ.get("IGNAV_TOKEN"))


def _headers():
    return {"X-Api-Key": os.environ["IGNAV_TOKEN"],
            "Content-Type": "application/json",
            "Accept": "application/json"}


def _first(d, *keys, default=None):
    """Pull the first present key. The response field names are not fully
    pinned down yet (the sandbox cannot reach Ignav to confirm), so accept
    the plausible spellings rather than crashing the whole scan."""
    if not isinstance(d, dict):
        return default
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return default


def search(req: dict) -> list[dict]:
    """
    req: {slices:[{origin,destination,date}], adults, cabin, currency,
          pos_code, max_stops}

    `pos_code` becomes Ignav's `market` — the reason this provider exists.
    """
    slices = req["slices"]
    if len(slices) > 2:
        # Only one-way and round-trip endpoints exist; returning [] lets
        # the scan continue with providers that do support multi-city.
        return []

    # The cabin parameter is `cabin_class` (economy | premium_economy |
    # business | first) — the earlier probe tried `cabin` and was
    # rejected. Returned itineraries stay in the requested bucket per
    # the docs; _normalise double-checks anyway (hard rule 5).
    market = (req.get("pos_code") or "SA").upper()
    if market in UNSUPPORTED_MARKETS:
        return []

    payload = {
        "origin": slices[0]["origin"],
        "destination": slices[0]["destination"],
        "departure_date": slices[0]["date"],
        "market": market,
        "cabin_class": CABIN_MAP.get(req.get("cabin", "economy"),
                                     "economy"),
        # Ignav defaults adults to 1 — send it so a 2-adult watch never
        # silently gets single-passenger pricing.
        "adults": int(req.get("adults") or 1),
    }
    if req.get("airlines"):
        # documented filter (docs/search): only these 2-letter codes
        payload["airlines_include"] = req["airlines"]
    path = ONE_WAY_PATH
    if len(slices) == 2:
        payload["return_date"] = slices[1]["date"]
        path = ROUND_TRIP_PATH
    # max_stops is not sent either — compare.apply_filters() already
    # enforces the stop limit client-side on every offer.

    r = _post_with_retry(f"{BASE}{path}", payload)
    data = r.json()

    if os.environ.get("IGNAV_DEBUG"):
        print(f"[ignav] raw response keys: {_shape(data)}", file=sys.stderr)

    results = _extract(data)
    out = []
    for item in results[:req.get("max_results", 20)]:
        norm = _normalise(item, payload)
        if norm:
            out.append(norm)
    return out


def _shape(obj, depth=0):
    """Compact structural summary — used only for IGNAV_DEBUG output."""
    if depth > 2:
        return "..."
    if isinstance(obj, dict):
        return {k: _shape(v, depth + 1) for k, v in list(obj.items())[:12]}
    if isinstance(obj, list):
        return [_shape(obj[0], depth + 1)] if obj else []
    return type(obj).__name__


def _extract(data):
    """Find the itinerary list wherever the payload puts it."""
    if isinstance(data, list):
        return data
    for key in ("fares", "itineraries", "results", "data", "offers", "flights"):
        val = data.get(key) if isinstance(data, dict) else None
        if isinstance(val, list):
            return val
        if isinstance(val, dict):
            for inner in ("fares", "itineraries", "results", "offers"):
                if isinstance(val.get(inner), list):
                    return val[inner]
    return []


def _legs(item):
    """Round trips nest segments under outbound/inbound leg objects;
    one-ways expose a flat segment list. Return a list of legs, each a
    list of raw segment dicts."""
    legs = []
    for key in ("outbound", "inbound"):
        leg = item.get(key)
        if isinstance(leg, dict):
            legs.append(_first(leg, "segments", "legs", default=[]) or [])
        elif isinstance(leg, list):
            legs.append(leg)
    if legs:
        return legs
    flat = _first(item, "segments", "legs", default=[]) or []
    return [flat] if flat else []


def _segment(s):
    """Live segment shape (run #49):
    marketing_carrier_code, flight_number, operating_carrier_name,
    departure_airport, departure_time_local/_utc, departure_timezone,
    arrival_airport, arrival_time_local/_utc, duration_minutes, aircraft.
    """
    carrier_code = _first(s, "marketing_carrier_code", "carrier_code",
                          "carrier", default="")
    number = _first(s, "flight_number", "number", default="")
    return {
        "from": _first(s, "departure_airport", "origin", default=""),
        "to": _first(s, "arrival_airport", "destination", default=""),
        # local time — it is what the matrix dedupe key compares
        "depart": _first(s, "departure_time_local", "departure_time_utc",
                         default=""),
        "arrive": _first(s, "arrival_time_local", "arrival_time_utc",
                         default=""),
        "flight": f"{carrier_code}{number}".strip(),
    }


def _normalise(item, payload):
    out = blank_flight(NAME, bookable=False)

    # Live shape (run #49): price {amount, currency, status}. The request
    # carries no currency — the market decides it — so the response's own
    # currency is authoritative and an offer without one is unusable.
    price = _first(item, "price", "total_price", "fare", default={})
    if isinstance(price, dict):
        amount = _first(price, "amount", "total", "value")
        currency = _first(price, "currency", "currency_code")
        price_status = _first(price, "status")
    else:
        amount, currency, price_status = price, None, None
    if amount is None or not currency:
        return None

    # Never let one cabin's fare leak into another's baseline (rule 5).
    want = payload.get("cabin_class")
    got = item.get("cabin_class")
    if want and got and got != want:
        return None

    legs = _legs(item)
    segs = [_segment(s) for leg in legs for s in leg]

    stops = _first(item, "stops", "stop_count")
    if stops is None:
        # stops = worst leg, matching how the other providers report it
        stops = max((len(leg) - 1 for leg in legs), default=0)

    # Carrier name lives on the leg object ("outbound": {"carrier": ...}),
    # not at the top level.
    outbound = item.get("outbound")
    carrier_name = (outbound.get("carrier", "")
                    if isinstance(outbound, dict)
                    else _first(item, "carrier", "airline", default=""))
    carrier_code = ""
    if segs:
        carrier_code = "".join(c for c in segs[0]["flight"]
                               if c.isalpha())[:2]

    flags = []
    if price_status and str(price_status).lower() not in ("verified", "ok"):
        flags.append(f"price {price_status}")
    if item.get("requires_self_transfer"):
        flags.append("self-transfer — missed connection is "
                     "not airline-protected")

    out.update({
        # ignav_id is what the booking-links endpoint takes
        "offer_id": str(_first(item, "ignav_id", "id", "fare_id", default="")),
        "amount": float(amount),
        "currency": currency,
        "carrier": carrier_name or carrier_code,
        "carrier_code": (carrier_code or "").upper(),
        "stops": int(stops),
        "segments": segs,
        "slices": [[_segment(s) for s in leg] for leg in legs] or [segs],
        # Search responses carry no inline URL; booking links are a
        # separate call keyed on ignav_id.
        "deep_link": _first(item, "booking_url", "deep_link", "link"),
        "market": payload["market"],
        "flags": flags,
        "conditions_raw": _first(item, "conditions", "fare_rules", default={}),
        "fetched_at": clock.iso(),
    })
    return out


def _post_with_retry(url, payload, attempts=3):
    """Ignav documents no rate limits, but retry a 429/5xx anyway so one
    blip doesn't cost a whole market's quote."""
    delay = 2.0
    for attempt in range(attempts):
        r = requests.post(url, headers=_headers(), json=payload, timeout=60)
        if r.status_code < 500 and r.status_code != 429:
            r.raise_for_status()
            return r
        if attempt == attempts - 1:
            r.raise_for_status()
            return r
        wait = delay
        retry_after = r.headers.get("Retry-After")
        if retry_after:
            try:
                wait = float(retry_after)
            except ValueError:
                pass
        time.sleep(min(wait, 30.0))
        delay *= 2
    raise RuntimeError("unreachable")
