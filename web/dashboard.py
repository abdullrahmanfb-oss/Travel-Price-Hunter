"""
Self-contained HTML dashboard rendered straight from hunter.db.

Two ways in:
    python hunt.py serve  [--port 8787]        live — re-renders per request
    python hunt.py dashboard [--out dashboard.html]   static export

Everything is inline (CSS, JS, SVG charts) so the output is one file that
works anywhere. Charts are single-series 30-day daily lows — economy and
business keep separate panels, same as everywhere else in the system.
"""
import html
import json

from core import clock, compare, countries, watches
from providers import registry
from storage import db


# ---------- data ----------

def collect(cfg=None):
    threshold = (cfg or {}).get("alerts", {}).get("market_edge_pct", 25)
    cards, gaps, alerts = [], [], []

    for w in db.list_watches(active_only=False):
        panels = []
        for var in watches.variants(w):
            hist = db.daily_lows(w["id"], var, 30)
            last = db.latest(w["id"], var)
            if not hist or not last:
                continue
            cur = hist[-1][1]
            real, why, med = compare.is_real_drop(cur, hist, var)
            target = compare.target_for(w, var)

            sa = db.latest_for_pos(w["id"], var, "SA", days=7)
            gap = None
            if sa and last["pos_code"] != "SA" and sa["amount_sar"]:
                gap = round((sa["amount_sar"] - last["amount_sar"])
                            / sa["amount_sar"] * 100, 1)

            badges = []
            if target and cur <= target:
                badges.append(("target", "★ target hit"))
                alerts.append(f'{w["id"]} {var}: {cur:.0f} SAR — at target')
            if real:
                badges.append(("drop", "↓ real drop"))
                alerts.append(f'{w["id"]} {var}: {why}')
            if gap is not None and gap >= threshold:
                badges.append(("gap", "⚑ cheaper abroad"))
                alerts.append(
                    f'{w["id"]} {var}: {last["amount_sar"]:,.0f} SAR bought '
                    f'from {countries.label(last["pos_code"])} vs '
                    f'{sa["amount_sar"]:,.0f} SAR in {countries.label("SA")} '
                    f'(-{gap:.0f}%)')
            if med is None:
                badges.append(("quiet", why))

            panels.append({
                "variant": var, "current": cur, "median": med, "why": why,
                "target": target, "history": hist, "last": last,
                "gap": gap, "sa": sa, "badges": badges,
                "countries": db.country_prices(w["id"], var, 7),
            })
            if gap is not None:
                gaps.append({
                    "watch": w["id"], "variant": var,
                    "best": last["amount_sar"], "pos": last["pos_code"],
                    "provider": last["provider"], "sa": sa["amount_sar"],
                    "gap": gap, "flagged": gap >= threshold,
                })
        matrix = {}
        if w["product"] == "flight":
            for var in watches.variants(w):
                windows = db.latest_matrix(w["id"], var)  # list of row lists
                if windows:
                    matrix[var] = windows

        # a watch with no history yet still belongs on the board
        cards.append({"watch": w, "panels": panels, "matrix": matrix})

    pos_seen = {m["pos_code"] for m in db.market_wins(30)}
    return {
        "cards": cards, "gaps": gaps, "alerts": alerts,
        "holds": db.open_holds(), "market_wins": db.market_wins(30),
        "threshold": threshold, "markets_seen": len(pos_seen),
        "providers": {k: {"active": [(p.NAME, p.BOOKABLE)
                                     for p in registry.active(k)],
                          "missing": registry.missing(k)}
                      for k in ("flight", "hotel", "car")},
    }


# ---------- svg chart ----------

CW, CH = 560, 150
PAD_L, PAD_R, PAD_T, PAD_B = 52, 8, 10, 22


