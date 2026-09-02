#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse, math, time
from dataclasses import dataclass
import numpy as np
import pandas as pd
import requests

BASE='https://api.bybit.com'
CATEGORY='linear'
S=requests.Session(); S.headers.update({'User-Agent':'bybit-htf-ltf-screener/1.0'})
TFS={'1D':'D','4H':'240','1H':'60','15m':'15','5m':'5'}
LIMITS={'1D':220,'4H':260,'1H':300,'15m':320,'5m':400}

@dataclass
class Cfg:
    min_turnover: float=5_000_000
    max_symbols: int=160
    sleep: float=0.05
    timeout: int=12
    min_rr: float=1.8

def api(path, params, cfg, retries=4):
    err=None
    for i in range(retries):
        try:
            r=S.get(BASE+path,params=params,timeout=cfg.timeout); r.raise_for_status(); j=r.json()
            if j.get('retCode')==0: return j
            err=RuntimeError(j.get('retMsg'))
        except Exception as e: err=e
        time.sleep(.4*(2**i))
    raise RuntimeError(f'{path}: {err}')

def universe(cfg):
    all_,cur=[],None
    while True:
        p={'category':CATEGORY,'limit':1000}
        if cur:p['cursor']=cur
        j=api('/v5/market/instruments-info',p,cfg)['result']; all_+=j['list']; cur=j.get('nextPageCursor')
        if not cur: break
    inst=pd.DataFrame(all_)
    inst=inst[(inst.quoteCoin=='USDT')&(inst.status=='Trading')&inst.contractType.str.contains('Perpetual',na=False)]
    tick=pd.DataFrame(api('/v5/market/tickers',{'category':CATEGORY},cfg)['result']['list'])
    for c in ['turnover24h','lastPrice','price24hPcnt']:
        tick[c]=pd.to_numeric(tick[c],errors='coerce')
    out=inst[['symbol']].merge(tick[['symbol','turnover24h','lastPrice','price24hPcnt']],on='symbol',how='left')
    return out[out.turnover24h.fillna(0)>=cfg.min_turnover].sort_values('turnover24h',ascending=False).head(cfg.max_symbols)

def klines(sym,tf,cfg):
    rows=api('/v5/market/kline',{'category':CATEGORY,'symbol':sym,'interval':TFS[tf],'limit':LIMITS[tf]},cfg)['result']['list']
    if not rows:return pd.DataFrame()
    d=pd.DataFrame(rows,columns=['ms','open','high','low','close','volume','turnover'])
    for c in ['open','high','low','close','volume','turnover']: d[c]=pd.to_numeric(d[c],errors='coerce')
    d['ms']=pd.to_numeric(d.ms,errors='coerce'); d['time']=pd.to_datetime(d.ms,unit='ms',utc=True)
    return d.sort_values('time').dropna().reset_index(drop=True)

def rsi(s,n=14):
    x=s.diff(); g=x.clip(lower=0); l=-x.clip(upper=0)
    ag=g.ewm(alpha=1/n,adjust=False,min_periods=n).mean(); al=l.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    return 100-100/(1+ag/al.replace(0,np.nan))

