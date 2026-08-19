#!/usr/bin/env python3
"""
Fare Hunter - flights (one-way/round/multi-city), hotels, car rental.
Multi-provider, multi-market, daily digest. Never stores or charges a card.

  python hunt.py flight lisbon --slice RUH:LIS:2026-10-05 \
      --slice LIS:RUH:2026-10-12 --flex 3 --adults 2 \
      --target-eco 2800 --target-biz 9500

  python hunt.py flight tour --slice RUH:IST:2026-11-01 \
      --slice IST:VIE:2026-11-05 --slice VIE:RUH:2026-11-10   # multi-city

  python hunt.py hotel almaty --city ALA --checkin 2026-09-28 \
      --checkout 2026-10-03 --adults 2 --target 2400

  python hunt.py car almaty-car --pickup ALA \
      --from-time 2026-09-29T10:00 --to-time 2026-10-06T10:00 --target 900

  python hunt.py list | scan | digest [--dry-run] | markets <route>
                     | providers | pause <id> | resume <id> | remove <id>
"""
import argparse
import json
import sys
import time

import requests
import yaml
from pathlib import Path

from core import clock, digest, search, watches
from providers import registry
from storage import db

CFG = yaml.safe_load((Path(__file__).parent / "config.yaml").read_text())

FX_URL = "https://open.er-api.com/v6/latest/SAR"
FX_CACHE = Path(__file__).parent / ".fx_cache.json"
FX_TTL = 6 * 3600

# Last-resort snapshot (2026-08). Only used when the live feed AND the
# cache are both unavailable — stale FX silently corrupts every
# cross-market comparison, so live-with-cache is the normal path.
STATIC_FX = {"SAR": 1.0, "USD": 3.75, "AED": 1.021, "EUR": 4.06, "GBP": 4.78,
             "INR": 0.0451, "TRY": 0.0925, "KZT": 0.0078, "PKR": 0.0135,
             "EGP": 0.0775, "JPY": 0.0243, "SGD": 2.79, "MYR": 0.845,
             "THB": 0.108, "IDR": 0.00023, "VND": 0.000148, "PHP": 0.0645,
             "ZAR": 0.207, "PLN": 0.94, "RON": 0.82, "HUF": 0.0103,
             "QAR": 1.03, "KWD": 12.2, "BHD": 9.95, "OMR": 9.74,
             "JOD": 5.29, "MAD": 0.375, "LKR": 0.0126, "KRW": 0.0027}


def fx_rates():
    """SAR per 1 unit of each currency. Live feed, cached 6h on disk.

    Fallback order: fresh cache -> live fetch -> stale cache -> static
    snapshot. A stale live rate still beats the static table.
    """
    try:
        cached = json.loads(FX_CACHE.read_text())
    except Exception:
        cached = None
    if cached and time.time() - cached["fetched_at"] < FX_TTL:
        return cached["rates"]
    try:
        r = requests.get(FX_URL, timeout=15)
        r.raise_for_status()
        d = r.json()
        if d.get("result") != "success":
            raise ValueError(f'fx feed returned {d.get("result")!r}')
        # Feed quotes units-per-SAR; comparisons need SAR-per-unit.
        rates = {c: round(1.0 / v, 6) for c, v in d["rates"].items() if v}
        rates["SAR"] = 1.0
        FX_CACHE.write_text(json.dumps(
            {"fetched_at": time.time(), "rates": rates}))
        return rates
    except Exception as e:
        if cached:
            print(f"fx: live feed failed ({e}); using cached rates",
                  file=sys.stderr)
            return cached["rates"]
        print(f"fx: live feed failed ({e}); using static snapshot",
              file=sys.stderr)
        return STATIC_FX


def _parse_slices(raw):
    out = []
    for s in raw:
        parts = s.split(":")
        if len(parts) != 3:
            sys.exit(f"bad --slice {s!r}; use ORIGIN:DEST:YYYY-MM-DD")
        out.append({"origin": parts[0].upper(),
                    "destination": parts[1].upper(), "date": parts[2]})
    return out


def _date_model(a):
    if getattr(a, "month", None):
        return "month"
    if getattr(a, "rolling", None):
        return "rolling"
    if getattr(a, "flex", None):
        return "flex"
    return "fixed"


