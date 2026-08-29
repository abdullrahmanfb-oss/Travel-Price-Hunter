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
import collections
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from core import clock, compare, countries, watches
from providers import registry
from storage import db

DEEP_MARGIN = 0.12
MAX_DEEP_COMBOS = 6
WORKERS = 3            # free/test provider tiers punish parallelism
PER_MINUTE = 30        # conservative default; raise via config once proven


class RateLimiter:
    """Global pace-setter shared by every worker thread.

    `back_off()` pushes the next slot out after a 429 so the whole scan
    slows down together — throttling one thread while the others keep
    hammering just earns more 429s.
    """

    def __init__(self, per_minute=PER_MINUTE):
        self.interval = 60.0 / per_minute
        self.lock = threading.Lock()
        self.next_at = 0.0

    def configure(self, per_minute):
        with self.lock:
            self.interval = 60.0 / max(1, per_minute)

    def wait(self):
        with self.lock:
            now = time.monotonic()
            if now < self.next_at:
                time.sleep(self.next_at - now)
                now = time.monotonic()
            self.next_at = now + self.interval

    def back_off(self, seconds):
        with self.lock:
            self.next_at = max(self.next_at, time.monotonic() + seconds)


LIMITER = RateLimiter()

# Why calls failed, so a bad scan reports a reason instead of a silent zero.
ERRORS = collections.Counter()


def error_summary(limit=4):
    if not ERRORS:
        return ""
    top = ", ".join(f"{reason} x{n}" for reason, n in ERRORS.most_common(limit))
    return f"{sum(ERRORS.values())} provider call(s) failed: {top}"


def warm_markets(all_pos, route):
    """Warm = not cold. Re-probe candidates are folded back in here.

    The home market is exempt from pruning: the Saudi-gap comparison and
    the SA reference sample need an SA quote every scan, and SA rarely
    wins on price — under the normal rule it would go cold and the gap
    view would go blind."""
    reprobe = db.due_for_reprobe(route)
    if reprobe:
        db.mark_reprobed(route, reprobe)
    cold = db.cold_markets(route) - {countries.HOME}
    warm = [p for p in all_pos if p["code"] not in cold]
    return warm or all_pos[:3]


def _build_req(watch, pos, variant, slice_set):
    base = {"currency": pos["currency"], "pos_code": pos["code"],
            "adults": watch.get("adults", 1)}
    if watch["product"] == "flight":
        base.update(slices=slice_set, cabin=variant,
                    max_stops=watch.get("max_stops"),
                    airlines=[a.strip().upper()
                              for a in (watch.get("airlines") or "").split(",")
                              if a.strip()])
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


def _reason(exc):
    """Short, groupable label for an exception — HTTP status when we have
    one, else the exception class. A 400's body usually names the bad
    field or value; surface it so the summary is actionable."""
    resp = getattr(exc, "response", None)
    if resp is not None and getattr(resp, "status_code", None):
        code = resp.status_code
        if code == 429:
            return "HTTP 429 rate-limited"
        if code == 400:
            try:
                err = resp.json().get("error", {})
                detail = err.get("field") or err.get("code")
                if detail:
                    return f"HTTP 400 ({detail})"
            except Exception:
                pass
        return f"HTTP {code}"
    return type(exc).__name__


def _one(watch, provider, pos, variant, slice_set, route):
    LIMITER.wait()
    try:
        res = provider.search(_build_req(watch, pos, variant, slice_set))
    except Exception as e:
        reason = _reason(e)
        # a 400 is usually market-specific — name the market so one bad
        # POS code identifies itself instead of hiding in an aggregate
        if reason.startswith("HTTP 400"):
            reason += f" pos={pos['code']}"
        ERRORS[f"{provider.NAME}: {reason}"] += 1
        # A 429 means the whole scan is going too fast, not just this call.
        if reason.endswith("rate-limited"):
            LIMITER.back_off(5.0)
        db.bump_provider(route, provider.NAME, error=True)
        return []
    for o in res:
        o["pos"] = pos
        o["variant"] = variant
        if slice_set:
            o["dates"] = [s["date"] for s in slice_set]
    return res


