# Fare Hunter — Claude Code guide

## Shape
Ad-hoc watches (SQLite, not config) across 28 markets. Two-phase search
keeps call volume flat. Daily digest email. Hold-then-human-pays booking.

```
hunt.py            CLI: add/list/scan/digest/markets/pause/holds
simulate.py        offline: seeds history, prints a digest, no keys needed
config.yaml        markets, search tuning, digest hour
core/
  watches.py       date-model expansion, capped at 40 pairs/scan
  search.py        two-phase fan-out, rate limiter, market pruning
  compare.py       FX normalise, filters, per-cabin drop detection
  digest.py        daily email builder
  clock.py         all time goes through here (tz-aware UTC)
storage/db.py      watches, price_history, market_stats, holds
providers/duffel.py  search + hold orders
```

## Hard rules
1. **Never add card storage, card entry, or auto-charge.** The design is
   hold-then-human-pays. If a task seems to need a card, stop and ask.
2. **Never bypass CAPTCHA, bot detection, or rate limits.** If a provider
   blocks us we swap providers or back off. POS and currency are passed
   as API parameters — that is the supported path and it already works.
3. Official APIs only. No headless-browser automation of checkout flows.
4. Normalise to SAR before any comparison. Never compare raw amounts
   across currencies.
5. Economy and business keep separate baselines, thresholds, and targets
   everywhere. Do not collapse them.
6. Every hold records `pay_by` and it must appear in the digest.
7. Use `core.clock` for time, never `datetime.utcnow()`.

## Gotchas
- `watches._flex` deliberately preserves trip length instead of crossing
  departures with returns. Restoring the cross product reintroduces the
  combinatorial blowup that made v1 unusable.
- `_thin()` samples long windows evenly rather than truncating, so a
  90-day rolling watch still covers the full window every scan.
- `is_real_drop` needs 5 days of history. This is intentional — it stops
  a brand-new watch from firing an alert off a single sample.
- Restricted fares never outrank clean ones in `rank()`, regardless of
  price. Don't "optimise" that sort away.

## Testing without keys
`python simulate.py` seeds 31 days across 6 watch/cabin pairs and prints
the digest. Use it to verify any change to compare.py or digest.py.

## v3 additions
- Products: flight | hotel | car. `watches.product` switches behaviour.
- Flights store a SLICE LIST: 1=one-way, 2=round, 3+=multi-city.
  `_shift()` moves the whole trip together - never flex slices
  independently, it produces nonsense itineraries.
- `providers/registry.py` fans out across sources and dedupes by
  itinerary/hotel/vendor key, keeping cheapest + recording `also_seen`.
- Cold markets are RE-PROBED every 7 days (db.due_for_reprobe). Do not
  reintroduce permanent pruning.
- `bump_market` stamps `last_probe` on every scan. Removing that makes
  newly-cold markets look instantly overdue and pruning stops working.
- Never claim a non-bookable provider can hold. `BOOKABLE` gates it.