def _chart(points, target, chart_id):
    values = [v for _, v in points]
    dom_lo, dom_hi = min(values), max(values)
    if target:
        dom_lo, dom_hi = min(dom_lo, target), max(dom_hi, target)
    span = (dom_hi - dom_lo) or dom_hi * 0.05 or 1
    dom_lo -= span * 0.10
    dom_hi += span * 0.10

    iw, ih = CW - PAD_L - PAD_R, CH - PAD_T - PAD_B
    n = len(points)

    def x(i):
        return PAD_L + (i * iw / (n - 1) if n > 1 else iw / 2)

    def y(v):
        return PAD_T + (dom_hi - v) / (dom_hi - dom_lo) * ih

    pts = [(round(x(i), 1), round(y(v), 1)) for i, (_, v) in enumerate(points)]
    line = "M" + " L".join(f"{px},{py}" for px, py in pts)
    base = PAD_T + ih
    area = f'{line} L{pts[-1][0]},{base} L{pts[0][0]},{base} Z'

    grid = []
    for frac in (0.25, 0.5, 0.75):
        gy = round(PAD_T + ih * frac, 1)
        gval = dom_hi - (dom_hi - dom_lo) * frac
        grid.append(f'<line class="grid" x1="{PAD_L}" y1="{gy}" '
                    f'x2="{CW - PAD_R}" y2="{gy}"/>'
                    f'<text class="tick" x="{PAD_L - 6}" y="{gy + 3}" '
                    f'text-anchor="end">{gval:,.0f}</text>')

    tline = ""
    if target:
        ty = round(y(target), 1)
        tline = (f'<line class="target" x1="{PAD_L}" y1="{ty}" '
                 f'x2="{CW - PAD_R}" y2="{ty}"/>'
                 f'<text class="tick target-tick" x="{PAD_L + 2}" '
                 f'y="{ty - 4}">target {target:,.0f}</text>')

    d0, d1 = points[0][0][5:], points[-1][0][5:]
    lx, ly = pts[-1]
    data = json.dumps({"points": points, "padL": PAD_L, "padR": PAD_R,
                       "w": CW})
    return f'''<figure class="chart" data-chart="{chart_id}">
<svg viewBox="0 0 {CW} {CH}" preserveAspectRatio="none" role="img"
     aria-label="30-day daily low, SAR">
  {"".join(grid)}
  <path class="area" d="{area}"/>
  {tline}
  <path class="line" d="{line}" pathLength="1"/>
  <line class="xhair" x1="0" y1="{PAD_T}" x2="0" y2="{base}" opacity="0"/>
  <circle class="dot-hover" r="4" opacity="0"/>
  <circle class="dot" cx="{lx}" cy="{ly}" r="3.5"/>
  <text class="tick" x="{PAD_L}" y="{CH - 6}">{d0}</text>
  <text class="tick" x="{CW - PAD_R}" y="{CH - 6}" text-anchor="end">{d1}</text>
</svg>
<div class="tip" hidden></div>
<script type="application/json">{data}</script>
</figure>'''


# ---------- html pieces ----------

def _e(s):
    return html.escape(str(s if s is not None else ""))


BADGE_CLASS = {"target": "b-good", "drop": "b-good", "gap": "b-accent",
               "quiet": "b-muted"}


