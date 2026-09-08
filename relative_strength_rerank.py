#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import time
import numpy as np
import pandas as pd
import requests

BASE = "https://bybit-trading-screener.feelmebaby79.workers.dev"
CATEGORY = "linear"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "bybit-leader-rerank/1.2"})


def api_get(path, params, retries=3):
    err = None
    for i in range(retries):
        try:
            r = SESSION.get(BASE + path, params=params, timeout=20)
            r.raise_for_status(); p = r.json()
            if p.get("retCode") != 0: raise RuntimeError(p.get("retMsg", "Bybit API error"))
            return p.get("result", {})
        except Exception as exc:
            err = exc; time.sleep(0.4 * (2 ** i))
    raise RuntimeError(err)


def fetch_klines(symbol, interval, limit=8):
    rows = api_get("/v5/market/kline", {"category":CATEGORY,"symbol":symbol,"interval":interval,"limit":limit}).get("list", [])
    parsed = [(int(x[0]), float(x[4])) for x in rows]; parsed.sort(key=lambda x:x[0])
    if len(parsed) < 2: raise RuntimeError("not enough klines")
    return parsed


def period_return(symbol, interval, bars_back):
    rows = fetch_klines(symbol, interval, max(bars_back+2,8)); latest=rows[-1][1]; past=rows[-1-bars_back][1]
    return (latest/past-1)*100 if past>0 else np.nan


def fetch_oi(symbol, interval="15min", limit=20):
    rows = api_get("/v5/market/open-interest", {"category":CATEGORY,"symbol":symbol,"intervalTime":interval,"limit":limit}).get("list", [])
    parsed=[]
    for x in rows:
        try: parsed.append((int(x["timestamp"]), float(x["openInterest"])))
        except Exception: pass
    parsed.sort(key=lambda x:x[0])
    if len(parsed)<2: raise RuntimeError("not enough OI history")
    return parsed


def oi_change(rows, bars_back):
    if len(rows)<=bars_back: return np.nan
    now=rows[-1][1]; past=rows[-1-bars_back][1]
    return (now/past-1)*100 if past>0 else np.nan


def clamp(v, lo, hi):
    return 0.0 if not np.isfinite(v) else max(lo,min(hi,v))


def persistent_strength_bonus(rs30,rs1h,rs4h,side):
    d=1 if side=="LONG" else -1; vals=[d*rs30,d*rs1h,d*rs4h]
    if not all(np.isfinite(v) for v in vals): return 0.0,0
    pos=sum(v>0 for v in vals); b=0.0
    if pos==3:
        b+=1.25
        if vals[2]>=vals[1]>=vals[0]>0: b+=0.75
        if vals[1]>=0.50 and vals[2]>=1.00: b+=0.75
    elif pos==2 and vals[1]>0 and vals[2]>0: b+=0.50
    return clamp(b,0,2.75),pos


def strength_adjustment(rs30,rs1h,rs4h,btc30,btc1h,btc4h,side):
    d=1 if side=="LONG" else -1
    w=.25*clamp(d*rs30/.75,-1.5,1.5)+.35*clamp(d*rs1h/1.25,-1.5,1.5)+.40*clamp(d*rs4h/2,-1.5,1.5)
    adj=w*2
    if side=="LONG":
        if btc30<-.20 and rs30>0: adj+=.75
        if btc1h<-.35 and rs1h>0: adj+=1
        if btc4h<-.60 and rs4h>0: adj+=.75
    else:
        if btc30>.20 and rs30<0: adj+=.75
        if btc1h>.35 and rs1h<0: adj+=1
        if btc4h>.60 and rs4h<0: adj+=.75
    p,h=persistent_strength_bonus(rs30,rs1h,rs4h,side); return clamp(adj+p,-4,6),p,h


