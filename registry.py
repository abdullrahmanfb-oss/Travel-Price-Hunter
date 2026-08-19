"""
Provider registry — discovery, fan-out, merge, dedupe.

Providers register themselves here. `active(kind)` returns only those with
credentials present, so a missing RateHawk account degrades the system
rather than breaking it.

Dedupe matters once you have 3+ flight sources: Duffel and Amadeus will
often return the same physical flight at slightly different prices. We key
on the itinerary (flight numbers + dates), keep the cheapest, and record
which other sources also saw it — agreement across sources is a signal the
price is real rather than a stale cache entry.
"""
from providers.flights import duffel, amadeus as am_flights, kiwi
from providers.hotels import amadeus_hotels, ratehawk
from providers.cars import amadeus_cars

REGISTRY = {
    "flight": [duffel, am_flights, kiwi],
    "hotel": [amadeus_hotels, ratehawk],
    "car": [amadeus_cars],
}


def active(kind: str) -> list:
    return [p for p in REGISTRY.get(kind, []) if p.available()]


def missing(kind: str) -> list[str]:
    return [p.NAME for p in REGISTRY.get(kind, []) if not p.available()]


def bookable_providers(kind: str) -> list[str]:
    return [p.NAME for p in active(kind) if p.BOOKABLE]


# ---------- dedupe keys ----------

def _flight_key(o):
    segs = o.get("segments") or []
    return tuple((s.get("flight"), (s.get("depart") or "")[:16]) for s in segs)


def _hotel_key(o):
    return (str(o.get("hotel_name") or o.get("hotel_id")).lower().strip(),
            o.get("room_name"), o.get("board"), o.get("checkin"))


def _car_key(o):
    return (str(o.get("vendor") or "").lower(), o.get("category"),
            o.get("pickup_at"))


KEYFN = {"flight": _flight_key, "hotel": _hotel_key, "car": _car_key}


def merge(results: list[dict], kind: str) -> list[dict]:
    """
    Collapse identical products across providers, keeping the cheapest.
    Records `also_seen` and `source_count` — a price confirmed by three
    independent sources is far more trustworthy than a lone outlier.
    """
    keyfn = KEYFN[kind]
    best: dict = {}
    for o in results:
        try:
            k = keyfn(o)
        except Exception:
            k = id(o)
        if not k:
            k = id(o)
        cur = best.get(k)
        if cur is None:
            o = {**o, "also_seen": [], "source_count": 1}
            best[k] = o
            continue
        # same product, different source
        loser, winner = (cur, o) if o["sar_est"] < cur["sar_est"] else (o, cur)
        winner = {**winner}
        winner["also_seen"] = sorted(set(
            cur.get("also_seen", []) + o.get("also_seen", []) +
            [loser["provider"]]))
        winner["source_count"] = cur.get("source_count", 1) + 1
        best[k] = winner
    return list(best.values())
