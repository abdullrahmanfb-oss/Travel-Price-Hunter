"""SQLite store: watches (flight/hotel/car), history, market stats, holds."""
import json
import os
import sqlite3
from datetime import timedelta
from pathlib import Path

from core import clock

DB = Path(os.environ.get("HUNTER_DB",
                         Path(__file__).resolve().parent.parent / "hunter.db"))


def use(path):
    """Point the store somewhere else (e.g. simulate.py's sandbox DB)."""
    global DB
    DB = Path(path)

SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id            TEXT PRIMARY KEY,
    product       TEXT NOT NULL DEFAULT 'flight',   -- flight|hotel|car
    -- flight
    trip_type     TEXT,                -- oneway|round|multi
    slices_json   TEXT,                -- [{origin,destination,date}]
    cabins        TEXT DEFAULT 'economy,business',
    max_stops     INTEGER DEFAULT 2,
    exclude       TEXT,
    airlines      TEXT,                -- only these carriers, e.g. 'SV'
    -- hotel
    city          TEXT,
    checkin       TEXT,
    checkout      TEXT,
    rooms         INTEGER DEFAULT 1,
    min_stars     REAL,
    refundable_only INTEGER DEFAULT 0,
    -- car
    pickup_location TEXT,
    pickup_at     TEXT,
    dropoff_at    TEXT,
    car_category  TEXT,
    -- shared
    date_model    TEXT NOT NULL DEFAULT 'fixed',
    flex_days     INTEGER DEFAULT 0,
    month         TEXT,
    rolling_days  INTEGER,
    nights        INTEGER,
    adults        INTEGER DEFAULT 1,
    target_eco    REAL,
    target_biz    REAL,
    target        REAL,                -- hotels/cars single target
    status        TEXT DEFAULT 'active',
    created_at    TEXT
);

CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id    TEXT NOT NULL,
    product     TEXT NOT NULL,
    variant     TEXT NOT NULL,        -- cabin for flights, 'std' otherwise
    provider    TEXT NOT NULL,
    pos_code    TEXT NOT NULL,
    currency    TEXT NOT NULL,
    amount_native REAL NOT NULL,
    amount_sar  REAL NOT NULL,
    label       TEXT,                 -- carrier / hotel name / vendor
    detail      TEXT,                 -- json blob for the digest
    source_count INTEGER DEFAULT 1,
    flags       TEXT,
    offer_id    TEXT,
    seen_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_hist ON price_history(watch_id, variant, seen_at);