def _panel(w, p, chart_id):
    cur = p["current"]
    med = p["median"]
    delta = ""
    if med:
        pct = (cur - med) / med * 100
        cls = "down" if pct < 0 else "up"
        delta = (f'<span class="delta {cls}">{pct:+.1f}% vs 30-day '
                 f'median</span>')
    badges = "".join(
        f'<span class="badge {BADGE_CLASS[k]}">{_e(t)}</span>'
        for k, t in p["badges"])
    last = p["last"]
    src = (f'{_e(last["provider"])} · bought from '
           f'{_e(countries.label(last["pos_code"]))} '
           f'· {last["amount_native"]:,.0f} {_e(last["currency"])}')
    home = countries.label("SA")
    gapline = ""
    if p["gap"] is not None:
        sa_amt = p["sa"]["amount_sar"]
        if p["gap"] > 0:
            gtxt = (f'{p["gap"]:.1f}% cheaper than {home} '
                    f'({sa_amt:,.0f} SAR)')
            gcls = "down"
        else:
            gtxt = f'{-p["gap"]:.1f}% above {home} ({sa_amt:,.0f} SAR)'
            gcls = "up"
        gapline = (f'<div class="srcline"><span class="delta {gcls}">'
                   f'{gtxt}</span></div>')
    return f'''<div class="panel">
  <div class="panel-head">
    <span class="variant">{_e(p["variant"])}</span>{badges}
  </div>
  <div class="price"><span class="num">{cur:,.0f}</span>
       <span class="cur">SAR</span> {delta}</div>
  <div class="srcline">{src}</div>
  {gapline}
  {_chart(p["history"], p["target"], chart_id)}
  {_bycountry(p["countries"])}
</div>'''


def _bycountry(rows):
    """Horizontal bars, cheapest country first — compare at a glance."""
    if len(rows) < 2:
        return ""
    mx = max(r["best_sar"] for r in rows)
    out = ['<div class="bycountry">'
           '<div class="bc-title">Price by country · cheapest seen, '
           'last 7 days · SAR</div>']
    for i, r in enumerate(rows):
        chips = ""
        if i == 0:
            chips += '<span class="badge b-good">cheapest</span>'
        if r["pos_code"] == "SA":
            chips += '<span class="badge b-muted">home</span>'
        width = max(2.0, r["best_sar"] / mx * 100)
        home_cls = " home" if r["pos_code"] == "SA" else ""
        out.append(
            f'<div class="bc-row{home_cls}">'
            f'<span class="bc-name">{_e(countries.label(r["pos_code"]))}</span>'
            f'<span class="bc-track"><span class="bc-bar" '
            f'style="width:{width:.1f}%"></span></span>'
            f'<span class="bc-val">{r["best_sar"]:,.0f}</span>'
            f'<span class="bc-chips">{chips}</span></div>')
    out.append('</div>')
    return "".join(out)


def _card(c, idx):
    w = c["watch"]
    status = "" if w["status"] == "active" else \
        f'<span class="badge b-muted">{_e(w["status"])}</span>'
    panels = "".join(_panel(w, p, f'c{idx}-{i}')
                     for i, p in enumerate(c["panels"]))
    if not panels:
        panels = ('<div class="panel"><p class="empty">Watching — awaiting '
                  'the first scan with prices. Scans need provider '
                  'credentials (e.g. <code>IGNAV_TOKEN</code>), then '
                  '<code>python hunt.py scan</code>.</p></div>')
    return f'''<article class="card">
  <header class="card-head">
    <h3>{_e(w["id"])}</h3>
    <span class="route">{_e(watches.describe(w))}</span>
    <span class="chip">{_e(w["product"])}</span>{status}
  </header>
  {panels}
</article>'''


