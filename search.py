"""
Two-phase search across PROVIDERS x MARKETS.

Phase 1 (broad): every active provider x every warm market, on ONE
representative date variant. Cost = providers x markets.

Phase 2 (deep): only (provider, market) pairs within DEEP_MARGIN of the
leader get the full date grid.

Cold markets are re-probed on schedule — see db.due_for_reprobe. A market
pruned in July gets another look in August, because fares are re-filed
seasonally and permanent pruning loses genuine wins.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from core import compare, watches
from providers import registry
from storage import db

DEEP_MARGIN = 0.12
MAX_DEEP_COMBOS = 6
WORKERS = 6


class RateLimiter:
    def __init__(self, per_minute=90):
        self.interval = 60.0 / per_minute
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            if now < self.next_at:
                time.sleep(self.next_at - now)
                now = time.monotonic()
            self.next_at = now + self.interval


LIMITER = RateLimiter()


def warm_markets(all_pos, route):
    """Warm = not cold. Re-probe candidates are folded back in here."""
    reprobe = db.due_for_reprobe(route)
    if reprobe:
        db.mark_reprobed(route, reprobe)
    cold = db.cold_markets(route)
    warm = [p for p in all_pos if p["code"] not in cold]
    return warm or all_pos[:3]


def _build_req(watch, pos, variant, slice_set):
    base = {"currency": pos["currency"], "pos_code": pos["code"],
            "adults": watch.get("adults", 1)}
    if watch["product"] == "flight":
        base.update(slices=slice_set, cabin=variant,
                    max_stops=watch.get("max_stops"))
    elif watch["product"] == "hotel":
        base.update(city=watch["city"], checkin=watch["checkin"],
                    checkout=watch["checkout"], rooms=watch.get("rooms", 1),
                    residency=pos["code"].lower(),
                    refundable_only=watch.get("refundable_only"))
    else:
        base.update(pickup_location=watch["pickup_location"],
                    pickup_at=watch["pickup_at"],
                    dropoff_at=watch["dropoff_at"])
    return base


def _one(watch, provider, pos, variant, slice_set, route):
    LIMITER.wait()
    try:
        res = provider.search(_build_req(watch, pos, variant, slice_set))
    except Exception:
        db.bump_provider(route, provider.NAME, error=True)
        return []
    for o in res:
        o["pos"] = pos
        o["variant"] = variant
        if slice_set:
            o["dates"] = [s["date"] for s in slice_set]
    return res


def run_watch(watch, all_pos, rates, cfg=None) -> list[dict]:
    product = watch["product"]
    route = watches.route_key(watch)
    providers = registry.active(product)
    if not providers:
        return []

    variant_list = watches.variants(watch)
    date_variants = watches.expand(watch)
    probe = date_variants[len(date_variants) // 2]
    results = []

    for variant in variant_list:
        warm = warm_markets(all_pos, route)

        # ---- phase 1 ----
        p1 = []
        with ThreadPoolExecutor(WORKERS) as ex:
            futs = [ex.submit(_one, watch, pr, pos, variant, probe, route)
                    for pr in providers for pos in warm]
            for f in as_completed(futs):
                p1 += f.result()

        p1 = compare.rank(compare.apply_filters(p1, watch), rates)
        if not p1:
            continue

        leader = p1[0]["sar_est"]
        combos, seen = [], set()
        for o in p1:
            key = (o["provider"], o["pos"]["code"])
            if key in seen:
                continue
            if o["sar_est"] <= leader * (1 + DEEP_MARGIN):
                pr = next(p for p in providers if p.NAME == o["provider"])
                combos.append((pr, o["pos"]))
                seen.add(key)
            if len(combos) >= MAX_DEEP_COMBOS:
                break

        # ---- phase 2 ----
        p2 = []
        if len(date_variants) > 1 and product == "flight":
            with ThreadPoolExecutor(WORKERS) as ex:
                futs = [ex.submit(_one, watch, pr, pos, variant, sv, route)
                        for pr, pos in combos
                        for sv in date_variants if sv != probe]
                for f in as_completed(futs):
                    p2 += f.result()

        merged = registry.merge(
            compare.rank(compare.apply_filters(p1 + p2, watch), rates),
            product)
        merged = sorted(merged, key=lambda x: (not x["clean"], x["sar_est"]))
        if not merged:
            continue

        best = merged[0]
        best["label"] = compare.label_of(best)
        best["detail"] = _detail(best)

        # market edge vs the home market
        sa_ref = min((o["sar_est"] for o in merged
                      if o["pos"]["code"] == "SA"), default=None)
        edge = round((sa_ref - best["sar_est"]) / sa_ref * 100, 1) \
            if sa_ref else 0.0
        best["market_edge_pct"] = edge

        for pos in warm:
            db.bump_market(route, pos["code"],
                           won=(pos["code"] == best["pos"]["code"]),
                           edge=edge if pos["code"] == best["pos"]["code"] else 0)
        for pr in providers:
            db.bump_provider(route, pr.NAME, won=(pr.NAME == best["provider"]))

        db.record(watch["id"], product, variant, best)
        best["alternatives"] = merged[1:4]
        results.append(best)

    return results


def _detail(o):
    kind = o.get("kind")
    if kind == "hotel":
        return {"room": o.get("room_name"), "board": o.get("board"),
                "stars": o.get("stars"),
                "free_cancellation": o.get("free_cancellation"),
                "cancel_by": o.get("cancel_by")}
    if kind == "car":
        return {"category": o.get("category"), "seats": o.get("seats"),
                "pickup_type": o.get("pickup_type"),
                "description": o.get("description")}
    return {"stops": o.get("stops"), "dates": o.get("dates"),
            "segments": o.get("segments", [])[:8],
            "deep_link": o.get("deep_link")}
