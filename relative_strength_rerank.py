#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import pandas as pd
import requests

BASE="https://bybit-trading-screener.feelmebaby79.workers.dev"; CATEGORY="linear"

def api_get(path,params,retries=2):
    err=None
    for i in range(retries):
        try:
            r=requests.get(BASE+path,params=params,timeout=(3,7),headers={"User-Agent":"bybit-leader-rerank/1.3"}); r.raise_for_status(); p=r.json()
            if p.get("retCode")!=0: raise RuntimeError(p.get("retMsg","Bybit API error"))
            return p.get("result",{})
        except Exception as exc: err=exc; time.sleep(.2*(2**i))
    raise RuntimeError(err)

def fetch_klines(symbol,interval,limit=8):
    rows=api_get("/v5/market/kline",{"category":CATEGORY,"symbol":symbol,"interval":interval,"limit":limit}).get("list",[])
    parsed=sorted((int(x[0]),float(x[4])) for x in rows)
    if len(parsed)<2: raise RuntimeError("not enough klines")
    return parsed

def period_return(symbol,interval,bars_back):
    r=fetch_klines(symbol,interval,max(bars_back+2,8)); return (r[-1][1]/r[-1-bars_back][1]-1)*100 if r[-1-bars_back][1]>0 else np.nan

def fetch_oi(symbol,interval="15min",limit=20):
    rows=api_get("/v5/market/open-interest",{"category":CATEGORY,"symbol":symbol,"intervalTime":interval,"limit":limit}).get("list",[]); p=[]
    for x in rows:
        try:p.append((int(x["timestamp"]),float(x["openInterest"])))
        except Exception:pass
    p.sort()
    if len(p)<2: raise RuntimeError("not enough OI history")
    return p

def oi_change(r,b):
    return (r[-1][1]/r[-1-b][1]-1)*100 if len(r)>b and r[-1-b][1]>0 else np.nan

def clamp(v,lo,hi): return 0.0 if not np.isfinite(v) else max(lo,min(hi,v))
def persistent_strength_bonus(a,b,c,side):
    d=1 if side=="LONG" else -1; v=[d*a,d*b,d*c]
    if not all(np.isfinite(x) for x in v):return 0.,0
    pos=sum(x>0 for x in v); z=0.
    if pos==3:
        z+=1.25
        if v[2]>=v[1]>=v[0]>0:z+=.75
        if v[1]>=.5 and v[2]>=1:z+=.75
    elif pos==2 and v[1]>0 and v[2]>0:z+=.5
    return clamp(z,0,2.75),pos

def strength_adjustment(a,b,c,ba,bb,bc,side):
    d=1 if side=="LONG" else -1; w=.25*clamp(d*a/.75,-1.5,1.5)+.35*clamp(d*b/1.25,-1.5,1.5)+.4*clamp(d*c/2,-1.5,1.5); adj=w*2
    if side=="LONG":
        if ba<-.2 and a>0:adj+=.75
        if bb<-.35 and b>0:adj+=1
        if bc<-.6 and c>0:adj+=.75
    else:
        if ba>.2 and a<0:adj+=.75
        if bb>.35 and b<0:adj+=1
        if bc>.6 and c<0:adj+=.75
    p,h=persistent_strength_bonus(a,b,c,side); return clamp(adj+p,-4,6),p,h

def oi_adjustment(a,b,c,p,side):
    d=1 if side=="LONG" else -1
    if not all(np.isfinite(x) for x in [a,b,c]):return 0.,0.,False
    base=.8*clamp(a/3,-1,1.5)+1.2*clamp(b/7,-1,1.5)+clamp(c/15,-1,1.5); bonus=.75 if a>0 and b>0 and c>0 else 0.; acc=a>0 and b>0 and a*4>b*1.2
    if acc:bonus+=.75
    if np.isfinite(p) and d*p>0:bonus+=.75
    else:base*=.35
    if b>=15 or c>=25:bonus-=.5
    return clamp(base+bonus,-2,4.5),bonus,acc

def scan_symbol(s,btc):
    try:p30=period_return(s,"5",6);p1=period_return(s,"15",4);p4=period_return(s,"60",4);rs=[p30-btc[0],p1-btc[1],p4-btc[2]]
    except Exception:p1=np.nan;rs=[np.nan]*3
    try:oi=fetch_oi(s);o15=oi_change(oi,1);o1=oi_change(oi,4);o4=oi_change(oi,16)
    except Exception:o15=o1=o4=np.nan
    la,lp,lh=strength_adjustment(*rs,*btc,"LONG");sa,sp,sh=strength_adjustment(*rs,*btc,"SHORT");loa,_,lacc=oi_adjustment(o15,o1,o4,p1,"LONG");soa,_,sacc=oi_adjustment(o15,o1,o4,p1,"SHORT")
    return rs,la,sa,lp,sp,lh,sh,o15,o1,o4,loa,soa,lacc,sacc

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--input",default="scan_results.csv");ap.add_argument("--top",type=int,default=15);a=ap.parse_args();df=pd.read_csv(a.input,encoding="utf-8-sig")
    if df.empty:raise SystemExit("No scan results")
    btc=(period_return("BTCUSDT","5",6),period_return("BTCUSDT","15",4),period_return("BTCUSDT","60",4)); symbols=[str(x).upper().strip() for x in df["symbol"]]; results={}
    # Bounded concurrency: worst-case network waits are capped instead of serially accumulating across ~100 symbols.
    with ThreadPoolExecutor(max_workers=12) as ex:
        fut={ex.submit(scan_symbol,s,btc):s for s in symbols}
        for f in as_completed(fut):
            s=fut[f]
            try:results[s]=f.result()
            except Exception as e: print(f"[WARN] rerank unavailable {s}: {e}");results[s]=([np.nan]*3,0,0,0,0,0,0,np.nan,np.nan,np.nan,0,0,False,False)
    out=[results[s] for s in symbols]; names=["rs_vs_btc_30m_pct","rs_vs_btc_1h_pct","rs_vs_btc_4h_pct"]
    for j,n in enumerate(names):df[n]=[x[0][j] for x in out]
    df["btc_return_30m_pct"],df["btc_return_1h_pct"],df["btc_return_4h_pct"]=btc
    for n,i in [("long_relative_strength_adj",1),("short_relative_strength_adj",2),("long_rs_persistence_bonus",3),("short_rs_persistence_bonus",4),("long_rs_positive_horizons",5),("short_rs_positive_horizons",6),("oi_change_15m_pct",7),("oi_change_1h_pct",8),("oi_change_4h_pct",9),("long_oi_adj",10),("short_oi_adj",11)]:df[n]=[x[i] for x in out]
    df["oi_accelerating"]=[x[12] or x[13] for x in out];df["long_score_base"]=pd.to_numeric(df["long_score"],errors="coerce");df["short_score_base"]=pd.to_numeric(df["short_score"],errors="coerce");df["long_score"]=df["long_score_base"]+df["long_relative_strength_adj"]+df["long_oi_adj"];df["short_score"]=df["short_score_base"]+df["short_relative_strength_adj"]+df["short_oi_adj"]
    df.to_csv(a.input,index=False,encoding="utf-8-sig");df.sort_values(["long_score","long_rr"],ascending=False,na_position="last").head(a.top).to_csv(a.input.replace(".csv","_long.csv"),index=False,encoding="utf-8-sig");df.sort_values(["short_score","short_rr"],ascending=False,na_position="last").head(a.top).to_csv(a.input.replace(".csv","_short.csv"),index=False,encoding="utf-8-sig");print(f"rerank complete symbols={len(symbols)} btc={btc}")
if __name__=="__main__":main()