-- one specific flight (same flight numbers + dates), quoted from every
-- market that priced it in a scan; one snapshot per scan per cabin
CREATE TABLE IF NOT EXISTS flight_matrix (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id    TEXT NOT NULL,
    variant     TEXT NOT NULL,
    itin_key    TEXT NOT NULL,       -- 'LH633+LH1172' style label
    carrier     TEXT,
    stops       INTEGER,
    pos_code    TEXT NOT NULL,
    currency    TEXT NOT NULL,
    amount_native REAL NOT NULL,
    amount_sar  REAL NOT NULL,
    seen_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_matrix
    ON flight_matrix(watch_id, variant, seen_at);

CREATE TABLE IF NOT EXISTS market_stats (
    route     TEXT NOT NULL,
    pos_code  TEXT NOT NULL,
    scans     INTEGER DEFAULT 0,
    wins      INTEGER DEFAULT 0,
    best_edge REAL DEFAULT 0,
    last_win  TEXT,
    last_probe TEXT,
    PRIMARY KEY (route, pos_code)
);

CREATE TABLE IF NOT EXISTS provider_stats (
    route     TEXT NOT NULL,
    provider  TEXT NOT NULL,
    scans     INTEGER DEFAULT 0,
    wins      INTEGER DEFAULT 0,
    errors    INTEGER DEFAULT 0,
    PRIMARY KEY (route, provider)
);

CREATE TABLE IF NOT EXISTS holds (
    order_id   TEXT PRIMARY KEY,
    watch_id   TEXT,
    provider   TEXT,
    booking_reference TEXT,
    variant    TEXT,
    amount_sar REAL,
    currency   TEXT,
    pay_by     TEXT,
    status     TEXT DEFAULT 'awaiting_payment',
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS digest_log (
    sent_date TEXT PRIMARY KEY,
    sent_at   TEXT
);
"""


def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    # CREATE IF NOT EXISTS never alters an existing table, so columns added
    # after a DB was first created need an explicit migration.
    try:
        c.execute("ALTER TABLE watches ADD COLUMN airlines TEXT")
    except sqlite3.OperationalError:
        pass                      # column already there
    return c


# ---------- watches ----------

def add_watch(w: dict):
    cols = ",".join(w.keys())
    marks = ",".join("?" * len(w))
    with conn() as c:
        c.execute(f"INSERT OR REPLACE INTO watches ({cols}) VALUES ({marks})",
                  tuple(w.values()))


def list_watches(active_only=True, product=None):
    q, args = "SELECT * FROM watches WHERE 1=1", []
    if active_only:
        q += " AND status='active'"
    if product:
        q += " AND product=?"
        args.append(product)
    with conn() as c:
        rows = [dict(r) for r in c.execute(q + " ORDER BY created_at", args)]
    for r in rows:
        r["slices"] = json.loads(r["slices_json"]) if r.get("slices_json") else []
    return rows


def set_status(watch_id, status):
    with conn() as c:
        c.execute("UPDATE watches SET status=? WHERE id=?", (status, watch_id))


def delete_watch(watch_id):
    with conn() as c:
        c.execute("DELETE FROM watches WHERE id=?", (watch_id,))
        c.execute("DELETE FROM price_history WHERE watch_id=?", (watch_id,))


# ---------- prices ----------

def record(watch_id, product, variant, offer):
    with conn() as c:
        c.execute(
            """INSERT INTO price_history
               (watch_id,product,variant,provider,pos_code,currency,
                amount_native,amount_sar,label,detail,source_count,flags,
                offer_id,seen_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (watch_id, product, variant, offer["provider"],
             offer["pos"]["code"], offer["currency"], offer["amount"],
             offer["sar_est"], offer.get("label"),
             json.dumps(offer.get("detail", {}), default=str),
             offer.get("source_count", 1),
             "|".join(offer.get("flags", [])), offer.get("offer_id"),
             clock.iso()))


def daily_lows(watch_id, variant, days=30):
    cutoff = (clock.now() - timedelta(days=days)).isoformat()
    with conn() as c:
        return [(r["d"], r["low"]) for r in c.execute(
            """SELECT substr(seen_at,1,10) d, MIN(amount_sar) low
               FROM price_history
               WHERE watch_id=? AND variant=? AND seen_at>?
               GROUP BY d ORDER BY d""", (watch_id, variant, cutoff))]


def latest(watch_id, variant):
    """Cheapest offer from the most recent day with data.
    A day can hold reference samples too (e.g. the SA price recorded
    alongside a cheaper market), so 'newest row' is not 'the best'.
    """
    with conn() as c:
        r = c.execute(
            """SELECT * FROM price_history WHERE watch_id=? AND variant=?
               AND substr(seen_at,1,10) =
                   (SELECT substr(MAX(seen_at),1,10) FROM price_history
                    WHERE watch_id=? AND variant=?)
               ORDER BY amount_sar ASC LIMIT 1""",
            (watch_id, variant, watch_id, variant)).fetchone()
    return dict(r) if r else None


def latest_for_pos(watch_id, variant, pos_code, days=7):
    """Cheapest sample from the most recent day that has this market."""
    cutoff = (clock.now() - timedelta(days=days)).isoformat()
    with conn() as c:
        r = c.execute(
            """SELECT * FROM price_history
               WHERE watch_id=? AND variant=? AND pos_code=? AND seen_at>?
               ORDER BY substr(seen_at,1,10) DESC, amount_sar ASC LIMIT 1""",
            (watch_id, variant, pos_code, cutoff)).fetchone()
    return dict(r) if r else None


def market_wins(days=30):
    """How often each market produced the daily low, from history.
    Counts only rows that WERE the day's low — reference samples
    (e.g. the SA price recorded alongside a cheaper market) don't count.
    """
    cutoff = (clock.now() - timedelta(days=days)).isoformat()
    with conn() as c:
        return [dict(r) for r in c.execute(
            """SELECT h.watch_id, h.variant, h.pos_code, COUNT(*) wins,
                      MIN(h.amount_sar) best_sar
               FROM price_history h
               JOIN (SELECT watch_id, variant, substr(seen_at,1,10) d,
                            MIN(amount_sar) low
                     FROM price_history WHERE seen_at>?
                     GROUP BY 1,2,3) m
                 ON m.watch_id=h.watch_id AND m.variant=h.variant
                AND substr(h.seen_at,1,10)=m.d AND h.amount_sar=m.low
               WHERE h.seen_at>?
               GROUP BY 1,2,3
               ORDER BY 1,2,4 DESC""", (cutoff, cutoff))]


def country_prices(watch_id, variant, days=7):
    """Cheapest recent price per country, ascending — the one query
    behind every 'compare countries' view (digest line, dashboard bars).
    """
    cutoff = (clock.now() - timedelta(days=days)).isoformat()
    with conn() as c:
        return [dict(r) for r in c.execute(
            """SELECT pos_code, MIN(amount_sar) best_sar,
                      MAX(substr(seen_at,1,10)) last_seen
               FROM price_history
               WHERE watch_id=? AND variant=? AND seen_at>?
               GROUP BY pos_code ORDER BY best_sar ASC""",
            (watch_id, variant, cutoff))]


def previous_best(watch_id, variant):
    with conn() as c:
        r = c.execute(
            """SELECT MIN(amount_sar) low FROM price_history
               WHERE watch_id=? AND variant=? AND substr(seen_at,1,10)<?""",
            (watch_id, variant, clock.today())).fetchone()
    return r["low"] if r and r["low"] is not None else None


def record_matrix(watch_id, variant, itin_key, carrier, rows, at=None):
    """One snapshot: the same flight quoted from every market that
    priced it this scan. `rows`: [{pos_code, currency, amount_native,
    amount_sar, stops}]. Pass the same `at` when recording several
    windows in one scan, so latest_matrix sees them as one batch."""
    now = at or clock.iso()
    with conn() as c:
        for r in rows:
            c.execute(
                """INSERT INTO flight_matrix
                   (watch_id,variant,itin_key,carrier,stops,pos_code,
                    currency,amount_native,amount_sar,seen_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (watch_id, variant, itin_key, carrier, r.get("stops"),
                 r["pos_code"], r["currency"], r["amount_native"],
                 r["amount_sar"], now))


def latest_matrix(watch_id, variant):
    """The most recent scan's same-flight snapshots, as a list of
    windows — one row list per itinerary, each cheapest first. A scan can
    record several windows (e.g. the requested-dates flight, quoted by
    every market, plus a flex-date winner only the deep pass priced), so
    widest coverage comes first."""
    with conn() as c:
        r = c.execute(
            """SELECT MAX(seen_at) m FROM flight_matrix
               WHERE watch_id=? AND variant=?""",
            (watch_id, variant)).fetchone()
        if not r or not r["m"]:
            return []
        rows = [dict(x) for x in c.execute(
            """SELECT * FROM flight_matrix
               WHERE watch_id=? AND variant=? AND seen_at=?
               ORDER BY amount_sar ASC""",
            (watch_id, variant, r["m"]))]
    windows = {}
    for row in rows:
        windows.setdefault(row["itin_key"], []).append(row)
    return sorted(windows.values(), key=lambda w: (-len(w), w[0]["itin_key"]))


# ---------- market pruning ----------

def bump_market(route, pos_code, won=False, edge=0.0):
    """
    Record that this market was scanned. `last_probe` is stamped every
    time, because a scan IS a probe — without this a market that just
    went cold would look overdue for re-probe immediately and pruning
    would never take effect.
    """
    now = clock.iso()
    with conn() as c:
        c.execute("""INSERT INTO market_stats
                       (route,pos_code,scans,wins,best_edge,last_probe)
                     VALUES(?,?,1,?,?,?)
                     ON CONFLICT(route,pos_code) DO UPDATE SET
                       scans=scans+1, wins=wins+?,
                       best_edge=MAX(best_edge,?), last_probe=?,
                       last_win=CASE WHEN ?=1 THEN ? ELSE last_win END""",
                  (route, pos_code, int(won), edge, now,
                   int(won), edge, now, int(won), now))


def _never_won(route, min_scans):
    with conn() as c:
        return {r["pos_code"]: r["last_probe"] for r in c.execute(
            """SELECT pos_code, last_probe FROM market_stats
               WHERE route=? AND scans>=? AND wins=0""",
            (route, min_scans))}


def due_for_reprobe(route, min_scans=8, reprobe_days=7) -> set[str]:
    """Cold markets whose re-probe window has elapsed."""
    cutoff = (clock.now() - timedelta(days=reprobe_days)).isoformat()
    return {p for p, last in _never_won(route, min_scans).items()
            if last is not None and last <= cutoff}


def cold_markets(route, min_scans=8, reprobe_days=7) -> set[str]:
    """
    Markets to SKIP = never-won MINUS those due for re-probe.

    Permanent pruning was a bug: airlines re-file fares seasonally, so a
    market that lost all summer can start winning in autumn.
    """
    return set(_never_won(route, min_scans)) - \
        due_for_reprobe(route, min_scans, reprobe_days)


def mark_reprobed(route, pos_codes):
    """Reset the counter so a re-probed market gets a fresh trial."""
    if not pos_codes:
        return
    with conn() as c:
        for p in pos_codes:
            c.execute("""UPDATE market_stats SET last_probe=?, scans=0
                         WHERE route=? AND pos_code=?""",
                      (clock.iso(), route, p))


def market_report(route):
    with conn() as c:
        return [dict(r) for r in c.execute(
            """SELECT * FROM market_stats WHERE route=?
               ORDER BY wins DESC, best_edge DESC""", (route,))]


# ---------- provider stats ----------

def bump_provider(route, provider, won=False, error=False):
    with conn() as c:
        c.execute("""INSERT INTO provider_stats(route,provider,scans,wins,errors)
                     VALUES(?,?,1,?,?)
                     ON CONFLICT(route,provider) DO UPDATE SET
                       scans=scans+1, wins=wins+?, errors=errors+?""",
                  (route, provider, int(won), int(error),
                   int(won), int(error)))


def provider_report(route=None):
    q, args = "SELECT * FROM provider_stats", []
    if route:
        q += " WHERE route=?"
        args.append(route)
    with conn() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY wins DESC", args)]


# ---------- holds / digest ----------

def save_hold(watch_id, hold, variant, amount_sar, provider):
    with conn() as c:
        c.execute("""INSERT OR REPLACE INTO holds
            (order_id,watch_id,provider,booking_reference,variant,
             amount_sar,currency,pay_by,status,created_at)
            VALUES (?,?,?,?,?,?,?,?,'awaiting_payment',?)""",
                  (hold["order_id"], watch_id, provider,
                   hold.get("booking_reference"), variant, amount_sar,
                   hold["currency"], hold.get("pay_by"), clock.iso()))


def open_holds():
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM holds WHERE status='awaiting_payment'")]


def digest_sent_today() -> bool:
    with conn() as c:
        return c.execute("SELECT 1 FROM digest_log WHERE sent_date=?",
                         (clock.today(),)).fetchone() is not None


def mark_digest_sent():
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO digest_log VALUES (?,?)",
                  (clock.today(), clock.iso()))
