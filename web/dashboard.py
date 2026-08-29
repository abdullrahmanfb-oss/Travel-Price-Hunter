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
from urllib.parse import quote_plus

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

            try:
                det = json.loads(last.get("detail") or "{}")
            except (TypeError, ValueError):
                det = {}
            panels.append({
                "variant": var, "current": cur, "median": med, "why": why,
                "target": target, "history": hist, "last": last,
                "gap": gap, "sa": sa, "badges": badges, "detail": det,
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
    det = p.get("detail") or {}
    src = (f'{_e(last["provider"])} · bought from '
           f'{_e(countries.label(last["pos_code"]))} '
           f'· {last["amount_native"]:,.0f} {_e(last["currency"])}')
    if det.get("deep_link"):
        via = det.get("link_via") or last["provider"]
        src += (f' · <a class="booklink" href="{_e(det["deep_link"])}" '
                f'target="_blank" rel="noopener">book via {_e(via)} ↗</a>')
    # what the big number IS: total for the whole trip, party size, dates
    trip = {"round": "round trip", "oneway": "one-way",
            "multi": "multi-city"}.get(w.get("trip_type") or "",
                                       w.get("trip_type") or "trip")
    n = w.get("adults") or 1
    meta = f'total for the {trip} · {n} adult{"s" if n > 1 else ""}'
    dates = det.get("dates") or []
    if dates:
        meta += " · " + " → ".join(_e(d) for d in dates)
    stops = det.get("stops")
    if stops is not None:
        meta += " · " + ("direct" if stops == 0 else f"{stops} stop(s)")
    if det.get("via"):
        meta += f' · {_e(det["via"])}'
    metaline = f'<div class="srcline">{meta}</div>'
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
  {metaline}
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
    route_window = itin in ("cheapest-any", "gulf-any", "fastest-any") \
        or itin.startswith("pure-")
    if itin == "cheapest-any":
        head = (f'cheapest for the route from each country — any airline, '
                f'any date in the flex window · '
                f'{len(rows)} of {len(all_codes)} markets quoted · '
                f'{_e(rows[0]["seen_at"][:10])} · trip totals in SAR')
    elif itin == "gulf-any":
        head = (f'cheapest Gulf-airlines ticket from each country — any '
                f'date in the flex window · '
                f'{len(rows)} of {len(all_codes)} markets quoted · '
                f'{_e(rows[0]["seen_at"][:10])} · trip totals in SAR')
    elif itin == "fastest-any":
        head = (f'shortest trip from each country, at the cheapest fare '
                f'that buys it · '
                f'{len(rows)} of {len(all_codes)} markets quoted · '
                f'{_e(rows[0]["seen_at"][:10])} · trip totals in SAR')
    elif itin.startswith("pure-"):
        code = itin[5:]
        name = AIRLINE_NAMES.get(code, code)
        head = (f'cheapest all-{_e(name)} ticket from each country — every '
                f'segment on {_e(name)} · '
                f'{len(rows)} of {len(all_codes)} markets quoted · '
                f'{_e(rows[0]["seen_at"][:10])} · trip totals in SAR')
    else:
        carrier = rows[0].get("carrier") or ""
        stops = rows[0].get("stops")
        stops_txt = "direct" if stops == 0 else f'{stops} stop(s)'
        via = f' · {_e(rows[0]["via"])}' if rows[0].get("via") else ""
        head = (f'same exact flight everywhere: <span class="mx-itin">'
                f'{_e(itin)}</span> · {_e(carrier)} · {stops_txt}{via} · '
                f'quoted in {len(rows)} of {len(all_codes)} markets · '
                f'{_e(rows[0]["seen_at"][:10])} · trip totals in SAR')
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
        if route_window and r.get("flight"):
            # each market's price may come from a different flight/date
            native += (f' <span class="mx-native">· {_e(r["flight"])}'
                       f'{" · " + _e(r["dates"]) if r.get("dates") else ""}'
                       f'{" · " + _e(r["via"]) if r.get("via") else ""}'
                       f'</span>')
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
        f'<div class="srcline">{head}</div>'
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


# ---------- Power BI-style overview + views ----------

def _fmt_dur(m):
    if not m:
        return "—"
    return f"{int(m) // 60}h {int(m) % 60:02d}m"


def _fmt_dates(d):
    return _e((d or "").replace("/", " → ")) or "—"


def _stops_txt(r):
    s = r.get("stops")
    if s is None:
        return ""
    return "direct" if s == 0 else f'{s} stop{"s" if s > 1 else ""}'


# Readable names for the airline filter chips; fallback is the raw code.
AIRLINE_NAMES = {
    "SV": "Saudia", "XY": "flynas", "F3": "flyadeal", "EK": "Emirates",
    "FZ": "flydubai", "EY": "Etihad", "G9": "Air Arabia", "QR": "Qatar",
    "GF": "Gulf Air", "J9": "Jazeera", "KU": "Kuwait", "WY": "Oman Air",
    "A3": "Aegean", "TK": "Turkish", "AF": "Air France", "KL": "KLM",
    "LH": "Lufthansa", "TP": "TAP", "AZ": "ITA", "BA": "British",
    "MS": "EgyptAir", "RJ": "Royal Jordanian", "LX": "Swiss",
    "OS": "Austrian", "IB": "Iberia", "UX": "Air Europa", "W6": "Wizz",
    "PC": "Pegasus", "SN": "Brussels",
}


def _row_codes(flight):
    """Carrier codes on a ticket, from its flight label
    ('A3953+A3722+EY100+EY559' -> ['A3', 'EY']). First two chars of the
    designator — never strip digits (A3, W6)."""
    out = []
    for f in (flight or "").split("+"):
        f = f.strip()
        if len(f) >= 3 and f[:2].upper() not in out:
            out.append(f[:2].upper())
    return out


def _view_data(cards):
    """Pull the four view row-sets (cheapest-any / gulf-any per cabin)
    from the latest scan's matrix windows, plus the watch's route
    (origin, destination) for fallback search links. Rows arrive
    cheapest-first."""
    views, route = {}, (None, None)
    for c in cards:
        if c["watch"]["product"] != "flight":
            continue
        slices = c["watch"].get("slices") or []
        if slices:
            route = (slices[0].get("origin"), slices[0].get("destination"))
        for var, row_lists in c.get("matrix", {}).items():
            for rows in row_lists:
                key = rows[0]["itin_key"]
                if key == "cheapest-any":
                    views.setdefault(var, rows)
                elif key == "gulf-any":
                    views.setdefault(f"gulf-{var}", rows)
                elif key == "fastest-any":
                    views.setdefault(f"fast-{var}", rows)
                elif key.startswith("pure-"):
                    # one window per carrier — concat them all so the
                    # One-airline view holds every (market, airline)
                    # pair and the airline chips slice it
                    views.setdefault(f"pure-{var}", []).extend(rows)
        break                      # one flight watch drives the overview
    for k in list(views):
        if k.startswith("pure-"):
            views[k] = sorted(views[k], key=lambda r: r["amount_sar"])
    return views, route


def _gsearch_link(pos, dates, route):
    """Fallback per-row link when the scan has no exact booking link:
    Google Flights for the row's dates, priced from the row's country
    (gl= sets the market — the same point-of-sale lever the scan uses).
    Constructed locally, so every row can carry it at zero API cost."""
    origin, dest = route or (None, None)
    d = (dates or "").split("/")
    if not origin or not dest or not d or not d[0]:
        return None
    q = f"Flights from {origin} to {dest} on {d[0]}"
    if len(d) > 1 and d[1]:
        q += f" through {d[1]}"
    return (f"https://www.google.com/travel/flights?q={quote_plus(q)}"
            f"&hl=en&gl={(pos or 'sa').lower()}")


def _kpi(view_id, label, row, cabin="", route=None):
    if not row:
        return (f'<a class="kpi" href="#{view_id}"><span class="k-lbl">'
                f'{_e(label)}</span><span class="k-num">—</span>'
                f'<span class="k-sub">awaiting scan data</span></a>')
    cabin_chip = f'<span class="badge b-muted">{_e(cabin)}</span>' if cabin \
        else ""
    book = ""
    if row.get("deep_link"):
        book = (f'<span class="k-book" data-href="{_e(row["deep_link"])}">'
                f'Book ↗</span>')
    else:
        gs = _gsearch_link(row.get("pos_code"), row.get("dates"), route)
        if gs:
            book = (f'<span class="k-book" data-href="{_e(gs)}">'
                    f'Search ↗</span>')
    return f'''<a class="kpi" href="#{view_id}">
  <span class="k-lbl">{_e(label)} {cabin_chip}</span>
  <span class="k-num">{row["amount_sar"]:,.0f} <small>SAR</small></span>
  <span class="k-sub">{_e(countries.label(row["pos_code"]))} ·
    {_e(row.get("carrier_name") or "")}</span>
  <span class="k-sub">{_fmt_dates(row.get("dates"))} ·
    {_fmt_dur(row.get("duration_min"))}{" · " + _e(_stops_txt(row))
                                       if _stops_txt(row) else ""}{book}</span>
  {f'<span class="k-sub">{_e(row["via"])}</span>'
   if row.get("via") else ""}
</a>'''


def _kpi_section(views, route=None):
    eco = (views.get("economy") or [None])[0]
    biz = (views.get("business") or [None])[0]
    ge = (views.get("gulf-economy") or [None])[0]
    gb = (views.get("gulf-business") or [None])[0]
    gulf, gulf_cabin = None, ""
    for cand, cab in ((ge, "economy"), (gb, "business")):
        if cand and (gulf is None or cand["amount_sar"] < gulf["amount_sar"]):
            gulf, gulf_cabin = cand, cab
    return f'''<section class="kpis">
{_kpi("view-a", "Cheapest economy", eco, route=route)}
{_kpi("view-b", "Cheapest Gulf airlines", gulf, gulf_cabin, route=route)}
{_kpi("view-c", "Cheapest business", biz, route=route)}
</section>
<p class="note">Trip totals in SAR, 1 adult, Riyadh ⇄ Lisbon. Tap a card
for the full per-country view. Travel time is the whole round trip —
outbound + return added together, layovers included. Booking links
regenerate each scan — open them in a private/incognito window for the
quoted price.</p>'''


def _view_table(rows, route=None):
    if not rows:
        return ('<p class="empty">No data in the latest scan for this '
                'view yet — it fills after the next scan.</p>')
    out = ['<div class="vt">',
           '<div class="vt-row vt-head"><span>Market</span>'
           '<span>Airline</span><span>Flights · dates</span>'
           '<span>Travel time</span><span>Price</span><span>Book</span></div>']
    for i, r in enumerate(rows):
        native = "" if r["currency"] == "SAR" else \
            f'<small>{r["amount_native"]:,.0f} {_e(r["currency"])}</small>'
        if r.get("deep_link"):
            book = (f'<a class="booklink" href="{_e(r["deep_link"])}" '
                    f'target="_blank" rel="noopener">Book ↗</a>')
        else:
            gs = _gsearch_link(r.get("pos_code"), r.get("dates"), route)
            book = (f'<a class="booklink alt" href="{_e(gs)}" '
                    f'target="_blank" rel="noopener">Search ↗</a>'
                    if gs else '<span class="vt-dim">—</span>')
        chips = '<span class="badge b-good">cheapest</span>' if i == 0 else \
            ('<span class="badge b-muted">home</span>'
             if r["pos_code"] == "SA" else "")
        via = (f'<br><small>{_e(r["via"])}</small>'
               if r.get("via") else "")
        stops = (f'<br><small>{_e(_stops_txt(r))}</small>'
                 if _stops_txt(r) else "")
        dur = r.get("duration_min")
        out.append(
            f'<div class="vt-row{" best" if i == 0 else ""}" '
            f'data-sar="{r["amount_sar"]:.0f}" '
            f'data-dur="{int(dur) if dur else ""}" '
            f'data-air="{_e(" ".join(_row_codes(r.get("flight"))))}">'
            f'<span class="vt-mkt">{_e(countries.label(r["pos_code"]))} '
            f'{chips}</span>'
            f'<span>{_e(r.get("carrier_name") or "—")}</span>'
            f'<span class="vt-fl">{_e(r.get("flight") or "")}<br>'
            f'<small>{_fmt_dates(r.get("dates"))}</small>{via}</span>'
            f'<span>{_fmt_dur(r.get("duration_min"))}{stops}</span>'
            f'<span class="vt-price">{r["amount_sar"]:,.0f} SAR<br>'
            f'{native}</span>'
            f'<span>{book}</span></div>')
    out.append('<div class="vt-row vt-none" hidden>No tickets match '
               'these filters in THIS view — airline-specific tickets '
               'live in view D · One airline; or loosen the filters.'
               '</div>')
    out.append('</div>')
    return "".join(out)


def _filter_bar(views):
    """Sort / airline / max-price controls over the view tables. Pure
    client-side — filters what the last scan already found, no extra
    API calls."""
    codes, prices, durs = set(), [], []
    for rows in views.values():
        for r in rows or []:
            codes.update(_row_codes(r.get("flight")))
            prices.append(r["amount_sar"])
            if r.get("duration_min"):
                durs.append(int(r["duration_min"]))
    if not prices:
        return ""
    lo = int(min(prices) // 100 * 100)
    hi = int(-(-max(prices) // 100) * 100)
    if hi <= lo:
        hi = lo + 100
    dlo = (min(durs) // 60) if durs else 0
    dhi = (-(-max(durs) // 60)) if durs else 48
    if dhi <= dlo:
        dhi = dlo + 1
    chips = "".join(
        f'<button class="fbtn airbtn" data-air="{_e(c)}">'
        f'{_e(AIRLINE_NAMES.get(c, c))}</button>'
        for c in sorted(codes, key=lambda c: AIRLINE_NAMES.get(c, c)))
    return f'''<div class="filterbar">
  <div class="fbar-title">Filter &amp; sort tickets</div>
  <div class="fgroup"><span class="flbl">Sort</span>
    <button class="fbtn sortbtn active" data-sort="sar">Cheapest</button>
    <button class="fbtn sortbtn" data-sort="dur">Shortest trip</button>
  </div>
  <div class="fgroup"><span class="flbl">Airline</span>
    <button class="fbtn airbtn active" data-air="">All</button>
    {chips}
    <button class="fbtn" id="fpure">One airline only</button>
  </div>
  <div class="fgroup fprice"><span class="flbl">Max price</span>
    <input type="range" id="pmax" min="{lo}" max="{hi}" value="{hi}"
           step="50">
    <span id="pmaxlbl">no limit</span>
  </div>
  <div class="fgroup fprice"><span class="flbl">Max travel time</span>
    <input type="range" id="dmax" min="{dlo}" max="{dhi}" value="{dhi}"
           step="1">
    <span id="dmaxlbl">no limit</span>
  </div>
  <button class="fbtn freset" id="freset">Reset</button>
</div>'''


def _views_section(views, route=None):
    a = _view_table(views.get("economy"), route)
    b = (f'<h3 class="vt-sub">Economy</h3>'
         f'{_view_table(views.get("gulf-economy"), route)}'
         f'<h3 class="vt-sub">Business</h3>'
         f'{_view_table(views.get("gulf-business"), route)}')
    c = _view_table(views.get("business"), route)
    dd = (f'<h3 class="vt-sub">Economy</h3>'
          f'{_view_table(views.get("pure-economy"), route)}'
          f'<h3 class="vt-sub">Business</h3>'
          f'{_view_table(views.get("pure-business"), route)}')
    ee = (f'<h3 class="vt-sub">Economy</h3>'
          f'{_view_table(views.get("fast-economy"), route)}'
          f'<h3 class="vt-sub">Business</h3>'
          f'{_view_table(views.get("fast-business"), route)}')
    return f'''<section>
{_filter_bar(views)}
<nav class="viewbar" role="tablist">
  <button class="vbtn active" data-view="view-e">E · Shortest trips</button>
  <button class="vbtn" data-view="view-a">A · Economy, all markets</button>
  <button class="vbtn" data-view="view-b">B · Gulf airlines</button>
  <button class="vbtn" data-view="view-c">C · Business, all markets</button>
  <button class="vbtn" data-view="view-d">D · One airline</button>
</nav>
<div id="view-e" class="view">
  <p class="note">Each country's SHORTEST trip at the cheapest fare
  that buys it (options within an hour of that country's fastest count
  as equally short — the cheapest of them wins the row). Sort by
  "Cheapest" to rank these short tickets by price; airline chips
  narrow to one carrier.</p>{ee}</div>
<div id="view-a" class="view" hidden>{a}</div>
<div id="view-b" class="view" hidden>
  <p class="note">Cheapest tickets that include a Gulf carrier (Saudia,
  flynas, flyadeal, Emirates, flydubai, Etihad, Air Arabia, Qatar
  Airways, Gulf Air, Jazeera, Kuwait Airways, Oman Air) — flight numbers
  show the exact carriers on each ticket.</p>{b}</div>
<div id="view-c" class="view" hidden>{c}</div>
<div id="view-d" class="view" hidden>
  <p class="note">Every segment on the SAME airline — no airline change
  at the connection (e.g. Riyadh→Doha→Lisbon all on Qatar Airways).
  Each country shows that airline's CHEAPEST ticket and (when different)
  its FASTEST one: tap an airline chip above, then sort by "Cheapest"
  for the best fare per country or "Shortest trip" for the quickest
  routing per country.</p>{dd}</div>
<p class="note"><b>Book ↗</b> = exact ticket link fetched this scan (the
3 cheapest rows of each view). <b>Search ↗</b> = every other row: opens
Google Flights priced from that row's country with that row's dates —
pick the matching flights there. Travel time = outbound + return
together, layovers included — the line under it says where the
connection is and how long the wait lasts.</p>
</section>'''


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
    views, route = _view_data(d["cards"])

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

{_kpi_section(views, route)}

{_views_section(views, route)}

<details class="more"><summary>Price history &amp; watch detail</summary>
<section><h2>Watches</h2><div class="grid">{cards}</div></section>
</details>

<details class="more"><summary>Same-flight market comparisons</summary>
<section>
<p class="note">All prices are totals for the whole trip, in SAR. Each
window compares one exact flight across markets (same flight numbers
and dates) — the cleanest view of the point-of-sale gap. Markets
showing — didn't quote in the latest scan.</p>
{_matrix_windows(d["cards"])}</section>
</details>

<details class="more"><summary>Saudi price gap · country wins · holds · providers</summary>
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
</details>

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

/* --- overview KPI cards --- */
.kpis { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px;
  margin-top: 18px; }
.kpi { display: flex; flex-direction: column; gap: 4px; padding: 16px;
  background: var(--card); border: 1px solid var(--hair);
  border-radius: 12px; text-decoration: none; color: var(--ink); }
.kpi:hover { border-color: var(--accent); }
.k-lbl { font: 600 12px/1.3 "IBM Plex Mono", monospace;
  letter-spacing: .05em; text-transform: uppercase; color: var(--ink-2); }
.k-num { font: 600 30px/1.1 "IBM Plex Sans Condensed", "Arial Narrow",
  sans-serif; }
.k-num small { font-size: 15px; color: var(--ink-2); }
.k-sub { color: var(--ink-2); font-size: 12.5px; }
.k-book { margin-left: 8px; color: var(--accent); font-weight: 600;
  cursor: pointer; }
@media (max-width: 720px) { .kpis { grid-template-columns: 1fr; } }

/* --- view switcher + tables --- */
.viewbar { display: flex; gap: 8px; flex-wrap: wrap; margin: 22px 0 12px; }
.vbtn { font: 600 13px/1 "IBM Plex Sans", sans-serif; padding: 10px 14px;
  border-radius: 999px; border: 1px solid var(--hair);
  background: var(--card); color: var(--ink-2); cursor: pointer; }
.vbtn.active { background: var(--accent-soft); border-color: var(--accent);
  color: var(--ink); }
.vt { border: 1px solid var(--hair); border-radius: 12px;
  background: var(--card); overflow: hidden; }
.vt-row { display: grid; gap: 10px; align-items: center;
  grid-template-columns: 1.3fr 1fr 1.4fr .7fr .9fr .6fr;
  padding: 10px 14px; border-top: 1px solid var(--hair); font-size: 13.5px; }
.vt-row:first-child { border-top: 0; }
.vt-head { font: 600 11px/1.3 "IBM Plex Mono", monospace;
  letter-spacing: .05em; text-transform: uppercase; color: var(--ink-3); }
.vt-row.best { background: var(--good-soft); }
.vt-price { font-weight: 600; white-space: nowrap; }
.vt-price small, .vt-fl small { color: var(--ink-3); font-weight: 400; }
.vt-fl { font-family: "IBM Plex Mono", monospace; font-size: 12px;
  overflow-wrap: anywhere; }
.vt-dim { color: var(--ink-3); font-size: 12px; }
.vt-sub { margin: 18px 0 8px; font: 600 14px/1 "IBM Plex Sans", sans-serif; }
@media (max-width: 760px) {
  .vt-head { display: none; }
  .vt-row { grid-template-columns: 1fr 1fr;
    padding: 12px 14px; }
  .vt-mkt { grid-column: 1 / -1; font-weight: 600; }
}

/* --- filter bar --- */
.filterbar { display: flex; flex-wrap: wrap; gap: 10px 18px;
  align-items: center; margin: 20px 0 0; padding: 12px 14px;
  border: 1px solid var(--accent); border-radius: 12px;
  background: var(--card); }
.fbar-title { flex-basis: 100%; font: 600 14px/1 "IBM Plex Sans",
  sans-serif; color: var(--accent); }
.booklink.alt { color: var(--ink-2); }
.fgroup { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.flbl { font: 600 11px/1 "IBM Plex Mono", monospace; letter-spacing: .05em;
  text-transform: uppercase; color: var(--ink-3); margin-right: 2px; }
.fbtn { font: 600 12px/1 "IBM Plex Sans", sans-serif; padding: 7px 11px;
  border-radius: 999px; border: 1px solid var(--hair);
  background: var(--card); color: var(--ink-2); cursor: pointer; }
.fbtn.active { background: var(--accent-soft); border-color: var(--accent);
  color: var(--ink); }
.fprice input[type=range] { width: 140px; accent-color: var(--accent); }
#pmaxlbl { font: 500 12px/1 "IBM Plex Mono", monospace;
  color: var(--ink-2); min-width: 86px; }
.freset { border-style: dashed; }
.vt-row[hidden] { display: none !important; }
.vt-row.top { background: var(--good-soft); }
.vt-row.vt-none { display: block; color: var(--ink-3); font-size: 13px; }

/* --- collapsed detail sections --- */
details.more { margin-top: 22px; border: 1px solid var(--hair);
  border-radius: 12px; background: var(--card); padding: 0 16px; }
details.more > summary { cursor: pointer; padding: 14px 0;
  font: 600 14px/1 "IBM Plex Sans", sans-serif; color: var(--ink-2); }
details.more[open] > summary { border-bottom: 1px solid var(--hair); }
'''


JS = '''
// view switcher: buttons + KPI-card anchors both select a view
function showView(id) {
  document.querySelectorAll(".view").forEach(function (v) {
    v.hidden = v.id !== id;
  });
  document.querySelectorAll(".vbtn").forEach(function (b) {
    b.classList.toggle("active", b.dataset.view === id);
  });
}
document.querySelectorAll(".vbtn").forEach(function (b) {
  b.addEventListener("click", function () { showView(b.dataset.view); });
});
document.querySelectorAll(".kpi").forEach(function (k) {
  k.addEventListener("click", function (e) {
    var id = k.getAttribute("href").slice(1);
    showView(id);
  });
});
document.querySelectorAll(".k-book").forEach(function (s) {
  s.addEventListener("click", function (e) {
    e.preventDefault(); e.stopPropagation();
    window.open(s.dataset.href, "_blank", "noopener");
  });
});

// --- ticket filters: sort / airline / max price (client-side only) ---
var selAir = new Set();
var sortKey = "sar";
var pureOnly = false;
function applyFilters() {
  var pmax = document.getElementById("pmax");
  var noLimit = !pmax || +pmax.value >= +pmax.max;
  var lim = pmax ? +pmax.value : Infinity;
  var dmax = document.getElementById("dmax");
  var noDur = !dmax || +dmax.value >= +dmax.max;
  var dlim = dmax ? +dmax.value * 60 : Infinity;
  document.querySelectorAll(".view .vt").forEach(function (t) {
    var rows = Array.prototype.slice.call(
      t.querySelectorAll(".vt-row:not(.vt-head):not(.vt-none)"));
    var vis = [];
    rows.forEach(function (r) {
      r.classList.remove("best", "top");
      var codes = (r.dataset.air || "").split(" ").filter(Boolean);
      var okAir = selAir.size === 0 ||
        codes.some(function (c) { return selAir.has(c); });
      var okPure = !pureOnly || codes.length === 1;
      var okPrice = noLimit || +r.dataset.sar <= lim;
      // unknown durations only pass while no time limit is set
      var okDur = noDur || (r.dataset.dur && +r.dataset.dur <= dlim);
      r.hidden = !(okAir && okPure && okPrice && okDur);
      if (!r.hidden) vis.push(r);
    });
    vis.sort(function (a, b) {
      var ka = sortKey === "dur" ? (+a.dataset.dur || 1e9) : +a.dataset.sar;
      var kb = sortKey === "dur" ? (+b.dataset.dur || 1e9) : +b.dataset.sar;
      return ka - kb || (+a.dataset.sar - +b.dataset.sar);
    });
    vis.forEach(function (r) { t.appendChild(r); });
    if (vis.length) vis[0].classList.add("top");
    var none = t.querySelector(".vt-none");
    if (none) { none.hidden = vis.length > 0; t.appendChild(none); }
  });
}
document.querySelectorAll(".sortbtn").forEach(function (b) {
  b.addEventListener("click", function () {
    sortKey = b.dataset.sort;
    document.querySelectorAll(".sortbtn").forEach(function (x) {
      x.classList.toggle("active", x === b);
    });
    applyFilters();
  });
});
document.querySelectorAll(".airbtn").forEach(function (b) {
  b.addEventListener("click", function () {
    var c = b.dataset.air;
    if (!c) selAir.clear();
    else if (selAir.has(c)) selAir.delete(c);
    else selAir.add(c);
    document.querySelectorAll(".airbtn").forEach(function (x) {
      x.classList.toggle("active",
        x.dataset.air ? selAir.has(x.dataset.air) : selAir.size === 0);
    });
    applyFilters();
  });
});
var fpure = document.getElementById("fpure");
if (fpure) fpure.addEventListener("click", function () {
  pureOnly = !pureOnly;
  fpure.classList.toggle("active", pureOnly);
  applyFilters();
});
var pmaxEl = document.getElementById("pmax");
var pmaxLbl = document.getElementById("pmaxlbl");
function pmaxText() {
  if (!pmaxEl) return;
  pmaxLbl.textContent = +pmaxEl.value >= +pmaxEl.max ? "no limit"
    : "\\u2264 " + (+pmaxEl.value).toLocaleString() + " SAR";
}
if (pmaxEl) {
  pmaxEl.addEventListener("input", function () { pmaxText(); applyFilters(); });
  pmaxText();
}
var dmaxEl = document.getElementById("dmax");
var dmaxLbl = document.getElementById("dmaxlbl");
function dmaxText() {
  if (!dmaxEl) return;
  dmaxLbl.textContent = +dmaxEl.value >= +dmaxEl.max ? "no limit"
    : "\\u2264 " + dmaxEl.value + "h round trip";
}
if (dmaxEl) {
  dmaxEl.addEventListener("input", function () { dmaxText(); applyFilters(); });
  dmaxText();
}
var freset = document.getElementById("freset");
if (freset) freset.addEventListener("click", function () {
  selAir.clear();
  sortKey = "sar";
  pureOnly = false;
  if (fpure) fpure.classList.remove("active");
  document.querySelectorAll(".sortbtn").forEach(function (x) {
    x.classList.toggle("active", x.dataset.sort === "sar");
  });
  document.querySelectorAll(".airbtn").forEach(function (x) {
    x.classList.toggle("active", !x.dataset.air);
  });
  if (pmaxEl) { pmaxEl.value = pmaxEl.max; pmaxText(); }
  if (dmaxEl) { dmaxEl.value = dmaxEl.max; dmaxText(); }
  applyFilters();
});
applyFilters();

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
