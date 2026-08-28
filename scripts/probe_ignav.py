#!/usr/bin/env python3
"""
Calibration probe for the Ignav API — round 2.

Round 1 established:
  * api.ignav.com does not resolve (ConnectionError on every path)
  * https://ignav.com/api/fares EXISTS — it answered 401, not 404

So the endpoint is known and the open question is authentication. This
round targets only the confirmed URL, tries the remaining plausible auth
schemes, and — the important part — PRINTS THE RESPONSE BODY, which
normally names the exact problem ("invalid api key", "missing header").

It is diagnostics only and never fails the build.

Run:  IGNAV_TOKEN=... python scripts/probe_ignav.py
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

TOKEN = os.environ.get("IGNAV_TOKEN", "")
BASE = os.environ.get("IGNAV_BASE") or "https://ignav.com/api"
PATHS = ["/fares/round-trip", "/fares/one-way"]

BODY = {
    "origin": "RUH", "destination": "LIS",
    "departure_date": "2026-10-08", "return_date": "2026-10-16",
    "market": "SA", "currency": "SAR", "cabin": "economy", "adults": 1,
}


def auth_variants(token):
    """Docs specify X-Api-Key; Bearer is documented as also accepted.
    Both are tried so a failure body identifies which is at fault."""
    return [
        ("X-Api-Key (documented)", {"X-Api-Key": token}, {}),
        ("Authorization: Bearer", {"Authorization": f"Bearer {token}"}, {}),
    ]


def shape(obj, depth=0):
    if depth > 3:
        return "..."
    if isinstance(obj, dict):
        return {k: shape(v, depth + 1) for k, v in list(obj.items())[:15]}
    if isinstance(obj, list):
        return [shape(obj[0], depth + 1), f"...x{len(obj)}"] if obj else []
    if isinstance(obj, str):
        return f"str({obj[:40]})"
    return type(obj).__name__


def report_success(label, method, url, r):
    print(f"\n*** SUCCESS: {label} {method} {url}\n")
    try:
        data = r.json()
    except ValueError:
        print("non-JSON body:", r.text[:800])
        return
    print("STRUCTURE:")
    print(json.dumps(shape(data), indent=2)[:3000])
    print("\nRAW BODY (first 2000 chars):")
    print(json.dumps(data)[:2000])


def main():
    if not TOKEN:
        print("IGNAV_TOKEN not set — skipping probe.")
        return
    print(f"token: starts {TOKEN[:6]!r}, length {len(TOKEN)}")
    print(f"base:  {BASE}\n")

    seen_bodies = set()
    for path in PATHS:
        url = f"{BASE}{path}"
        for label, headers, params in auth_variants(TOKEN):
            hdrs = {**headers, "Content-Type": "application/json",
                    "Accept": "application/json"}
            for method in ("POST", "GET"):
                try:
                    if method == "POST":
                        r = requests.post(url, headers=hdrs, json=BODY,
                                          params=params, timeout=25)
                    else:
                        r = requests.get(url, headers=hdrs,
                                         params={**params, **BODY}, timeout=25)
                except Exception as e:
                    print(f"  {method:4} {path:9} {label:24} -> "
                          f"{type(e).__name__}")
                    continue

                print(f"  {method:4} {path:9} {label:24} -> {r.status_code}")

                if r.status_code == 200:
                    report_success(label, method, url, r)
                    return

                # The body is the whole point of this round. Print each
                # distinct one once so the output stays readable.
                body = (r.text or "")[:300].strip()
                if body and body not in seen_bodies:
                    seen_bodies.add(body)
                    print(f"       body: {body}")

    print("\nNo 200 yet. The distinct response bodies above should say "
          "whether this is an auth-scheme problem or an invalid key.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:            # diagnostics must never gate a scan
        print(f"probe error: {type(e).__name__}: {e}")