def run_watch(watch, all_pos, rates, cfg=None) -> list[dict]:
    tune = (cfg or {}).get("search", {})
    deep_margin = tune.get("deep_margin", DEEP_MARGIN)
    max_deep = tune.get("max_deep_combos", MAX_DEEP_COMBOS)
    # config.yaml was previously read but never applied — these two lines
    # are what make search.requests_per_minute / workers actually count.
    LIMITER.configure(tune.get("requests_per_minute", PER_MINUTE))
    workers = max(1, tune.get("workers", WORKERS))

    product = watch["product"]
    route = watches.route_key(watch)
    providers = registry.active(product)
    if not providers:
        return []

    variant_list = watches.variants(watch)
    date_variants = watches.expand(watch)
    base_probe = date_variants[len(date_variants) // 2]
    results = []

    # One warm set for the whole scan: bump_market runs between cabins,
    # and letting the second cabin see freshly-cold markets meant economy
    # and business covered different markets in the same run.
    warm = warm_markets(all_pos, route)

    for variant in variant_list:
        # ---- phase 1 ----
        # The probe date can genuinely have nothing to offer — a carrier
        # that serves the route only some weekdays, or an airline-filtered
        # watch — so fall back through the other date variants before
        # declaring the whole cabin empty. Extra requests are spent only
        # when a variant came back empty.
        probe = base_probe
        p1 = []
        came_up_empty = []
        # nearest dates first, and at most three attempts: a schedule gap
        # is usually +-1 day, and a watch with genuinely nothing to find
        # must not burn warm x variants requests every single scan
        others = sorted((v for v in date_variants if v != base_probe),
                        key=lambda v: abs(date_variants.index(v)
                                          - date_variants.index(base_probe)))
        for candidate in ([base_probe] + others)[:3]:
            with ThreadPoolExecutor(workers) as ex:
                futs = [ex.submit(_one, watch, pr, pos, variant, candidate,
                                  route)
                        for pr in providers for pos in warm]
                for f in as_completed(futs):
                    p1 += f.result()
            p1 = compare.rank(compare.apply_filters(p1, watch), rates)
            if p1:
                probe = candidate
                break
            came_up_empty.append(candidate)
        if not p1:
            continue

        leader = p1[0]["sar_est"]
        combos, seen = [], set()
        for o in p1:
            key = (o["provider"], o["pos"]["code"])
            if key in seen:
                continue
            if o["sar_est"] <= leader * (1 + deep_margin):
                pr = next(p for p in providers if p.NAME == o["provider"])
                combos.append((pr, o["pos"]))
                seen.add(key)
            if len(combos) >= max_deep:
                break

        # ---- phase 2 ----
        p2 = []
        if len(date_variants) > 1 and product == "flight":
            with ThreadPoolExecutor(workers) as ex:
                futs = [ex.submit(_one, watch, pr, pos, variant, sv, route)
                        for pr, pos in combos
                        for sv in date_variants
                        # skip the phase-1 date and any variant phase 1
                        # already proved empty across every warm market
                        if sv != probe and sv not in came_up_empty]
                for f in as_completed(futs):
                    p2 += f.result()

        ranked = compare.rank(compare.apply_filters(p1 + p2, watch), rates)
        merged = registry.merge(ranked, product)
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
        best["sa_ref_sar"] = sa_ref

        for pos in warm:
            db.bump_market(route, pos["code"],
                           won=(pos["code"] == best["pos"]["code"]),
                           edge=edge if pos["code"] == best["pos"]["code"] else 0)
        for pr in providers:
            db.bump_provider(route, pr.NAME, won=(pr.NAME == best["provider"]))

        db.record(watch["id"], product, variant, best)
        # Also record the SA reference sample when SA didn't win, so the
        # dashboard's Saudi-gap view stays live even on routes where the
        # home market never produces the best price.
        if sa_ref is not None and best["pos"]["code"] != "SA":
            sa_offer = min((o for o in merged
                            if o["pos"]["code"] == "SA"),
                           key=lambda x: x["sar_est"])
            db.record(watch["id"], product, variant, sa_offer)
        # same flight, every market: keep each market's quote for the
        # winning itinerary so the dashboard can show them side by side.
        # The winner may sit on a flex date only the deep pass priced, so
        # also record the best offer on the REQUESTED dates (the probe
        # variant) — every warm market quoted those in phase 1, which is
        # what fills the full 28-row window at zero extra request cost.
        if product == "flight":
            at = clock.iso()
            recorded = {_record_matrix(watch, variant, best, ranked, at)}
            probe_dates = [s["date"] for s in probe]
            if best.get("dates") != probe_dates:
                probe_best = min(
                    (o for o in ranked if o.get("dates") == probe_dates),
                    key=lambda x: x["sar_est"], default=None)
                if probe_best is not None:
                    recorded.add(_record_matrix(watch, variant, probe_best,
                                                ranked, at))
            # Focus carriers (e.g. Saudia): their best itinerary gets its
            # own window even when it never wins. Reuses offers this scan
            # already holds — zero extra requests. A hard airlines filter
            # can't do this job: no pure single-carrier itinerary may
            # exist on the route at all (interline segments).
            for code in [c.strip().upper()
                         for c in (watch.get("focus_airlines") or "").split(",")
                         if c.strip()]:
                fo = min((o for o in ranked
                          if any(s.get("flight", "").startswith(code)
                                 for s in o.get("segments", []))),
                         key=lambda x: x["sar_est"], default=None)
                if fo is not None:
                    _record_matrix(watch, variant, fo, ranked, at,
                                   min_markets=1, skip_keys=recorded)

        best["alternatives"] = merged[1:4]
        results.append(best)

    return results


def _record_matrix(watch, variant, best, ranked, at=None,
                   min_markets=2, skip_keys=None):
    """For one itinerary (same flight numbers + departure times — the
    dedupe key already encodes the dates), record the cheapest quote from
    each market that priced it. Returns the itinerary key on success so
    callers can avoid recording the same window twice in one batch."""
    keyfn = registry.KEYFN["flight"]
    try:
        key = keyfn(best)
    except Exception:
        return None
    if not key or (skip_keys and key in skip_keys):
        return None
    by_pos = {}
    for o in ranked:
        try:
            if keyfn(o) != key:
                continue
        except Exception:
            continue
        pc = o["pos"]["code"]
        cur = by_pos.get(pc)
        if cur is None or o["sar_est"] < cur["sar_est"]:
            by_pos[pc] = o
    if len(by_pos) < min_markets:
        return None
    label = "+".join(s.get("flight", "?") for s in best.get("segments", []))
    if best.get("dates"):
        # several windows can coexist in one scan — the dates tell them apart
        label += " · " + "/".join(best["dates"])
    db.record_matrix(
        watch["id"], variant, label, best.get("carrier"),
        [{"pos_code": pc, "currency": o["currency"],
          "amount_native": o["amount"], "amount_sar": o["sar_est"],
          "stops": o.get("stops")} for pc, o in by_pos.items()], at)
    return key


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
