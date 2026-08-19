"""One daily email covering flights, hotels and cars."""
import os
import smtplib
from email.message import EmailMessage

from core import clock, compare, watches
from providers import registry
from storage import db

SPARK = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"


def sparkline(values):
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    if hi == lo:
        return SPARK[0] * len(values)
    return "".join(SPARK[int((v - lo) / (hi - lo) * (len(SPARK) - 1))]
                   for v in values)


def _offer_block(w, o, variant, edge_threshold=25.0):
    hist = db.daily_lows(w["id"], variant, 30)
    prev = db.previous_best(w["id"], variant)
    real, why, med = compare.is_real_drop(o["sar_est"], hist, variant)
    target = compare.target_for(w, variant)

    delta = ""
    if prev:
        delta = f'  ({o["sar_est"] - prev:+.0f} vs best {prev:.0f})'

    mark, alerts = "", []
    if target and o["sar_est"] <= target:
        mark += "  \u2605 TARGET"
        alerts.append(f'{w["id"]} {variant}: {o["sar_est"]:.0f} SAR')
    elif real:
        mark += "  \u2193 DROP"
        alerts.append(f'{w["id"]} {variant}: {why}')

    # Saudi price gap: same thing, materially cheaper bought from another
    # market. Independent of target/drop \u2014 both can fire on one offer.
    gap = o.get("market_edge_pct") or 0
    sa_ref = o.get("sa_ref_sar")
    if sa_ref and gap >= edge_threshold and o["pos"]["code"] != "SA":
        mark += "  \u2691 CHEAPER ABROAD"
        alerts.append(
            f'{w["id"]} {variant}: {o["sar_est"]:.0f} SAR via {o["pos"]["code"]}'
            f' vs {sa_ref:.0f} SAR in SA (-{gap:.0f}%)')

    src = o["provider"]
    if o.get("also_seen"):
        src += f' (+{len(o["also_seen"])} sources agree)'
    book = "holdable" if o.get("bookable") else "link-only"

    d = o.get("detail", {})
    if o.get("kind") == "hotel":
        extra = f'{d.get("room") or "room"} · {d.get("board") or "room only"}'
        if d.get("free_cancellation"):
            extra += f' · free cancel to {(d.get("cancel_by") or "")[:10]}'
    elif o.get("kind") == "car":
        extra = f'{d.get("category") or ""} · {d.get("pickup_type")} pickup'
    else:
        dates = " / ".join(d.get("dates") or [])
        extra = f'{d.get("stops")} stop(s) · {dates}'

    lines = [
        f'  {variant:<9} {o["sar_est"]:>8.0f} SAR{delta}{mark}',
        f'            {o.get("label") or ""} · {extra}',
        f'            {src} · market {o["pos"]["code"]} '
        f'({o["amount"]:.0f} {o["currency"]})'
        + (f' · {o["market_edge_pct"]:+.1f}% vs SA'
           if o.get("market_edge_pct") else "") + f' · {book}',
        f'            {why}',
    ]
    for f in o.get("flags", []):
        lines.append(f'            \u26a0 {f}')
    if o.get("deep_link"):
        lines.append(f'            {o["deep_link"][:90]}')
    spark = sparkline([v for _, v in hist])
    if spark:
        lines.append(f'            {spark}')
    for alt in o.get("alternatives", [])[:2]:
        lines.append(f'            alt: {alt["sar_est"]:>7.0f} SAR  '
                     f'{compare.label_of(alt) or ""} via {alt["provider"]}'
                     f'/{alt["pos"]["code"]}')
    return "\n".join(lines) + "\n", alerts


def build(results_by_watch, watches_list, cfg=None):
    edge_threshold = (cfg or {}).get("alerts", {}).get("market_edge_pct", 25)
    head = [f"FARE HUNTER \u2014 {clock.now():%a %d %b %Y}", ""]
    alerts, body = [], []

    for w in watches_list:
        offers = results_by_watch.get(w["id"], [])
        title = f'\u2501\u2501 {w["id"]}  {watches.describe(w)}'
        if not offers:
            body.append(f'{title}\n  no results this scan\n')
            continue
        body.append(title)
        for o in offers:
            block, offer_alerts = _offer_block(w, o, o["variant"],
                                               edge_threshold)
            body.append(block)
            alerts += offer_alerts
        body.append("")

    if alerts:
        head += ["ACTION:", *[f"  \u2022 {a}" for a in alerts], ""]
    else:
        head += ["No targets hit today.", ""]

    holds = db.open_holds()
    if holds:
        head.append("HELD \u2014 AWAITING YOUR PAYMENT:")
        for h in holds:
            head.append(f'  {h["booking_reference"]} · {h["variant"]} · '
                        f'{h["amount_sar"]:.0f} SAR · pay by {h["pay_by"]}')
        head.append("")

    missing = {k: registry.missing(k) for k in ("flight", "hotel", "car")}
    inactive = [f"{k}: {', '.join(v)}" for k, v in missing.items() if v]
    tail = ["", "No card is stored by this system. Holds lapse unpaid."]
    if inactive:
        tail.append("Providers idle (no credentials): " + " | ".join(inactive))
    tail.append("Pause a watch:  python hunt.py pause <id>")

    return "\n".join(head + body + tail), bool(alerts)


def send(to, subject, body):
    msg = EmailMessage()
    msg["From"] = os.environ["SMTP_FROM"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(os.environ.get("SMTP_HOST", "smtp.gmail.com"),
                      int(os.environ.get("SMTP_PORT", 587))) as s:
        s.starttls()
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.send_message(msg)