def _one_window(w, var, rows, all_codes):
    quoted = {r["pos_code"] for r in rows}
    mx = max(r["amount_sar"] for r in rows)
    itin = rows[0]["itin_key"]
    carrier = rows[0].get("carrier") or ""
    stops = rows[0].get("stops")
    stops_txt = "direct" if stops == 0 else f'{stops} stop(s)'
    body = []
    for i, r in enumerate(rows):
        chips = ('<span class="badge b-good">cheapest</span>'
                 if i == 0 else "")
        if r["pos_code"] == "SA":
            chips += '<span class="badge b-muted">home</span>'
        width = max(2.0, r["amount_sar"] / mx * 100)
        native = "" if r["currency"] == "SAR" else \
            f' <span class="mx-native">({r["amount_native"]:,.0f} ' \
            f'{_e(r["currency"])})</span>'
        body.append(
            f'<div class="bc-row{" home" if r["pos_code"] == "SA" else ""}">'
            f'<span class="bc-name">'
            f'{_e(countries.label(r["pos_code"]))}</span>'
            f'<span class="bc-track"><span class="bc-bar" '
            f'style="width:{width:.1f}%"></span></span>'
            f'<span class="bc-val">{r["amount_sar"]:,.0f}{native}</span>'
            f'<span class="bc-chips">{chips}</span></div>')
    for code in sorted(set(all_codes) - quoted, key=countries.name):
        body.append(
            f'<div class="bc-row unquoted">'
            f'<span class="bc-name">{_e(countries.label(code))}</span>'
            f'<span class="bc-track"></span>'
            f'<span class="bc-val">—</span>'
            f'<span class="bc-chips"></span></div>')
    return (
        f'<article class="card">'
        f'<header class="card-head"><h3>{_e(w["id"])}</h3>'
        f'<span class="variant">{_e(var)}</span></header>'
        f'<div class="srcline">flight <span class="mx-itin">'
        f'{_e(itin)}</span> · {_e(carrier)} · {stops_txt} · '
        f'quoted in {len(rows)} of {len(all_codes)} markets · '
        f'{_e(rows[0]["seen_at"][:10])} · SAR</div>'
        f'<div class="bycountry">{"".join(body)}</div></article>')


def _matrix_windows(cards):
    """One window per watch/cabin/itinerary: the exact same flight, quoted
    from every market. Unquoted markets stay visible as em-dash rows. A
    scan can leave several windows per cabin — the requested-dates flight
    (full market coverage) plus a flex-date winner."""
    all_codes = list(countries.NAME)
    windows = []
    for c in cards:
        w = c["watch"]
        for var, row_lists in c.get("matrix", {}).items():
            windows += [_one_window(w, var, rows, all_codes)
                        for rows in row_lists]
    if not windows:
        return ('<p class="empty">Appears after the first scan with '
                'provider credentials: the winning flight for each watch, '
                'priced from every market that quoted it — one window per '
                'cabin (economy and business stay separate).</p>')
    return f'<div class="grid">{"".join(windows)}</div>'


def _gap_table(gaps, threshold):
    if not gaps:
        return ('<p class="empty">No cross-market data yet. Once scans '
                'run, each watch shows its best price anywhere next to '
                'the SA price for the same thing.</p>')
    rows = []
    for g in sorted(gaps, key=lambda x: -x["gap"]):
        flag = ('<span class="badge b-accent">⚑ flagged</span>'
                if g["flagged"] else "")
        rows.append(
            f'<tr><td>{_e(g["watch"])}</td><td>{_e(g["variant"])}</td>'
            f'<td class="num-cell">{g["best"]:,.0f}</td>'
            f'<td>{_e(countries.label(g["pos"]))} · {_e(g["provider"])}</td>'
            f'<td class="num-cell">{g["sa"]:,.0f}</td>'
            f'<td class="num-cell">{g["gap"]:+.1f}%</td><td>{flag}</td></tr>')
    home = _e(countries.label("SA"))
    return f'''<div class="scroll"><table>
<thead><tr><th>watch</th><th>cabin</th><th>best now (SAR)</th>
<th>cheapest country</th><th>{home} (SAR)</th><th>saved vs home</th>
<th>≥{threshold:.0f}%</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>'''


def _wins_table(wins):
    if not wins:
        return ('<p class="empty">No scans recorded yet — this fills in '
                'once watches exist and scans run.</p>')
    rows = "".join(
        f'<tr><td>{_e(r["watch_id"])}</td><td>{_e(r["variant"])}</td>'
        f'<td>{_e(countries.label(r["pos_code"]))}</td>'
        f'<td class="num-cell">{r["wins"]}</td>'
        f'<td class="num-cell">{r["best_sar"]:,.0f}</td></tr>'
        for r in wins)
    return f'''<div class="scroll"><table>
<thead><tr><th>watch</th><th>cabin</th><th>country</th>
<th>days it was cheapest (30d)</th><th>best seen (SAR)</th></tr></thead>
<tbody>{rows}</tbody></table></div>'''


