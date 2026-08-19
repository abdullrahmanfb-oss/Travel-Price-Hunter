"""
Duffel — flights. BOOKABLE (supports hold orders).

Duffel is itself multi-source: it aggregates airline NDC connections plus
GDS content, so one call already spans many carriers. It is the only
source here supporting `type="hold"` — reserve now, pay later.

Env: DUFFEL_TOKEN   Docs: https://duffel.com/docs/api
"""
import os
import requests

from core import clock
from providers.base import blank_flight

NAME = "duffel"
KIND = "flight"
BOOKABLE = True

BASE = "https://api.duffel.com"
VERSION = "v2"
CABIN_MAP = {"economy": "economy", "premium": "premium_economy",
             "business": "business", "first": "first"}


def available() -> bool:
    return bool(os.environ.get("DUFFEL_TOKEN"))


def _headers():
    return {"Authorization": f'Bearer {os.environ["DUFFEL_TOKEN"]}',
            "Duffel-Version": VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json"}


def search(req: dict) -> list[dict]:
    """
    req: {slices:[{origin,destination,date}], adults, cabin, currency,
          pos_code, max_stops}

    1 slice = one-way, 2 = round trip, 3+ = multi-city. Duffel treats all
    three identically — which is why this whole system stores a slice list
    instead of depart/return fields.
    """
    payload = {"data": {
        "slices": [{"origin": s["origin"], "destination": s["destination"],
                    "departure_date": s["date"]} for s in req["slices"]],
        "passengers": [{"type": "adult"} for _ in range(req.get("adults", 1))],
        "cabin_class": CABIN_MAP.get(req.get("cabin", "economy"), "economy"),
    }}
    if req.get("max_stops") == 0:
        payload["data"]["max_connections"] = 0

    r = requests.post(
        f"{BASE}/air/offer_requests?return_offers=true&supplier_timeout=20000",
        headers=_headers(), json=payload, timeout=60)
    r.raise_for_status()
    offers = r.json()["data"].get("offers", [])[:req.get("max_results", 20)]
    return [_normalise(o) for o in offers]


def _normalise(offer):
    out = blank_flight(NAME, bookable=True)
    conds = offer.get("conditions", {}) or {}
    refund = conds.get("refund_before_departure") or {}
    change = conds.get("change_before_departure") or {}

    slices, max_stops = [], 0
    for sl in offer.get("slices", []):
        segs = [{
            "from": s["origin"]["iata_code"],
            "to": s["destination"]["iata_code"],
            "depart": s["departing_at"],
            "arrive": s["arriving_at"],
            "flight": f'{s["marketing_carrier"]["iata_code"]}'
                      f'{s["marketing_carrier_flight_number"]}',
        } for s in sl.get("segments", [])]
        slices.append(segs)
        max_stops = max(max_stops, len(segs) - 1)

    out.update({
        "offer_id": offer["id"],
        "amount": float(offer["total_amount"]),
        "currency": offer["total_currency"],
        "carrier": offer["owner"]["name"],
        "carrier_code": offer["owner"]["iata_code"],
        "stops": max_stops,
        "slices": slices,
        "segments": [s for sl in slices for s in sl],
        "expires_at": offer.get("expires_at"),
        "refundable": refund.get("allowed"),
        "changeable": change.get("allowed"),
        "conditions_raw": conds,
        "fetched_at": clock.iso(),
    })
    return out


def create_hold(offer_id, passengers):
    """No payment, no card. Returns the pay-by deadline."""
    payload = {"data": {"type": "hold", "selected_offers": [offer_id],
                        "passengers": passengers}}
    r = requests.post(f"{BASE}/air/orders", headers=_headers(),
                      json=payload, timeout=60)
    r.raise_for_status()
    d = r.json()["data"]
    pr = d.get("payment_requirements", {}) or {}
    return {"order_id": d["id"],
            "booking_reference": d.get("booking_reference"),
            "amount": float(d["total_amount"]),
            "currency": d["total_currency"],
            "pay_by": pr.get("payment_required_by"),
            "price_guarantee_expires": pr.get("price_guarantee_expires_at")}
