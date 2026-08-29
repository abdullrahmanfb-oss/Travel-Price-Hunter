"""
Date-model expansion, now slice-aware.

A watch holds a LIST of slices, so one-way, round trip and multi-city are
the same object with 1, 2 or N entries. Flexing a multi-city trip shifts
every slice together, preserving the gaps between cities — shifting them
independently would produce itineraries that make no sense.
"""
import calendar
from datetime import date, datetime, timedelta

from core import clock

MAX_VARIANTS = 40


def _d(s):
    return datetime.fromisoformat(s).date()


def expand(watch) -> list[list[dict]]:
    """Returns a list of slice-sets, one per date variant to search."""
    if watch["product"] != "flight":
        return [[]]
    model = watch["date_model"]
    slices = watch["slices"]
    if model == "fixed":
        return [slices]
    if model == "flex":
        return _flex(slices, watch.get("flex_days") or 0,
                     int(watch.get("length_flex") or 0))
    if model == "month":
        return _month(watch, slices)
    if model == "rolling":
        return _rolling(watch, slices)
    raise ValueError(f"unknown date_model: {model}")


def _shift(slices, days):
    """Move the whole trip, keeping every internal gap identical."""
    return [{**s, "date": (_d(s["date"]) + timedelta(days=days)).isoformat()}
            for s in slices]


def _flex(slices, flex, length_flex=0):
    """Whole-trip shift ±flex days; optionally the LAST slice also moves
    ±length_flex days, so a "10 nights, give or take a day" trip stays
    expressible. This is a CAPPED end-flex ((2·flex+1)·(2·length_flex+1)
    pairs), not the v1 full departure×return cross product — do not widen
    it into one."""
    out, seen = [], set()
    for i in range(-flex, flex + 1):
        shifted = _shift(slices, i)
        if _d(shifted[0]["date"]) < clock.today_date():
            continue
        lengths = range(-length_flex, length_flex + 1) \
            if length_flex and len(shifted) > 1 else (0,)
        for j in lengths:
            # the return moves by i+j in total — keep it inside its own
            # ±flex window, same as the departure
            if abs(i + j) > flex:
                continue
            var = shifted if j == 0 else shifted[:-1] + [
                {**shifted[-1],
                 "date": (_d(shifted[-1]["date"])
                          + timedelta(days=j)).isoformat()}]
            key = tuple(s["date"] for s in var)
            if key in seen or (len(var) > 1 and
                               _d(var[-1]["date"]) <= _d(var[0]["date"])):
                continue
            seen.add(key)
            out.append(var)
    return out[:MAX_VARIANTS]


def _month(watch, slices):
    y, m = map(int, watch["month"].split("-"))
    base = _d(slices[0]["date"])
    out = []
    for day in range(1, calendar.monthrange(y, m)[1] + 1):
        target = date(y, m, day)
        if target < clock.today_date():
            continue
        out.append(_shift(slices, (target - base).days))
    return _thin(out)


def _rolling(watch, slices):
    span = watch.get("rolling_days") or 90
    base = _d(slices[0]["date"])
    out = []
    for i in range(span):
        target = clock.today_date() + timedelta(days=i)
        out.append(_shift(slices, (target - base).days))
    return _thin(out)


def _thin(variants):
    """Sample evenly rather than truncating, so long windows stay covered."""
    if len(variants) <= MAX_VARIANTS:
        return variants
    step = len(variants) / MAX_VARIANTS
    return [variants[int(i * step)] for i in range(MAX_VARIANTS)]


def route_key(watch) -> str:
    if watch["product"] == "hotel":
        return f'HOTEL-{watch["city"]}'
    if watch["product"] == "car":
        return f'CAR-{watch["pickup_location"]}'
    sl = watch["slices"]
    key = "-".join([sl[0]["origin"]] + [s["destination"] for s in sl])
    if watch.get("airlines"):
        # An airline-restricted watch has its own winners and losers —
        # sharing stats with the unrestricted watch on the same route
        # would double the scan counter and prune markets twice as fast.
        key += "+" + str(watch["airlines"]).replace(",", "+")
    return key


def variants(watch) -> list[str]:
    """Price tracks per cabin for flights; a single 'std' track otherwise."""
    if watch["product"] != "flight":
        return ["std"]
    return [c.strip() for c in (watch.get("cabins") or "economy").split(",")
            if c.strip()]


def trip_type(slices) -> str:
    if len(slices) == 1:
        return "oneway"
    if len(slices) == 2 and slices[0]["origin"] == slices[1]["destination"] \
            and slices[0]["destination"] == slices[1]["origin"]:
        return "round"
    return "multi"


def describe(watch) -> str:
    if watch["product"] == "hotel":
        return f'{watch["city"]} {watch["checkin"]}→{watch["checkout"]}'
    if watch["product"] == "car":
        return f'{watch["pickup_location"]} {watch["pickup_at"][:10]}'
    sl = watch["slices"]
    path = sl[0]["origin"] + "".join(f'→{s["destination"]}' for s in sl)
    return f'{path} [{watch["trip_type"]}]'