def _providers(pv):
    out = []
    for kind, d in pv.items():
        chips = "".join(
            f'<span class="chip ok">{_e(n)}{" ⚑" if b else ""}</span>'
            for n, b in d["active"]) or ""
        chips += "".join(f'<span class="chip idle">{_e(n)}</span>'
                         for n in d["missing"])
        out.append(f'<div class="prov-row"><span class="prov-kind">'
                   f'{_e(kind)}</span>{chips}</div>')
    return "".join(out)


def _holds(holds):
    if not holds:
        return ('<p class="empty">No open holds. A bookable provider can hold '
                'a fare here with its pay-by deadline.</p>')
    rows = "".join(
        f'<tr><td>{_e(h["booking_reference"])}</td><td>{_e(h["variant"])}</td>'
        f'<td class="num-cell">{h["amount_sar"]:,.0f}</td>'
        f'<td class="payby">{_e(h["pay_by"])}</td></tr>' for h in holds)
    return f'''<div class="scroll"><table>
<thead><tr><th>reference</th><th>cabin</th><th>SAR</th>
<th>pay by</th></tr></thead><tbody>{rows}</tbody></table></div>'''


# ---------- assembly ----------

def render(cfg=None) -> str:
    d = collect(cfg)
    n_alerts = len(d["alerts"])
    alert_items = "".join(f'<li>{_e(a)}</li>' for a in d["alerts"])
    action = (f'<section class="action"><h2>Action</h2>'
              f'<ul>{alert_items}</ul></section>') if n_alerts else ""
    cards = "".join(_card(c, i) for i, c in enumerate(d["cards"])) or \
        ('<p class="empty">Nothing is tracked. Add a watch with your own '
         'route and dates:<br><code>python hunt.py flight &lt;id&gt; '
         '--slice ORIGIN:DEST:YYYY-MM-DD</code> — then '
         '<code>python hunt.py scan</code>.</p>')
    n_panels = sum(len(c["panels"]) for c in d["cards"])

    return f'''<title>Fare Hunter</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Sans+Condensed:wght@600&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>{CSS}</style>
<div class="wrap">
<header class="top">
  <h1>FARE HUNTER</h1>
  <span class="date">{clock.now():%a %d %b %Y} · {clock.now():%H:%M} UTC</span>
  <span class="alert-pill{" hot" if n_alerts else ""}">{n_alerts} alert{"s" if n_alerts != 1 else ""}</span>
</header>

<section class="tiles">
  <div class="tile"><span class="t-num">{len(d["cards"])}</span><span class="t-lbl">watches</span></div>
  <div class="tile"><span class="t-num">{n_panels}</span><span class="t-lbl">price tracks</span></div>
  <div class="tile"><span class="t-num">{d["markets_seen"]}</span><span class="t-lbl">markets seen (30d)</span></div>
  <div class="tile"><span class="t-num">{len(d["holds"])}</span><span class="t-lbl">open holds</span></div>
</section>

{action}

<section><h2>Watches</h2><div class="grid">{cards}</div></section>

<section><h2>Same flight, every market</h2>
<p class="note">The exact same flight (same flight numbers, same dates),
priced from each point of sale. Markets showing — didn't quote this
flight in the latest scan. Prices normalised to SAR.</p>
{_matrix_windows(d["cards"])}</section>

<section><h2>Saudi price gap</h2>
<p class="note">Best current price anywhere vs the latest price in
{countries.label("SA")} for the same thing, after SAR normalisation.
Gaps of {d["threshold"]:.0f}%+ are flagged.</p>
{_gap_table(d["gaps"], d["threshold"])}</section>

<section><h2>Which country wins</h2>{_wins_table(d["market_wins"])}</section>

<section><h2>Holds — awaiting your payment</h2>{_holds(d["holds"])}</section>

<section><h2>Providers</h2>{_providers(d["providers"])}
<p class="note">⚑ = bookable (can hold). Others are price-discovery +
link. Greyed providers have no credentials configured.</p></section>

<footer>No card is stored by this system — holds lapse unpaid.
· Rendered {clock.iso()}</footer>
</div>
<script>{JS}</script>
'''


