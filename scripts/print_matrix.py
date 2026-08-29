#!/usr/bin/env python3
"""Print the same-flight market matrix to stdout — for CI logs.

One block per watch/cabin: the winning itinerary priced from every market
that quoted it, cheapest first, names not codes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import countries, watches
from storage import db


def main():
    any_rows = False
    for w in db.list_watches():
        if w["product"] != "flight":
            continue
        for var in watches.variants(w):
            for rows in db.latest_matrix(w["id"], var):
                any_rows = True
                head = rows[0]
                if head["itin_key"] == "cheapest-any":
                    print(f"\n=== {w['id']} · {var} · CHEAPEST PER MARKET "
                          f"(any flight, any flex date) ===")
                elif head["itin_key"] == "gulf-any":
                    print(f"\n=== {w['id']} · {var} · GULF AIRLINES — "
                          f"CHEAPEST PER MARKET (any flex date) ===")
                elif head["itin_key"] == "fastest-any":
                    print(f"\n=== {w['id']} · {var} · SHORTEST TRIP, "
                          f"CHEAPEST FARE — PER MARKET ===")
                elif head["itin_key"].startswith("pure-"):
                    print(f"\n=== {w['id']} · {var} · ALL ON "
                          f"{head['itin_key'][5:]} — CHEAPEST PER MARKET "
                          f"(any flex date) ===")
                else:
                    stops = head.get("stops")
                    stops_txt = "direct" if stops == 0 \
                        else f"{stops} stop(s)"
                    if head.get("via"):
                        stops_txt += f" · {head['via']}"
                    print(f"\n=== {w['id']} · {var} · same flight "
                          f"{head['itin_key']} "
                          f"({head.get('carrier') or '?'}) · {stops_txt} ===")
                for i, r in enumerate(rows):
                    tag = "  <- cheapest" if i == 0 else \
                        ("  (home)" if r["pos_code"] == "SA" else "")
                    via = ""
                    if (head["itin_key"] in ("cheapest-any", "gulf-any",
                                             "fastest-any")
                            or head["itin_key"].startswith("pure-")) \
                            and r.get("flight"):
                        via = f"   {r['flight']}" + \
                            (f" {r['dates']}" if r.get("dates") else "") + \
                            (f"   {r['via']}" if r.get("via") else "")
                    print(f"  {countries.label(r['pos_code']):<24} "
                          f"{r['amount_sar']:>10,.0f} SAR   "
                          f"{r['amount_native']:>12,.0f} "
                          f"{r['currency']}{via}{tag}")
    if not any_rows:
        print("No matrix rows yet — a market quote for the winning flight "
              "needs at least two markets pricing the same itinerary.")


if __name__ == "__main__":
    main()
