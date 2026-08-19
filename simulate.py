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

WATCHES = [
    {"id": "lisbon", "product": "flight", "trip_type": "round",
     "slices_json": json.dumps([
         {"origin": "RUH", "destination": "LIS", "date": "2026-10-05"},
         {"origin": "LIS", "destination": "RUH", "date": "2026-10-12"}]),
     "cabins": "economy,business", "date_model": "flex", "flex_days": 3,
     "adults": 2, "target_eco": 2800, "target_biz": 9500,
     "status": "active", "created_at": clock.iso()},
    {"id": "tour", "product": "flight", "trip_type": "multi",
     "slices_json": json.dumps([
         {"origin": "RUH", "destination": "IST", "date": "2026-11-01"},
         {"origin": "IST", "destination": "VIE", "date": "2026-11-05"},
         {"origin": "VIE", "destination": "RUH", "date": "2026-11-10"}]),
     "cabins": "economy", "date_model": "fixed",
     "status": "active", "created_at": clock.iso()},
    {"id": "oneway", "product": "flight", "trip_type": "oneway",
     "slices_json": json.dumps([
         {"origin": "RUH", "destination": "DXB", "date": "2026-09-15"}]),
     "cabins": "economy", "date_model": "fixed",
     "status": "active", "created_at": clock.iso()},
    {"id": "almaty", "product": "hotel", "city": "ALA",
     "checkin": "2026-09-28", "checkout": "2026-10-03", "adults": 2,
     "date_model": "fixed", "target": 2400,
     "status": "active", "created_at": clock.iso()},
    {"id": "almaty-car", "product": "car", "pickup_location": "ALA",
     "pickup_at": "2026-09-29T10:00", "dropoff_at": "2026-10-06T10:00",
     "date_model": "fixed", "target": 900,
     "status": "active", "created_at": clock.iso()},
]

def seed():
    with db.conn() as c:
        c.execute("DELETE FROM price_history")
    for w in WATCHES:
        db.add_watch(w)
    now = clock.now()
    prods = {w["id"]: w["product"] for w in db.list_watches()}
    ins = """INSERT INTO price_history
      (watch_id,product,variant,provider,pos_code,currency,
       amount_native,amount_sar,label,detail,source_count,flags,
       offer_id,seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    for (wid,var),base in BASE.items():
        for d in range(30,-1,-1):
            mult = 1 + (END[(wid,var)]-1)*(((30-d)/30)**2)
            price = base*mult*random.uniform(0.97,1.04)
            # lisbon/business demos the Saudi-gap flag: best price always
            # found abroad, plus a same-day SA sample filed ~55% higher
            gap_demo = (wid,var)==("lisbon","business")
            code,cur,fx = random.choice(MKT[1:] if gap_demo else MKT)
            prod = prods[wid]
            seen = (now-timedelta(days=d)).isoformat()
            with db.conn() as c:
                c.execute(ins,(wid,prod,var,random.choice(PROV[prod]),
                   code,cur,round(price*fx,2),round(price,2),"Sim","{}",
                   random.randint(1,3),"",f"o{d}",seen))
                if gap_demo:
                    c.execute(ins,(wid,prod,var,"duffel","SA","SAR",
                       round(price*1.55,2),round(price*1.55,2),"Sim","{}",
                       1,"",f"sa{d}",seen))

def today_best():
    out={}
    with db.conn() as c:
        rows=c.execute("""SELECT watch_id,product,variant,provider,pos_code,
          currency,amount_native,label,MIN(amount_sar) sar,source_count
          FROM price_history WHERE substr(seen_at,1,10)=?
          GROUP BY watch_id,variant""",(clock.today(),)).fetchall()
    for r in rows:
        kind=r["product"]
        # same SA-gap computation production search.py uses
        sa = db.latest_for_pos(r["watch_id"], r["variant"], "SA", days=7)
        if sa and r["pos_code"]!="SA" and sa["amount_sar"]:
            sa_ref = sa["amount_sar"]
            edge = round((sa_ref-r["sar"])/sa_ref*100, 1)
        else:
            edge, sa_ref = 0.0, None
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
            "market_edge_pct":edge,"sa_ref_sar":sa_ref,
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