def cmd_flight(a):
    slices = _parse_slices(a.slice)
    db.add_watch({
        "id": a.id, "product": "flight",
        "trip_type": watches.trip_type(slices),
        "slices_json": json.dumps(slices), "cabins": a.cabins,
        "max_stops": a.max_stops, "exclude": a.exclude,
        "date_model": _date_model(a), "flex_days": a.flex or 0,
        "month": a.month, "rolling_days": a.rolling, "nights": a.nights,
        "adults": a.adults, "target_eco": a.target_eco,
        "target_biz": a.target_biz, "status": "active",
        "created_at": clock.iso()})
    w = next(x for x in db.list_watches() if x["id"] == a.id)
    print(f'added {a.id}  {watches.describe(w)}  '
          f'{len(watches.expand(w))} date variants/scan')


def cmd_hotel(a):
    db.add_watch({"id": a.id, "product": "hotel", "city": a.city.upper(),
                  "checkin": a.checkin, "checkout": a.checkout,
                  "rooms": a.rooms, "adults": a.adults,
                  "min_stars": a.min_stars,
                  "refundable_only": int(bool(a.refundable_only)),
                  "date_model": "fixed", "target": a.target,
                  "status": "active", "created_at": clock.iso()})
    print(f'added {a.id}  hotel {a.city.upper()} '
          f'{a.checkin}->{a.checkout}')


def cmd_car(a):
    db.add_watch({"id": a.id, "product": "car",
                  "pickup_location": a.pickup.upper(),
                  "pickup_at": a.from_time, "dropoff_at": a.to_time,
                  "car_category": a.category, "adults": a.adults,
                  "date_model": "fixed", "target": a.target,
                  "status": "active", "created_at": clock.iso()})
    print(f'added {a.id}  car {a.pickup.upper()} {a.from_time[:10]}')


def cmd_list(a):
    rows = db.list_watches(active_only=False)
    if not rows:
        return print("no watches")
    for w in rows:
        tag = "" if w["status"] == "active" else f'  ({w["status"]})'
        tg = w.get("target") or w.get("target_eco") or "-"
        print(f'{w["id"]:<14} {w["product"]:<7} {watches.describe(w):<38} '
              f'target:{tg}{tag}')


def cmd_scan(a):
    rates, pos, out = fx_rates(), CFG["points_of_sale"], {}
    for w in db.list_watches():
        print(f'\n=== {w["id"]}  {watches.describe(w)} ===')
        found = search.run_watch(w, pos, rates, CFG)
        out[w["id"]] = found
        if not found:
            print("  no results")
        for o in found:
            print(f'  {o["variant"]:<9} {o["sar_est"]:>8.0f} SAR  '
                  f'{o.get("label")}  {o["provider"]}/{o["pos"]["code"]}')
    return out


def cmd_digest(a):
    if db.digest_sent_today() and not a.force:
        return print("already sent today (use --force)")
    body, alerts = digest.build(cmd_scan(a), db.list_watches(), CFG)
    subj = "[Fare Hunter] * target hit" if alerts else "[Fare Hunter] daily digest"
    if a.dry_run:
        return print("\n" + "=" * 66 + "\n" + body)
    digest.send(CFG["user"]["email"], subj, body)
    db.mark_digest_sent()
    print(f'sent to {CFG["user"]["email"]}')


def cmd_markets(a):
    rows = db.market_report(a.route)
    if not rows:
        return print(f"no data for {a.route} yet")
    print(f'{"POS":<5}{"scans":>7}{"wins":>6}{"edge":>8}')
    for r in rows:
        print(f'{r["pos_code"]:<5}{r["scans"]:>7}{r["wins"]:>6}'
              f'{r["best_edge"]:>7.1f}%')
    cold = db.cold_markets(a.route)
    due = db.due_for_reprobe(a.route)
    if cold:
        print(f'\nresting: {", ".join(sorted(cold))}')
    if due:
        print(f'due for re-probe next scan: {", ".join(sorted(due))}')


