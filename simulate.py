#!/usr/bin/env python3
"""Offline demo - no API keys. Seeds 31 days, prints the digest."""
import json, random
from datetime import timedelta
from core import clock, compare, digest
from storage import db

random.seed(11)
BASE = {("lisbon","economy"):3100, ("lisbon","business"):11800,
        ("tour","economy"):3900, ("oneway","economy"):760,
        ("almaty","std"):2650, ("almaty-car","std"):1010}
END  = {("lisbon","economy"):0.84, ("lisbon","business"):0.97,
        ("tour","economy"):0.88, ("oneway","economy"):1.03,
        ("almaty","std"):0.90, ("almaty-car","std"):0.99}
MKT = [("SA","SAR",1.0),("TR","TRY",10.81),("IN","INR",22.17),
       ("PL","PLN",1.064),("KZ","KZT",128.2)]
PROV = {"flight":["duffel","amadeus","kiwi"],
        "hotel":["amadeus-hotels"],"car":["amadeus-cars"]}

def seed():
    with db.conn() as c:
        c.execute("DELETE FROM price_history")
    now = clock.now()
    prods = {w["id"]: w["product"] for w in db.list_watches()}
    for (wid,var),base in BASE.items():
        for d in range(30,-1,-1):
            mult = 1 + (END[(wid,var)]-1)*(((30-d)/30)**2)
            price = base*mult*random.uniform(0.97,1.04)
            code,cur,fx = random.choice(MKT)
            prod = prods[wid]
            with db.conn() as c:
                c.execute("""INSERT INTO price_history
                  (watch_id,product,variant,provider,pos_code,currency,
                   amount_native,amount_sar,label,detail,source_count,flags,
                   offer_id,seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (wid,prod,var,random.choice(PROV[prod]),code,cur,
                   round(price*fx,2),round(price,2),"Sim",
                   "{}",random.randint(1,3),"",f"o{d}",
                   (now-timedelta(days=d)).isoformat()))

def today_best():
    out={}
    with db.conn() as c:
        rows=c.execute("""SELECT watch_id,product,variant,provider,pos_code,
          currency,amount_native,label,MIN(amount_sar) sar,source_count
          FROM price_history WHERE substr(seen_at,1,10)=?
          GROUP BY watch_id,variant""",(clock.today(),)).fetchall()
    for r in rows:
        kind=r["product"]
        detail={"stops":1,"dates":["2026-10-05","2026-10-12"],"segments":[]} \
            if kind=="flight" else (
            {"room":"Deluxe King","board":"Breakfast","stars":4,
             "free_cancellation":True,"cancel_by":"2026-09-24"} if kind=="hotel"
            else {"category":"Compact SUV","seats":5,"pickup_type":"airport"})
        out.setdefault(r["watch_id"],[]).append({
            "variant":r["variant"],"sar_est":r["sar"],"amount":r["amount_native"],
            "currency":r["currency"],"pos":{"code":r["pos_code"]},
            "provider":r["provider"],"label":"Simulated "+kind.title(),
            "kind":kind,"detail":detail,"flags":[],"clean":True,
            "bookable":r["provider"] in ("duffel","ratehawk"),
            "source_count":r["source_count"],
            "also_seen":["amadeus"] if r["source_count"]>1 else [],
            "market_edge_pct":round(random.uniform(2,14),1),
            "alternatives":[]})
    return out

if __name__=="__main__":
    seed()
    print("seeded 31 days\n")
    for (wid,var) in BASE:
        h=db.daily_lows(wid,var,30); cur=h[-1][1]
        real,why,med=compare.is_real_drop(cur,h,var)
        print(f'{wid:<12}{var:<9}now {cur:>8.0f}  med {med:>8.0f}  '
              f'{"DROP" if real else "quiet":<6}({why})')
    body,_=digest.build(today_best(), db.list_watches())
    print("\n"+"="*66+"\n"+body)
