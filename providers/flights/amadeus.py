"""
Amadeus Self-Service — flights. PRICE DISCOVERY (not bookable here).

Why this alongside Duffel: Amadeus is a different GDS with different
airline contracts and different negotiated fares. When Duffel and Amadeus
disagree on the same route, that gap IS the finding — it usually means one
source has content the other doesn't.

Self-Service tier is free and rate-limited. Supports multi-city natively.

Env: AMADEUS_KEY, AMADEUS_SECRET
Docs: https://developers.amadeus.com
"""
import os
import time

import requests

from core import clock
from providers.base import blank_flight

NAME = "amadeus"
KIND = "flight"
BOOKABLE = False          # discovery only; booking needs Enterprise tier

BASE = "https://api.amadeus.com"
CABIN_MAP = {"economy": "ECONOMY", "premium": "PREMIUM_ECONOMY",
             "business": "BUSINESS", "first": "FIRST"}

_token = {"value": None, "expires": 0}


def available() -> bool:
    return bool(os.environ.get("AMADEUS_KEY")
                and os.environ.get("AMADEUS_SECRET"))


def _auth():
    if _token["value"] and time.time() < _token["expires"] - 60:
        return _token["value"]
    r = requests.post(
        f"{BASE}/v1/security/oauth2/token",
        data={"grant_type": "client_credentials",
              "client_id": os.environ["AMADEUS_KEY"],
              "client_secret": os.environ["AMADEUS_SECRET"]},
        timeout=30)
    r.raise_for_status()
    d = r.json()
    _token["value"] = d["access_token"]
    _token["expires"] = time.time() + d.get("expires_in", 1799)
    return _token["value"]


def search(req: dict) -> list[dict]:
    """Uses the POST shopping endpoint — it handles 1..N slices uniformly."""
    dests = [{
        "id": str(i + 1),
        "originLocationCode": s["origin"],
        "destinationLocationCode": s["destination"],
        "departureDateTimeRange": {"date": s["date"]},
    } for i, s in enumerate(req["slices"])]

    body = {
        "currencyCode": req.get("currency", "SAR"),
        "originDestinations": dests,
        "travelers": [{"id": str(i + 1), "travelerType": "ADULT"}
                      for i in range(req.get("adults", 1))],
        "sources": ["GDS"],
        "searchCriteria": {
            "maxFlightOffers": req.get("max_results", 20),
            "flightFilters": {
                "cabinRestrictions": [{
                    "cabin": CABIN_MAP.get(req.get("cabin", "economy"),
                                           "ECONOMY"),
                    "coverage": "MOST_SEGMENTS",
                    "originDestinationIds": [d["id"] for d in dests],
                }],
            },
        },
    }
    if req.get("max_stops") is not None:
        body["searchCriteria"]["flightFilters"]["connectionRestriction"] = {
            "maxNumberOfConnections": req["max_stops"]}

    r = requests.post(f"{BASE}/v2/shopping/flight-offers",
                      headers={"Authorization": f"Bearer {_auth()}",
                               "Content-Type": "application/json"},
                      json=body, timeout=60)
    r.raise_for_status()
    payload = r.json()
    carriers = payload.get("dictionaries", {}).get("carriers", {})
    return [_normalise(o, carriers) for o in payload.get("data", [])]


def _normalise(offer, carriers):
    out = blank_flight(NAME, bookable=False)
    price = offer.get("price", {})

    slices, max_stops = [], 0
    for it in offer.get("itineraries", []):
        segs = [{
            "from": s["departure"]["iataCode"],
            "to": s["arrival"]["iataCode"],
            "depart": s["departure"]["at"],
            "arrive": s["arrival"]["at"],
            "flight": f'{s["carrierCode"]}{s["number"]}',
        } for s in it.get("segments", [])]
        slices.append(segs)
        max_stops = max(max_stops, len(segs) - 1)

    code = offer.get("validatingAirlineCodes", [""])[0]
    fare_rules = offer.get("travelerPricings", [{}])[0] \
        .get("fareDetailsBySegment", [{}])

    out.update({
        "offer_id": offer.get("id"),
        "amount": float(price.get("grandTotal") or price.get("total", 0)),
        "currency": price.get("currency", "SAR"),
        "carrier": carriers.get(code, code),
        "carrier_code": code,
        "stops": max_stops,
        "slices": slices,
        "segments": [s for sl in slices for s in sl],
        "refundable": None,
        "changeable": None,
        "conditions_raw": fare_rules,
        "fetched_at": clock.iso(),
    })
    return out
