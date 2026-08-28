#!/usr/bin/env python3
"""
Calibration probe for the Ignav API — round 3: field discovery.

Round 2 established (run #48):
  * X-Api-Key authenticates (Bearer does NOT — 'api_key_required')
  * POST /fares/round-trip and /fares/one-way are the endpoints
  * validation is strict and names ONE offending field per response:
      round-trip rejected 'currency'; one-way rejected 'return_date'

So this round starts from a minimal body and adds candidate fields one
request at a time, recording which are accepted and which are rejected,
then prints the response structure of the final successful call.

Failed requests do not count against the free quota (only successful
responses are billed), so this discovery is cheap.

Run:  IGNAV_TOKEN=... python scripts/probe_ignav.py
"""
import json
import os
import sys

import requests

TOKEN = os.environ.get("IGNAV_TOKEN", "")
BASE = os.environ.get("IGNAV_BASE") or "https://ignav.com/api"

MINIMAL = {
    "origin": "RUH", "destination": "LIS",
    "departure_date": "2026-10-08", "return_date": "2026-10-16",
}
# candidates in priority order — market matters most
CANDIDATES = [
    ("market", "PL"),
    ("cabin_class", "business"),
    ("adults", 2),
    ("max_stops", 1),
    ("currency", "SAR"),
]


def post(url, body):
    return requests.post(url, json=body, timeout=25,
                         headers={"X-Api-Key": TOKEN,
                                  "Content-Type": "application/json",
                                  "Accept": "application/json"})


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
    url = f"{BASE}/fares/round-trip"
    print(f"target: {url}\n")

    r = post(url, MINIMAL)
    print(f"minimal body -> {r.status_code}")
    if r.status_code != 200:
        print("  body:", r.text[:400])
        print("\nminimal body already rejected — fix that first.")
        return

    body = dict(MINIMAL)
    accepted, rejected = [], []
    last_ok = r
    for key, val in CANDIDATES:
        trial = {**body, key: val}
        r = post(url, trial)
        if r.status_code == 200:
            body[key] = val
            accepted.append(key)
            last_ok = r
            print(f"+ {key:10} accepted")
        else:
            rejected.append(key)
            print(f"- {key:10} REJECTED ({r.status_code}): {r.text[:200]}")

    print(f"\naccepted: {accepted}")
    print(f"rejected: {rejected}")
    print(f"final body: {json.dumps(body)}")

    try:
        data = last_ok.json()
    except ValueError:
        print("non-JSON body:", last_ok.text[:800])
        return
    print("\nRESPONSE STRUCTURE:")
    print(json.dumps(shape(data), indent=2)[:3500])
    print("\nRAW (first 2500 chars):")
    print(json.dumps(data)[:2500])


if __name__ == "__main__":
    try:
        main()
    except Exception as e:            # diagnostics must never gate a scan
        print(f"probe error: {type(e).__name__}: {e}")