def cmd_providers(a):
    for kind in ("flight", "hotel", "car"):
        act = [p.NAME + ("*" if p.BOOKABLE else "") for p in registry.active(kind)]
        miss = registry.missing(kind)
        print(f'{kind:<7} active: {", ".join(act) or "none"}'
              + (f'   missing creds: {", ".join(miss)}' if miss else ""))
    print("\n* = bookable (can hold). others are price-discovery + link.")
    rows = db.provider_report()
    if rows:
        print(f'\n{"route":<16}{"provider":<16}{"scans":>7}{"wins":>6}{"err":>6}')
        for r in rows:
            print(f'{r["route"]:<16}{r["provider"]:<16}{r["scans"]:>7}'
                  f'{r["wins"]:>6}{r["errors"]:>6}')


def cmd_dashboard(a):
    from web import dashboard
    out = Path(a.out)
    out.write_text(dashboard.render(CFG))
    print(f"wrote {out}  (open it in a browser)")


def cmd_serve(a):
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from web import dashboard

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = dashboard.render(CFG).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    print(f"dashboard live on http://localhost:{a.port}  (Ctrl-C to stop)")
    HTTPServer(("", a.port), Handler).serve_forever()


def cmd_holds(a):
    for h in db.open_holds():
        print(f'{h["booking_reference"]}  {h["variant"]}  '
              f'{h["amount_sar"]:.0f} SAR  pay by {h["pay_by"]}')


def main():
    p = argparse.ArgumentParser(prog="hunt")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("flight")
    f.add_argument("id")
    f.add_argument("--slice", action="append", required=True,
                   help="ORIGIN:DEST:YYYY-MM-DD (repeat for multi-city)")
    f.add_argument("--flex", type=int)
    f.add_argument("--month")
    f.add_argument("--rolling", type=int)
    f.add_argument("--nights", type=int, default=7)
    f.add_argument("--adults", type=int, default=1)
    f.add_argument("--cabins", default="economy,business")
    f.add_argument("--target-eco", type=float)
    f.add_argument("--target-biz", type=float)
    f.add_argument("--max-stops", type=int, default=2)
    f.add_argument("--exclude")
    f.set_defaults(func=cmd_flight)

    h = sub.add_parser("hotel")
    h.add_argument("id")
    h.add_argument("--city", required=True)
    h.add_argument("--checkin", required=True)
    h.add_argument("--checkout", required=True)
    h.add_argument("--rooms", type=int, default=1)
    h.add_argument("--adults", type=int, default=2)
    h.add_argument("--min-stars", type=float)
    h.add_argument("--refundable-only", action="store_true")
    h.add_argument("--target", type=float)
    h.set_defaults(func=cmd_hotel)

    c = sub.add_parser("car")
    c.add_argument("id")
    c.add_argument("--pickup", required=True)
    c.add_argument("--from-time", required=True, dest="from_time")
    c.add_argument("--to-time", required=True, dest="to_time")
    c.add_argument("--category")
    c.add_argument("--adults", type=int, default=2)
    c.add_argument("--target", type=float)
    c.set_defaults(func=cmd_car)

    sub.add_parser("list").set_defaults(func=cmd_list)
    sub.add_parser("scan").set_defaults(func=cmd_scan)
    sub.add_parser("providers").set_defaults(func=cmd_providers)
    sub.add_parser("holds").set_defaults(func=cmd_holds)

    sv = sub.add_parser("serve")
    sv.add_argument("--port", type=int, default=8787)
    sv.set_defaults(func=cmd_serve)

    dash = sub.add_parser("dashboard")
    dash.add_argument("--out", default="dashboard.html")
    dash.set_defaults(func=cmd_dashboard)

    d = sub.add_parser("digest")
    d.add_argument("--dry-run", action="store_true")
    d.add_argument("--force", action="store_true")
    d.set_defaults(func=cmd_digest)

    m = sub.add_parser("markets")
    m.add_argument("route")
    m.set_defaults(func=cmd_markets)

    for name, fn in [("pause", lambda x: db.set_status(x.id, "paused")),
                     ("resume", lambda x: db.set_status(x.id, "active")),
                     ("remove", lambda x: db.delete_watch(x.id))]:
        s = sub.add_parser(name)
        s.add_argument("id")
        s.set_defaults(func=fn)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