CSS = '''
:root {
  color-scheme: light;
  --bg: #f7f6f3; --card: #fcfcfb; --ink: #1c1b18; --ink-2: #52514e;
  --ink-3: #8a887f; --hair: #e5e3dc; --accent: #2a78d6;
  --accent-soft: rgba(42,120,214,.10); --good: #0ca30c;
  --good-soft: rgba(12,163,12,.10); --warn: #fab219; --crit: #d03b3b;
  --chart-grid: #eceae4;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --bg: #141413; --card: #1a1a19; --ink: #f2f1ec; --ink-2: #c3c2b7;
    --ink-3: #8b8a80; --hair: #2e2d2a; --accent: #3987e5;
    --accent-soft: rgba(57,135,229,.16); --good: #3dbb3d;
    --good-soft: rgba(61,187,61,.14); --warn: #fab219; --crit: #e66767;
    --chart-grid: #262521;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #141413; --card: #1a1a19; --ink: #f2f1ec; --ink-2: #c3c2b7;
  --ink-3: #8b8a80; --hair: #2e2d2a; --accent: #3987e5;
  --accent-soft: rgba(57,135,229,.16); --good: #3dbb3d;
  --good-soft: rgba(61,187,61,.14); --warn: #fab219; --crit: #e66767;
  --chart-grid: #262521;
}
* { box-sizing: border-box; }
body { background: var(--bg); color: var(--ink); margin: 0;
  font: 15px/1.5 "IBM Plex Sans", "Segoe UI", system-ui, sans-serif; }
.wrap { max-width: 1180px; margin: 0 auto; padding: 20px 24px 48px; }

.top { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap;
  padding: 10px 0 16px; border-bottom: 2px solid var(--ink); }
.top h1 { margin: 0; font: 600 26px/1 "IBM Plex Sans Condensed",
  "Arial Narrow", sans-serif; letter-spacing: .06em; }
.date { color: var(--ink-2); font-size: 13px; }
.alert-pill { margin-left: auto; font: 500 12px/1 "IBM Plex Mono", monospace;
  padding: 6px 10px; border-radius: 999px; border: 1px solid var(--hair);
  color: var(--ink-2); }
.alert-pill.hot { background: var(--good-soft); border-color: var(--good);
  color: var(--good); }

.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px,1fr));
  gap: 10px; margin: 18px 0; }
.tile { background: var(--card); border: 1px solid var(--hair);
  border-radius: 8px; padding: 14px 16px; display: flex;
  flex-direction: column; gap: 2px; }
.t-num { font: 600 26px/1.1 "IBM Plex Mono", monospace;
  font-variant-numeric: tabular-nums; }
.t-lbl { color: var(--ink-2); font-size: 12px; text-transform: uppercase;
  letter-spacing: .05em; }

section { margin: 26px 0; }
h2 { font: 600 13px/1 "IBM Plex Sans Condensed", "Arial Narrow", sans-serif;
  text-transform: uppercase; letter-spacing: .12em; color: var(--ink-2);
  margin: 0 0 10px; }
.note { color: var(--ink-3); font-size: 13px; margin: -4px 0 10px;
  max-width: 62ch; }
.empty { color: var(--ink-3); font-size: 14px; line-height: 2; }
.empty code { font: 12.5px "IBM Plex Mono", monospace;
  background: var(--accent-soft); color: var(--ink-2);
  padding: 2px 6px; border-radius: 4px; }

.action { background: var(--good-soft); border: 1px solid var(--good);
  border-radius: 8px; padding: 12px 18px; }
.action h2 { color: var(--good); margin-bottom: 6px; }
.action ul { margin: 0; padding-left: 18px; }
.action li { font: 500 14px/1.7 "IBM Plex Mono", monospace;
  font-variant-numeric: tabular-nums; }

.grid { display: grid; grid-template-columns:
  repeat(auto-fit, minmax(min(430px,100%), 1fr)); gap: 14px; }
.card { background: var(--card); border: 1px solid var(--hair);
  border-radius: 10px; padding: 16px 18px; min-width: 0; }
.card-head { display: flex; align-items: baseline; gap: 10px;
  flex-wrap: wrap; margin-bottom: 4px; }
.card-head h3 { margin: 0; font-size: 17px; font-weight: 600; }
.route { color: var(--ink-2); font: 400 13px "IBM Plex Mono", monospace; }
.chip { font: 500 11px/1 "IBM Plex Mono", monospace; padding: 4px 8px;
  border-radius: 999px; border: 1px solid var(--hair); color: var(--ink-2); }
.chip.ok { border-color: var(--good); color: var(--good); }
.chip.idle { color: var(--ink-3); }

.panel { border-top: 1px solid var(--hair); padding-top: 12px;
  margin-top: 12px; }
.panel-head { display: flex; gap: 8px; align-items: center;
  flex-wrap: wrap; }
.variant { font: 600 12px/1 "IBM Plex Sans Condensed", sans-serif;
  text-transform: uppercase; letter-spacing: .1em; color: var(--ink-2); }
.badge { font: 500 11px/1 "IBM Plex Mono", monospace; padding: 4px 8px;
  border-radius: 4px; }
.b-good { background: var(--good-soft); color: var(--good); }
.b-accent { background: var(--accent-soft); color: var(--accent); }
.b-muted { background: transparent; border: 1px solid var(--hair);
  color: var(--ink-3); }
.price { margin: 6px 0 2px; display: flex; align-items: baseline; gap: 8px;
  flex-wrap: wrap; }
.price .num { font: 600 30px/1 "IBM Plex Mono", monospace;
  font-variant-numeric: tabular-nums; }
.price .cur { color: var(--ink-2); font-size: 13px; }
.delta { font: 500 12px "IBM Plex Mono", monospace; color: var(--ink-2); }
.delta.down { color: var(--good); }
.delta.up { color: var(--ink-3); }
.srcline { color: var(--ink-2); font-size: 12.5px; margin-bottom: 8px; }

.chart { margin: 6px 0 0; position: relative; }
.chart svg { width: 100%; height: 130px; display: block; }
.chart .grid { stroke: var(--chart-grid); stroke-width: 1; }
.chart .area { fill: var(--accent); opacity: .12; }
.chart .line { stroke: var(--accent); stroke-width: 2; fill: none;
  stroke-linejoin: round; vector-effect: non-scaling-stroke; }
.chart .dot { fill: var(--accent); stroke: var(--card); stroke-width: 2; }
.chart .dot-hover { fill: var(--accent); stroke: var(--card);
  stroke-width: 2; }
.chart .xhair { stroke: var(--ink-3); stroke-width: 1;
  stroke-dasharray: 2 3; vector-effect: non-scaling-stroke; }
.chart .target { stroke: var(--good); stroke-width: 1.5;
  stroke-dasharray: 5 4; vector-effect: non-scaling-stroke; }
.chart .tick { font: 10px "IBM Plex Mono", monospace; fill: var(--ink-3); }
.chart .target-tick { fill: var(--good); }
.tip { position: absolute; pointer-events: none; background: var(--ink);
  color: var(--bg); font: 500 12px/1.4 "IBM Plex Mono", monospace;
  padding: 5px 8px; border-radius: 5px; transform: translate(-50%, -130%);
  white-space: nowrap; z-index: 3; }

.bycountry { margin-top: 14px; display: grid; gap: 5px; }
.bc-title { font: 600 10.5px "IBM Plex Sans Condensed", "Arial Narrow",
  sans-serif; text-transform: uppercase; letter-spacing: .08em;
  color: var(--ink-3); margin-bottom: 2px; }
.bc-row { display: grid;
  grid-template-columns: minmax(110px, 150px) 1fr 64px auto;
  gap: 8px; align-items: center; font-size: 12.5px; min-width: 0; }
.bc-name { color: var(--ink-2); overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.bc-track { background: var(--chart-grid); border-radius: 3px;
  height: 10px; overflow: hidden; }
.bc-bar { display: block; height: 100%; background: var(--accent);
  border-radius: 3px; }
.bc-row.home .bc-bar { opacity: .45; }
.bc-val { font: 500 12px "IBM Plex Mono", monospace;
  font-variant-numeric: tabular-nums; text-align: right; }
.bc-chips { display: flex; gap: 4px; min-width: 68px; }
.bc-row.unquoted { opacity: .45; }
.mx-itin { font: 500 12.5px "IBM Plex Mono", monospace; color: var(--ink); }
.mx-native { color: var(--ink-3); font-size: 11px; }

.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 13.5px;
  background: var(--card); border: 1px solid var(--hair);
  border-radius: 8px; }
th, td { text-align: left; padding: 8px 12px;
  border-bottom: 1px solid var(--hair); }
th { font: 600 11px "IBM Plex Sans Condensed", sans-serif;
  text-transform: uppercase; letter-spacing: .08em; color: var(--ink-2); }
tr:last-child td { border-bottom: none; }
.num-cell { font-family: "IBM Plex Mono", monospace;
  font-variant-numeric: tabular-nums; }
.payby { color: var(--crit); font: 500 13px "IBM Plex Mono", monospace; }

.prov-row { display: flex; gap: 8px; align-items: center; margin: 6px 0;
  flex-wrap: wrap; }
.prov-kind { font: 600 12px "IBM Plex Sans Condensed", sans-serif;
  text-transform: uppercase; letter-spacing: .1em; color: var(--ink-2);
  min-width: 56px; }

footer { margin-top: 36px; padding-top: 14px;
  border-top: 1px solid var(--hair); color: var(--ink-3); font-size: 12px; }
@media (prefers-reduced-motion: no-preference) {
  .badge, .tile { transition: background .15s; }
}
'''