def oi_adjustment(oi15,oi1h,oi4h,price1h,side):
    """Early leader detector: reward persistent/accelerating OI with price confirmation; do not use OI as ENTRY permission."""
    d=1 if side=="LONG" else -1
    vals=[oi15,oi1h,oi4h]
    if not all(np.isfinite(v) for v in vals): return 0.0,0.0,False
    persistent = oi15>0 and oi1h>0 and oi4h>0
    # thresholds begin before headline OI spikes: 15m 1%, 1h 3-5%, 4h 6-10%.
    base=.8*clamp(oi15/3,-1,1.5)+1.2*clamp(oi1h/7,-1,1.5)+1.0*clamp(oi4h/15,-1,1.5)
    bonus=0.75 if persistent else 0.0
    # OI acceleration: recent 15m pace exceeds the average hourly pace.
    accelerating = oi15>0 and oi1h>0 and oi15*4 > oi1h*1.20
    if accelerating: bonus+=0.75
    # Direction comes from price, never OI alone.
    confirmed = np.isfinite(price1h) and d*price1h>0
    if confirmed: bonus+=0.75
    else: base*=0.35
    # Very hot OI is still a leader signal, but cap it to avoid rewarding crowded leverage endlessly.
    if oi1h>=15 or oi4h>=25: bonus-=0.50
    return clamp(base+bonus,-2,4.5),bonus,accelerating


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--input",default="scan_results.csv"); ap.add_argument("--top",type=int,default=15); args=ap.parse_args()
    df=pd.read_csv(args.input,encoding="utf-8-sig");
    if df.empty: raise SystemExit("No scan results")
    btc30=period_return("BTCUSDT","5",6); btc1h=period_return("BTCUSDT","15",4); btc4h=period_return("BTCUSDT","60",4)
    out=[]
    for _,row in df.iterrows():
        s=str(row.get("symbol","")).upper().strip()
        try:
            p30=period_return(s,"5",6); p1=period_return(s,"15",4); p4=period_return(s,"60",4); rs=[p30-btc30,p1-btc1h,p4-btc4h]
        except Exception: p1=np.nan; rs=[np.nan]*3
        try:
            oi=fetch_oi(s); o15=oi_change(oi,1); o1=oi_change(oi,4); o4=oi_change(oi,16)
        except Exception as exc:
            print(f"[WARN] OI unavailable {s}: {exc}"); o15=o1=o4=np.nan
        la,lp,lh=strength_adjustment(*rs,btc30,btc1h,btc4h,"LONG"); sa,sp,sh=strength_adjustment(*rs,btc30,btc1h,btc4h,"SHORT")
        loa,lob,lacc=oi_adjustment(o15,o1,o4,p1,"LONG"); soa,sob,sacc=oi_adjustment(o15,o1,o4,p1,"SHORT")
        out.append((rs,la,sa,lp,sp,lh,sh,o15,o1,o4,loa,soa,lacc,sacc))
        print(f"{s} RS={rs} OI15={o15:.2f} OI1H={o1:.2f} OI4H={o4:.2f} L_OI={loa:.2f}")
    names=["rs_vs_btc_30m_pct","rs_vs_btc_1h_pct","rs_vs_btc_4h_pct"]
    for j,n in enumerate(names): df[n]=[x[0][j] for x in out]
    df["btc_return_30m_pct"]=btc30; df["btc_return_1h_pct"]=btc1h; df["btc_return_4h_pct"]=btc4h
    df["long_relative_strength_adj"]=[x[1] for x in out]; df["short_relative_strength_adj"]=[x[2] for x in out]
    df["long_rs_persistence_bonus"]=[x[3] for x in out]; df["short_rs_persistence_bonus"]=[x[4] for x in out]
    df["long_rs_positive_horizons"]=[x[5] for x in out]; df["short_rs_positive_horizons"]=[x[6] for x in out]
    df["oi_change_15m_pct"]=[x[7] for x in out]; df["oi_change_1h_pct"]=[x[8] for x in out]; df["oi_change_4h_pct"]=[x[9] for x in out]
    df["long_oi_adj"]=[x[10] for x in out]; df["short_oi_adj"]=[x[11] for x in out]
    df["oi_accelerating"]=[x[12] or x[13] for x in out]
    df["long_score_base"]=pd.to_numeric(df["long_score"],errors="coerce"); df["short_score_base"]=pd.to_numeric(df["short_score"],errors="coerce")
    df["long_score"]=df["long_score_base"]+df["long_relative_strength_adj"]+df["long_oi_adj"]
    df["short_score"]=df["short_score_base"]+df["short_relative_strength_adj"]+df["short_oi_adj"]
    df.to_csv(args.input,index=False,encoding="utf-8-sig")
    long=df.sort_values(["long_score","long_rr"],ascending=False,na_position="last").head(args.top); short=df.sort_values(["short_score","short_rr"],ascending=False,na_position="last").head(args.top)
    long.to_csv(args.input.replace(".csv","_long.csv"),index=False,encoding="utf-8-sig"); short.to_csv(args.input.replace(".csv","_short.csv"),index=False,encoding="utf-8-sig")
    show=["symbol","long_score_base","long_relative_strength_adj","long_oi_adj","long_score","long_rr","oi_change_15m_pct","oi_change_1h_pct","oi_change_4h_pct","oi_accelerating"]
    print("\nTOP LONG leader ranking"); print(long[show].to_string(index=False))

if __name__=="__main__": main()
