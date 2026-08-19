"""
Amadeus Cars & Transfers — car rental. PRICE DISCOVERY.

Same free credentials as flights and hotels. Car rental pricing varies by
market less dramatically than air, but pickup location matters enormously
— airport counters carry surcharges that downtown branches don't, and the
gap is often 25-40%. `pickup_type` is surfaced so the digest can show it.

Env: AMADEUS_KEY, AMADEUS_SECRET
"""
import os

import requests

from core import clock
from providers.base import blank_car
from providers.flights.amadeus import _auth, BASE

NAME = "amadeus-cars"
KIND = "car"
BOOKABLE = False


def available() -> bool:
    return bool(os.environ.get("AMADEUS_KEY")
                and os.environ.get("AMADEUS_SECRET"))


def search(req: dict) -> list[dict]:
    """
    req: {pickup_lat, pickup_lon, pickup_at, dropoff_at, currency,
          pickup_location (IATA or free text)}
    """
    params = {
        "startLocationCode": req.get("pickup_location"),
        "startDateTime": req["pickup_at"],
        "endDateTime": req["dropoff_at"],
        "currency": req.get("currency", "SAR"),
        "countryCode": req.get("pos_code", "SA"),
    }
    if req.get("pickup_lat"):
        params.update(startGeoCode=f'{req["pickup_lat"]},{req["pickup_lon"]}')

    r = requests.get(f"{BASE}/v1/shopping/transfer-offers",
                     headers={"Authorization": f"Bearer {_auth()}"},
                     params=params, timeout=60)
    r.raise_for_status()
    return [_normalise(x, req) for x in r.json().get("data", [])]


def _normalise(item, req):
    o = blank_car(NAME, bookable=False)
    q = item.get("quotation", {}) or {}
    veh = item.get("vehicle", {}) or {}
    prov = item.get("serviceProvider", {}) or {}
    start = item.get("start", {}) or {}

    loc_code = start.get("locationCode") or ""
    o.update({
        "offer_id": item.get("id"),
        "amount": float(q.get("monetaryAmount", 0) or 0),
        "currency": q.get("currencyCode", req.get("currency", "SAR")),
        "vendor": prov.get("name"),
        "category": veh.get("category") or veh.get("code"),
        "seats": (veh.get("seats", [{}]) or [{}])[0].get("count"),
        "description": veh.get("description"),
        "pickup_type": "airport" if len(loc_code) == 3 else "city",
        "pickup_at": req["pickup_at"],
        "dropoff_at": req["dropoff_at"],
        "fetched_at": clock.iso(),
    })
    return o