JS = '''
document.querySelectorAll(".chart").forEach(function (fig) {
  var cfg = JSON.parse(fig.querySelector("script").textContent);
  var svg = fig.querySelector("svg"), tip = fig.querySelector(".tip");
  var xhair = fig.querySelector(".xhair"), dot = fig.querySelector(".dot-hover");
  var pts = cfg.points, n = pts.length;
  var iw = cfg.w - cfg.padL - cfg.padR;
  var line = svg.querySelector(".line");
  var coords = line.getAttribute("d").slice(1).split(" L")
    .map(function (p) { return p.split(",").map(Number); });
  svg.addEventListener("mousemove", function (ev) {
    var r = svg.getBoundingClientRect();
    var fx = ((ev.clientX - r.left) / r.width * cfg.w - cfg.padL) / iw;
    var i = Math.max(0, Math.min(n - 1, Math.round(fx * (n - 1))));
    var c = coords[i];
    xhair.setAttribute("x1", c[0]); xhair.setAttribute("x2", c[0]);
    xhair.setAttribute("opacity", 1);
    dot.setAttribute("cx", c[0]); dot.setAttribute("cy", c[1]);
    dot.setAttribute("opacity", 1);
    tip.hidden = false;
    tip.textContent = pts[i][0].slice(5) + " \\u00b7 " +
      Math.round(pts[i][1]).toLocaleString() + " SAR";
    tip.style.left = (c[0] / cfg.w * 100) + "%";
    tip.style.top = (c[1] / svg.viewBox.baseVal.height * r.height) + "px";
  });
  svg.addEventListener("mouseleave", function () {
    tip.hidden = true;
    xhair.setAttribute("opacity", 0); dot.setAttribute("opacity", 0);
  });
});
'''
