#!/usr/bin/env python3
"""
One-shot calibration probe for the Ignav API.

The dev sandbox cannot reach ignav.com, so the provider was written from
published parameter descriptions rather than a live response. This script
runs from CI (which does have network), tries the plausible endpoint and
auth combinations, and prints the structure of the first success.

Its output is what pins down providers/flights/ignav.py exactly. It never
fails the build — a probe is diagnostics, not a gate.

Run:  IGNAV_TOKEN=... python scripts/probe_ignav.py
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

TOKEN = os.environ.get("IGNAV_TOKEN", "")
BASES = [os.environ.get("IGNAV_BASE"), "https://api.ignav.com",
         "https://ignav.com/api"]
PATHS = ["/v1/fares", "/v1/search", "/fares", "/v1/flights/search",
         "/v1/round-trip", "/v1/one-way"]
AUTHS = {
    "Bearer": lambda t: {"Authorization": f"Bearer {t}"},
    "X-API-Key": lambda t: {"X-API-Key": t},
    "X-Access-Token": lambda t: {"X-Access-Token": t},
}

BODY = {
    "origin": "RUH", "destination": "LIS",
    "departure_date": "2026-10-08", "return_date": "2026-10-16",
    "market": "SA", "currency": "SAR", "cabin": "economy", "adults": 1,
}


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


def main():
    if not TOKEN:
        print("IGNAV_TOKEN not set — skipping probe.")
        return
    print(f"token prefix: {TOKEN[:6]}…  len={len(TOKEN)}")

    tried = []
    for base in [b for b in BASES if b]:
        for path in PATHS:
            for auth_name, auth in AUTHS.items():
                url = f"{base}{path}"
                headers = {**auth(TOKEN), "Content-Type": "application/json",
                           "Accept": "application/json"}
                try:
                    r = requests.post(url, headers=headers, json=BODY,
                                      timeout=25)
                except Exception as e:
                    tried.append(f"{auth_name} POST {url} -> {type(e).__name__}")
                    continue
                tried.append(f"{auth_name} POST {url} -> {r.status_code}")
                if r.status_code == 200:
                    print(f"\n*** SUCCESS: {auth_name} POST {url}\n")
                    try:
                        data = r.json()
                    except ValueError:
                        print("non-JSON body:", r.text[:600])
                        return
                    print("STRUCTURE:")
                    print(json.dumps(shape(data), indent=2)[:3000])
                    print("\nFIRST 1500 CHARS OF RAW BODY:")
                    print(json.dumps(data)[:1500])
                    return
                # A 4xx that isn't auth tells us the path exists but the
                # body is wrong — that is useful signal, so show it.
                if r.status_code in (400, 422):
                    print(f"\n[{r.status_code} at {url} via {auth_name}] "
                          f"body says: {r.text[:400]}\n")

    print("\nNo combination returned 200. Attempts:")
    for t in tried:
        print("  ", t)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:            # never fail the build on diagnostics
        print(f"probe error: {type(e).__name__}: {e}")