def atr(d,n=14):
    p=d.close.shift(1); tr=pd.concat([(d.high-d.low),(d.high-p).abs(),(d.low-p).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/n,adjust=False,min_periods=n).mean()

def swings(d,left=2,right=2):
    x=d.copy(); sh=np.zeros(len(x),dtype=bool); sl=np.zeros(len(x),dtype=bool)
    for i in range(left,len(x)-right):
        wh=x.high.iloc[i-left:i+right+1]; wl=x.low.iloc[i-left:i+right+1]
        sh[i]=(x.high.iloc[i]==wh.max()) and ((wh==x.high.iloc[i]).sum()==1)
        sl[i]=(x.low.iloc[i]==wl.min()) and ((wl==x.low.iloc[i]).sum()==1)
    x['swing_high']=sh; x['swing_low']=sl; return x

def recent(x,n=3):
    hs=[float(v) for v in x.loc[x.swing_high,'high'].tail(n)]; ls=[float(v) for v in x.loc[x.swing_low,'low'].tail(n)]
    return hs,ls

def structure(d):
    x=swings(d); hs,ls=recent(x,3); tr='range'; sc=0; c=float(x.close.iloc[-1])
    if len(hs)>=2 and len(ls)>=2:
        if hs[-1]>hs[-2] and ls[-1]>ls[-2]: tr='bullish'; sc=2
        elif hs[-1]<hs[-2] and ls[-1]<ls[-2]: tr='bearish'; sc=-2
    if hs and c>hs[-1]: tr='bullish'; sc+=1
    if ls and c<ls[-1]: tr='bearish'; sc-=1
    return tr,sc,(hs[-1] if hs else np.nan),(ls[-1] if ls else np.nan)

def location(d,n=60):
    z=d.tail(min(n,len(d))); hi=float(z.high.max()); lo=float(z.low.min()); c=float(z.close.iloc[-1]); pos=(c-lo)/(hi-lo) if hi>lo else .5
    zone='discount' if pos<=.38 else ('premium' if pos>=.62 else 'equilibrium')
    return zone,pos,hi,lo,(hi+lo)/2

def strict_fvg(d):
    for i in range(len(d)-1,1,-1):
        a=d.iloc[i-2]; c=d.iloc[i]
        if c.low>a.high:return 'bullish',float(a.high),float(c.low)
        if c.high<a.low:return 'bearish',float(c.high),float(a.low)
    return None,np.nan,np.nan

def sweep(d,side,n=25):
    if len(d)<n+2:return False
    p=d.iloc[-n-1:-1]; c=d.iloc[-1]
    if side=='LONG':
        lv=float(p.low.min()); return c.low<lv and c.close>lv
    lv=float(p.high.max()); return c.high>lv and c.close<lv

def displacement(d,side,m=.9):
    a=atr(d).iloc[-1]; c=d.iloc[-1]
    if not np.isfinite(a) or a<=0:return False
    body=abs(c.close-c.open)
    return (c.close>c.open if side=='LONG' else c.close<c.open) and body>=m*a

def trigger(d,side):
    x=swings(d); hs,ls=recent(x,2); c=float(x.close.iloc[-1]); sw=sweep(x,side); disp=displacement(x,side); ft,fl,fh=strict_fvg(x)
    mss=(c>hs[-1]) if side=='LONG' and hs else ((c<ls[-1]) if side=='SHORT' and ls else False)
    fvg_ok=(ft=='bullish' if side=='LONG' else ft=='bearish')
    score=2*int(sw)+2*int(mss)+2*int(disp)+int(fvg_ok)
    return {'sweep':sw,'mss':mss,'disp':disp,'fvg':ft,'score':score}

def rr_hint(d,side):
    x=swings(d); hs,ls=recent(x,3); e=float(x.close.iloc[-1])
    if side=='LONG':
        stop=ls[-1] if ls else float(x.low.tail(20).min()); target=(hs[-1] if hs and hs[-1]>e else float(x.high.tail(80).max())); risk=e-stop; reward=target-e
    else:
        stop=hs[-1] if hs else float(x.high.tail(20).max()); target=(ls[-1] if ls and ls[-1]<e else float(x.low.tail(80).min())); risk=stop-e; reward=e-target
    return e,stop,target,(reward/risk if risk>0 and reward>0 else np.nan)

def feat(d):
    tr,sc,_,_=structure(d); zone,pos,hi,lo,eq=location(d); rv=rsi(d.close).iloc[-1]
    return {'trend':tr,'score':sc,'zone':zone,'pos':pos,'rsi':float(rv) if np.isfinite(rv) else np.nan,'hi':hi,'lo':lo,'eq':eq}

def side_score(side,F,turn,p24):
    want='bullish' if side=='LONG' else 'bearish'; opp='bearish' if side=='LONG' else 'bullish'; score=0; reasons=[]
    for tf,w in {'1D':4.5,'4H':3.5,'1H':2,'15m':1}.items():
        if F[tf]['trend']==want: score+=w; reasons.append(f'{tf} {want}')
        elif F[tf]['trend']==opp: score-=w*1.25; reasons.append(f'{tf} reverse')
    if side=='LONG':
        if F['4H']['zone']=='discount':score+=2;reasons.append('4H discount')
        if F['1H']['zone']=='discount':score+=1
        if F['4H']['zone']=='premium':score-=1.5
    else:
        if F['4H']['zone']=='premium':score+=2;reasons.append('4H premium')
        if F['1H']['zone']=='premium':score+=1
        if F['4H']['zone']=='discount':score-=1.5
    if np.isfinite(F['15m']['rsi']):
        if side=='LONG' and F['15m']['rsi']>76:score-=1.2
        if side=='SHORT' and F['15m']['rsi']<24:score-=1.2
    if np.isfinite(p24): score+=float(np.clip((p24*100)/(4 if side=='LONG' else -4),-1,1))
    if turn>0:score+=min(1.2,max(0,(math.log10(turn)-6.5)*.4))
    return score,reasons

def scan_symbol(sym,turn,p24,cfg):
    D={}
    for tf in TFS:
        D[tf]=klines(sym,tf,cfg); time.sleep(cfg.sleep)
        if len(D[tf])<60:return None
    F={tf:feat(D[tf]) for tf in TFS}
    out={'symbol':sym,'last':float(D['5m'].close.iloc[-1]),'turnover24h':turn,'price24h_pct':p24*100 if np.isfinite(p24) else np.nan,
         '1D':F['1D']['trend'],'4H':F['4H']['trend'],'1H':F['1H']['trend'],'15m':F['15m']['trend'],'4H_location':F['4H']['zone'],'1H_location':F['1H']['zone'],'15m_rsi':F['15m']['rsi']}
    for side in ['LONG','SHORT']:
        base,_=side_score(side,F,turn,p24); t15=trigger(D['15m'],side); t5=trigger(D['5m'],side); e,st,tg,rr=rr_hint(D['15m'],side)
        score=base+.45*t15['score']+.55*t5['score']+(min(2,max(-1,rr-1)) if np.isfinite(rr) else 0)
        trig=t15['mss'] and (t15['disp'] or t5['disp']); grade='A' if score>=13 and trig and np.isfinite(rr) and rr>=cfg.min_rr else ('B+' if score>=10 else ('B' if score>=7 else 'C'))
        k=side.lower(); out.update({f'{k}_score':score,f'{k}_grade':grade,f'{k}_rr':rr,f'{k}_entry':e,f'{k}_stop_hint':st,f'{k}_target_hint':tg,
            f'{k}_15m_trigger':t15['score'],f'{k}_5m_trigger':t5['score'],f'{k}_sweep15':t15['sweep'],f'{k}_mss15':t15['mss'],f'{k}_disp15':t15['disp'],f'{k}_fvg5':t5['fvg']})
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--min-turnover',type=float,default=5_000_000); ap.add_argument('--max-symbols',type=int,default=160); ap.add_argument('--top',type=int,default=12); ap.add_argument('--output',default='scan_results.csv'); a=ap.parse_args()
    cfg=Cfg(a.min_turnover,a.max_symbols); U=universe(cfg); print(f'Universe: {len(U)}')
    rows=[]
    for i,r in U.reset_index(drop=True).iterrows():
        try:
            z=scan_symbol(r.symbol,float(r.turnover24h or 0),float(r.price24hPcnt) if pd.notna(r.price24hPcnt) else np.nan,cfg)
            if z:rows.append(z);print(f'[{i+1}/{len(U)}] {r.symbol} L={z["long_score"]:.1f} S={z["short_score"]:.1f}')
        except Exception as e: print('[WARN]',r.symbol,e)
    if not rows:raise SystemExit('No results')
    df=pd.DataFrame(rows); df.to_csv(a.output,index=False,encoding='utf-8-sig')
    L=df.sort_values(['long_score','long_rr'],ascending=False).head(a.top); S_=df.sort_values(['short_score','short_rr'],ascending=False).head(a.top)
    L.to_csv(a.output.replace('.csv','_long.csv'),index=False,encoding='utf-8-sig'); S_.to_csv(a.output.replace('.csv','_short.csv'),index=False,encoding='utf-8-sig')
    print('\nTOP LONG');print(L[['symbol','long_grade','long_score','long_rr','1D','4H','1H','4H_location']].to_string(index=False))
    print('\nTOP SHORT');print(S_[['symbol','short_grade','short_score','short_rr','1D','4H','1H','4H_location']].to_string(index=False))

if __name__=='__main__':main()
