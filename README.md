# Fare Hunter

Flights (one-way / round / multi-city), hotels, car rental.
Multi-provider, 28 markets, one daily digest. **No card stored or charged.**

## Quick look, no keys needed
    pip install -r requirements.txt
    python simulate.py

## Setup
    export DUFFEL_TOKEN=...              # duffel.com, free test mode
    export AMADEUS_KEY=... AMADEUS_SECRET=...   # free self-service tier
    export KIWI_API_KEY=...              # optional
    export SMTP_FROM=Abdullrahman.fb@gmail.com
    export SMTP_USER=Abdullrahman.fb@gmail.com
    export SMTP_PASS=...                 # Gmail App Password

## Use
Nothing is tracked until you add a watch. Fill in your own airports,
cities and dates:

    # round trip, flexible 3 days (repeat --slice; 1 = one-way, 3+ = multi-city)
    python hunt.py flight <id> --slice ORIGIN:DEST:2026-10-05 \
        --slice DEST:ORIGIN:2026-10-12 --flex 3 --adults 2 \
        --target-eco 2800 --target-biz 9500 --max-stops 1

    python hunt.py hotel <id> --city CITYCODE --checkin 2026-09-28 \
        --checkout 2026-10-03 --adults 2 --target 2400 --refundable-only

    python hunt.py car <id> --pickup LOCATION \
        --from-time 2026-09-29T10:00 --to-time 2026-10-06T10:00 --target 900

    python hunt.py digest --dry-run
    python hunt.py providers          # who's active, who's winning
    python hunt.py markets <route>    # e.g. ORIGIN-DEST-ORIGIN
    python hunt.py serve              # live dashboard on http://localhost:8787
    python hunt.py dashboard          # static export to dashboard.html

Cron: `0 5 * * * cd ~/fare-hunter && python hunt.py digest >> hunt.log 2>&1`

## Why not scrape airline sites
Scraped prices are unbookable. You get a number you can't act on, from a
scraper that breaks weekly behind Akamai/DataDome and gets your IP banned.
Every source here is an official API returning a real, priced offer.

Multi-source is still achieved - just properly:

| Source | Content | Bookable |
|---|---|---|
| Duffel | airline NDC direct + GDS | yes - hold orders |
| Amadeus | different GDS, different contracts | discovery |
| Kiwi | low-cost carriers, self-transfer routes | link |
| Amadeus Hotels | hotel rates | discovery |
| RateHawk | net hotel rates, residency-aware | yes |
| Amadeus Cars | rental | discovery |

When sources disagree on the same flight, that gap IS the finding.
`source_count` shows how many independently confirmed a price.

## Market pruning (fixed)
A market that never wins in 8 scans rests - but is **re-probed every 7
days** with a fresh trial. Permanent pruning was a bug: fares are re-filed
seasonally, so a market that loses all summer can win in autumn.

## Drop thresholds
economy -8%, business -12%, hotels/cars -7%, measured against the median
of daily lows (not the last sample). Needs 5 days of history first.

## Saudi price gap
Any market beating the SA price for the same thing by 25%+ (after SAR
normalisation, `alerts.market_edge_pct` in config.yaml) is flagged
`⚑ CHEAPER ABROAD` in the digest and on the dashboard — e.g. 10,000 SAR
at home, 5,000 SAR bought from Turkey gets flagged at -50%. Scans record
an SA reference sample whenever SA doesn't win, so the gap stays
computable from history.

## Dashboard
`python hunt.py serve` renders straight from hunter.db on every request —
30-day charts per watch/cabin, target lines, drop/target/gap badges, the
Saudi-gap table, market wins and holds. `python hunt.py dashboard` writes
the same page as a single self-contained HTML file.

## Guards
- Fares mentioning residency/point-of-sale limits never outrank clean ones
- Kiwi self-transfer itineraries flagged: missed connection isn't protected
- Non-bookable sources labelled `link-only` so the digest never implies
  it can hold something it can't

## FX
Live rates (open.er-api.com, no key needed), cached 6h in `.fx_cache.json`.
Fallback order: fresh cache → live fetch → stale cache → static snapshot.

## TODO
- RateHawk region_id resolution via /search/multicomplete
- Almosafer / Wego for ex-KSA routes
