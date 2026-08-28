"""
Kiwi.com (Tequila) — flights. PRICE DISCOVERY + deep link.

Why this matters for your use case specifically: Kiwi indexes low-cost
carriers that never appear in GDS content, and it builds "virtual
interlining" — self-transfer itineraries stitched from separate tickets.
Those are frequently the cheapest option on RUH→Europe, and the GDS
sources will both miss them entirely.

The tradeoff, stated plainly in the digest: self-transfer means if leg one
is late, leg two is not the airline's problem. `virtual_interline` is
surfaced as a flag so you can decide.

Env: KIWI_API_KEY   Docs: https://tequila.kiwi.com/portal/docs
"""
import os
from datetime import datetime

import requests

from core import clock
from providers.base import blank_flight

NAME = "kiwi"
KIND = "flight"
BOOKABLE = False

BASE = "https://api.tequila.kiwi.com"
CABIN_MAP = {"economy": "M", "premium": "W", "business": "C", "first": "F"}


def available() -> bool:
    return bool(os.environ.get("KIWI_API_KEY"))


def _headers():
    return {"apikey": os.environ["KIWI_API_KEY"]}


def _ddmmyyyy(iso_date):
    return datetime.fromisoformat(iso_date).strftime("%d/%m/%Y")


def search(req: dict) -> list[dict]:
    slices = req["slices"]
    if len(slices) > 2:
        return _multicity(req)

    params = {
        "fly_from": slices[0]["origin"],
        "fly_to": slices[0]["destination"],
        "date_from": _ddmmyyyy(slices[0]["date"]),
        "date_to": _ddmmyyyy(slices[0]["date"]),
        "adults": req.get("adults", 1),
        "selected_cabins": CABIN_MAP.get(req.get("cabin", "economy"), "M"),
        "curr": req.get("currency", "SAR"),
        "limit": req.get("max_results", 20),
        "vehicle_type": "aircraft",
    }
    if len(slices) == 2:
        params["return_from"] = _ddmmyyyy(slices[1]["date"])
        params["return_to"] = _ddmmyyyy(slices[1]["date"])
    if req.get("max_stops") is not None:
        params["max_stopovers"] = req["max_stops"]

    r = requests.get(f"{BASE}/v2/search", headers=_headers(),
                     params=params, timeout=60)
    r.raise_for_status()
    return [_normalise(x, req.get("currency", "SAR"))
            for x in r.json().get("data", [])]


def _multicity(req):
    body = {"requests": [{
        "fly_from": s["origin"], "fly_to": s["destination"],
        "date_from": _ddmmyyyy(s["date"]), "date_to": _ddmmyyyy(s["date"]),
        "adults": req.get("adults", 1),
        "selected_cabins": CABIN_MAP.get(req.get("cabin", "economy"), "M"),
    } for s in req["slices"]]}
    r = requests.post(f"{BASE}/v2/flights_multi",
                      headers={**_headers(), "Content-Type": "application/json"},
                      params={"curr": req.get("currency", "SAR")},
                      json=body, timeout=60)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        data = data.get("data", [])
    return [_normalise(x, req.get("currency", "SAR")) for x in data]


def _normalise(item, currency):
    out = blank_flight(NAME, bookable=False)
    routes = item.get("route", [])
    segs = [{
        "from": s.get("flyFrom"), "to": s.get("flyTo"),
        "depart": s.get("local_departure"), "arrive": s.get("local_arrival"),
        "flight": f'{s.get("airline","")}{s.get("flight_no","")}',
    } for s in routes]

    # Kiwi marks legs that are NOT protected by a single ticket.
    vi = bool(item.get("virtual_interlining")) or \
        len({s.get("airline") for s in routes}) > 1
    flags = ["self-transfer (virtual interline) — missed connection is "
             "not airline-protected"] if vi else []

    out.update({
        "offer_id": item.get("id"),
        "amount": float(item.get("price", 0)),
        "currency": currency,
        "carrier": ", ".join(sorted({s.get("airline", "") for s in routes})),
        "carrier_code": (routes[0].get("airline") if routes else ""),
        "stops": max(0, len(routes) - 1),
        "segments": segs,
        "slices": [segs],
        "deep_link": item.get("deep_link"),
        "virtual_interline": vi,
        "flags": flags,
        "conditions_raw": item.get("conversion", {}),
        "fetched_at": clock.iso(),
    })
    return out
