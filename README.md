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
    # round trip, flexible 3 days
    python hunt.py flight lisbon --slice RUH:LIS:2026-10-05 \
        --slice LIS:RUH:2026-10-12 --flex 3 --adults 2 \
        --target-eco 2800 --target-biz 9500 --max-stops 1

    # multi-city
    python hunt.py flight tour --slice RUH:IST:2026-11-01 \
        --slice IST:VIE:2026-11-05 --slice VIE:RUH:2026-11-10

    # one-way
    python hunt.py flight dxb --slice RUH:DXB:2026-09-15

    python hunt.py hotel almaty --city ALA --checkin 2026-09-28 \
        --checkout 2026-10-03 --adults 2 --target 2400 --refundable-only

    python hunt.py car almaty-car --pickup ALA \
        --from-time 2026-09-29T10:00 --to-time 2026-10-06T10:00 --target 900

    python hunt.py digest --dry-run
    python hunt.py providers          # who's active, who's winning
    python hunt.py markets RUH-LIS-RUH

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

## Guards
- Fares mentioning residency/point-of-sale limits never outrank clean ones
- Kiwi self-transfer itineraries flagged: missed connection isn't protected
- Non-bookable sources labelled `link-only` so the digest never implies
  it can hold something it can't

## TODO
- Live FX in `hunt.py: fx_rates()` - **highest value**, stale rates
  silently corrupt every cross-market comparison
- RateHawk region_id resolution via /search/multicomplete
- Almosafer / Wego for ex-KSA routes
