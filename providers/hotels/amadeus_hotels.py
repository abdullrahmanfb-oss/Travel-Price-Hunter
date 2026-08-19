"""
Amadeus Hotel Search — hotels. PRICE DISCOVERY.

Uses the same free Self-Service credentials as the flight provider, so
hotels cost you no extra signup. Two-step: resolve city → hotel IDs, then
price them. IDs are cached because the city list barely changes.

Env: AMADEUS_KEY, AMADEUS_SECRET
"""
import os

import requests

from core import clock
from providers.base import blank_hotel
from providers.flights.amadeus import _auth, BASE

NAME = "amadeus-hotels"
KIND = "hotel"
BOOKABLE = False

_id_cache: dict[str, list[str]] = {}


def available() -> bool:
    return bool(os.environ.get("AMADEUS_KEY")
                and os.environ.get("AMADEUS_SECRET"))


def _hotel_ids(city_code, limit=60):
    if city_code in _id_cache:
        return _id_cache[city_code]
    r = requests.get(f"{BASE}/v1/reference-data/locations/hotels/by-city",
                     headers={"Authorization": f"Bearer {_auth()}"},
                     params={"cityCode": city_code}, timeout=45)
    r.raise_for_status()
    ids = [h["hotelId"] for h in r.json().get("data", [])][:limit]
    _id_cache[city_code] = ids
    return ids


def search(req: dict) -> list[dict]:
    """
    req: {city, checkin, checkout, rooms, adults, currency, stars,
          board, refundable_only}
    """
    ids = _hotel_ids(req["city"])
    if not ids:
        return []

    out = []
    # API caps hotelIds per call; chunk it.
    for i in range(0, len(ids), 20):
        params = {
            "hotelIds": ",".join(ids[i:i + 20]),
            "checkInDate": req["checkin"],
            "checkOutDate": req["checkout"],
            "adults": req.get("adults", 1),
            "roomQuantity": req.get("rooms", 1),
            "currency": req.get("currency", "SAR"),
            "bestRateOnly": "true",
        }
        try:
            r = requests.get(f"{BASE}/v3/shopping/hotel-offers",
                             headers={"Authorization": f"Bearer {_auth()}"},
                             params=params, timeout=60)
            r.raise_for_status()
        except Exception:
            continue
        for entry in r.json().get("data", []):
            out += _normalise(entry, req)
    return out


def _normalise(entry, req):
    hotel = entry.get("hotel", {})
    results = []
    for off in entry.get("offers", []):
        o = blank_hotel(NAME, bookable=False)
        price = off.get("price", {})
        pol = off.get("policies", {}) or {}
        cancel = pol.get("cancellations", [{}])
        free_cancel = any(not c.get("amount") and not c.get("numberOfNights")
                          for c in cancel) if cancel else None
        deadline = next((c.get("deadline") for c in cancel
                         if c.get("deadline")), None)

        room = off.get("room", {}) or {}
        o.update({
            "offer_id": off.get("id"),
            "amount": float(price.get("total", 0)),
            "currency": price.get("currency", req.get("currency", "SAR")),
            "hotel_name": hotel.get("name"),
            "hotel_id": hotel.get("hotelId"),
            "lat": hotel.get("latitude"),
            "lon": hotel.get("longitude"),
            "board": (off.get("boardType") or "").replace("_", " ").title()
                     or None,
            "room_name": (room.get("typeEstimated", {}) or {}).get("category")
                         or room.get("type"),
            "free_cancellation": free_cancel,
            "cancel_by": deadline,
            "checkin": off.get("checkInDate"),
            "checkout": off.get("checkOutDate"),
            "conditions_raw": pol,
            "fetched_at": clock.iso(),
        })
        if free_cancel is False:
            o["flags"].append("non-refundable rate")
        results.append(o)
    return results
