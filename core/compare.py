"""
Normalise to SAR, filter, guard, detect real drops.

Every product gets its own drop threshold because they move differently:
air fares swing daily, hotel rates drift, car rental is nearly flat until
inventory tightens. One threshold across all three produces either spam or
silence.
"""
import re
import statistics

RESTRICTION_PATTERNS = [
    r"resident", r"residency", r"national of", r"citizen",
    r"point of sale", r"sold only in", r"local(?:ly)? issued",
    r"proof of address", r"domestic market", r"tour(?:ist)? package",
]
_RX = re.compile("|".join(RESTRICTION_PATTERNS), re.I)

MIN_DROP_PCT = {
    "economy": 8.0, "premium": 9.0, "business": 12.0, "first": 12.0,
    "std": 7.0,          # hotels and cars
}


def to_sar(amount, currency, rates):
    if currency == "SAR":
        return round(amount, 2)
    if currency not in rates:
        raise ValueError(f"no FX rate for {currency}")
    return round(amount * rates[currency], 2)


def apply_filters(offers, watch):
    product = watch["product"]
    out = []
    excl = {c.strip().upper() for c in (watch.get("exclude") or "").split(",")
            if c.strip()}
    for o in offers:
        if product == "flight":
            if watch.get("max_stops") is not None \
                    and (o.get("stops") or 0) > watch["max_stops"]:
                continue
            if o.get("carrier_code", "").upper() in excl:
                continue
        elif product == "hotel":
            if watch.get("min_stars") and (o.get("stars") or 0) \
                    < watch["min_stars"]:
                continue
            if watch.get("refundable_only") and not o.get("free_cancellation"):
                continue
        out.append(o)
    return out


def flag_offer(offer):
    flags = list(offer.get("flags", []))
    blob = str(offer.get("conditions_raw", ""))
    hits = sorted({m.group(0).lower() for m in _RX.finditer(blob)})
    if hits:
        flags.append(f'restricted-fare: {", ".join(hits)}')
    if offer.get("kind") == "flight":
        if offer.get("refundable") is False and offer.get("changeable") is False:
            flags.append("non-refundable, non-changeable")
    if not offer.get("bookable"):
        flags.append(f'price-discovery only — book via {offer["provider"]} link')
    return flags


def rank(offers, rates):
    out = []
    for o in offers:
        try:
            o = {**o, "sar_est": to_sar(o["amount"], o["currency"], rates)}
        except (ValueError, TypeError):
            continue
        o["flags"] = flag_offer(o)
        o["clean"] = not any(f.startswith("restricted-fare") for f in o["flags"])
        out.append(o)
    # Restricted fares never outrank clean ones, whatever the price.
    return sorted(out, key=lambda x: (not x["clean"], x["sar_est"]))


def is_real_drop(current_sar, daily_low_history, variant):
    """Median of daily lows, not the last sample — kills false positives."""
    lows = [v for _, v in daily_low_history]
    threshold = MIN_DROP_PCT.get(variant, 8.0)
    if len(lows) < 5:
        return False, f"building baseline ({len(lows)}/5 days)", None
    med = statistics.median(lows)
    drop = (med - current_sar) / med * 100
    if drop >= threshold:
        return True, f"{drop:.1f}% below {len(lows)}-day median", med
    return False, f"{drop:+.1f}% vs median (need -{threshold:.0f}%)", med


def target_for(watch, variant):
    if watch["product"] != "flight":
        return watch.get("target")
    if variant in ("business", "first"):
        return watch.get("target_biz")
    return watch.get("target_eco")


def label_of(offer):
    kind = offer.get("kind")
    if kind == "hotel":
        return offer.get("hotel_name")
    if kind == "car":
        return offer.get("vendor")
    return offer.get("carrier")
