#!/usr/bin/env python3
import csv, json, time
from pathlib import Path
import requests

BASE='https://bybit-trading-screener.feelmebaby79.workers.dev'
SCAN=Path('latest/scan_results.csv'); WATCH=Path('latest/watchlist.json')
MIN_SCORE=70; MAX_SELECTED=3; MODEL='STORJ_TYPE_V3'
S=requests.Session(); S.headers.update({'User-Agent':'storj-early-filter/3.2'})

def num(v):
    try:return float(v)
    except:return None

def truthy(v): return str(v).lower() in {'1','true','yes'}
def trend(d): return 'bullish' if d=='LONG' else 'bearish'

def location_gate(r,d):
    loc=str(r.get('1H_location') or '').strip().lower()
    if d=='SHORT' and loc=='discount': return False,'rejected: SHORT already in 1H discount'
    if d=='LONG' and loc=='premium': return False,'rejected: LONG already in 1H premium'
    return True,''

def candles(sym, interval, limit=140):
    r=S.get(BASE+'/v5/market/kline',params={'category':'linear','symbol':sym,'interval':interval,'limit':limit},timeout=15)
    r.raise_for_status(); j=r.json()
    if j.get('retCode')!=0: raise RuntimeError(j.get('retMsg'))
    return [{'ms':int(x[0]),'o':float(x[1]),'h':float(x[2]),'l':float(x[3]),'c':float(x[4])} for x in reversed(j['result']['list'])]

def pivots(a,left=2,right=2):
    hs=[]; ls=[]
    for i in range(left,len(a)-right):
        w=a[i-left:i+right+1]
        if a[i]['h']==max(x['h'] for x in w) and sum(x['h']==a[i]['h'] for x in w)==1: hs.append(i)
        if a[i]['l']==min(x['l'] for x in w) and sum(x['l']==a[i]['l'] for x in w)==1: ls.append(i)
    return hs,ls

def storj_pullback(sym,direction):
    a=candles(sym,'60',160)
    if len(a)>2:a=a[:-1]
    hs,ls=pivots(a)
    if len(hs)<2 or len(ls)<2:return False,{'reason':'insufficient_1h_swings'}
    last=a[-1]['c']; atr=sum(x['h']-x['l'] for x in a[-15:])/15
    if direction=='LONG':
        hi_i=hs[-1]; prior_h=hs[-2]; lows_before=[i for i in ls if i<hi_i]
        if len(lows_before)<2:return False,{'reason':'no_impulse_low'}
        lo_i=lows_before[-1]; prev_lo=lows_before[-2]; lo=a[lo_i]['l']; hi=a[hi_i]['h']; impulse=hi-lo
        structural=hi>a[prior_h]['h'] and lo>a[prev_lo]['l']; bars=len(a)-1-hi_i
        depth=(hi-last)/impulse if impulse>0 else 99
        protected=min(x['l'] for x in a[hi_i+1:])>lo if hi_i+1<len(a) else False
        actual=2<=bars<=18 and 0.10<=depth<=0.65 and last<hi and (hi-last)>=0.35*atr
    else:
        lo_i=ls[-1]; prior_l=ls[-2]; highs_before=[i for i in hs if i<lo_i]
        if len(highs_before)<2:return False,{'reason':'no_impulse_high'}
        hi_i=highs_before[-1]; prev_hi=highs_before[-2]; hi=a[hi_i]['h']; lo=a[lo_i]['l']; impulse=hi-lo
        structural=lo<a[prior_l]['l'] and hi<a[prev_hi]['h']; bars=len(a)-1-lo_i
        depth=(last-lo)/impulse if impulse>0 else 99
        protected=max(x['h'] for x in a[lo_i+1:])<hi if lo_i+1<len(a) else False
        actual=2<=bars<=18 and 0.10<=depth<=0.65 and last>lo and (last-lo)>=0.35*atr
    meta={'structural_impulse':structural,'protected_structure':protected,'actual_pullback':actual,'pullback_depth':round(depth,4),'pullback_bars':bars}
    return structural and protected and actual,meta

def score_v3(r,d,meta):
    t=trend(d); s=d.lower(); reasons=[]; pts=0
    loc_ok,loc_reason=location_gate(r,d)
    if not loc_ok:return False,0,[loc_reason]
    if r.get('1D')!=t or r.get('4H')!=t:return False,0,reasons
    pts+=20; reasons.append('1D/4H aligned')
    if r.get('15m')!=t:return False,pts,reasons
    pts+=10; reasons.append('15m returned to HTF direction')
    if not (meta.get('structural_impulse') and meta.get('protected_structure') and meta.get('actual_pullback')):return False,pts,reasons
    pts+=25; reasons.append('verified 1H impulse/pullback/protected structure')
    depth=meta.get('pullback_depth',99)
    if 0.20<=depth<=0.50:pts+=10; reasons.append('healthy pullback depth')
    elif 0.10<=depth<=0.65:pts+=6; reasons.append('acceptable pullback depth')
    rs=[num(r.get('rs_vs_btc_30m_pct')),num(r.get('rs_vs_btc_1h_pct')),num(r.get('rs_vs_btc_4h_pct'))]
    signed=[x if d=='LONG' else -x for x in rs if x is not None]; wins=sum(x>0 for x in signed)
    if wins<2:return False,pts,reasons
    pts+=10 if wins==3 else 7; reasons.append('BTC relative strength persistent')
    if truthy(r.get(f'{s}_sweep15')):pts+=4; reasons.append('15m sweep')
    if truthy(r.get(f'{s}_mss15')):pts+=5; reasons.append('15m MSS')
    if truthy(r.get(f'{s}_disp15')):pts+=5; reasons.append('15m displacement')
    if str(r.get(f'{s}_fvg5') or '').lower()==t:pts+=5; reasons.append('5m FVG aligned')
    if truthy(r.get(f'{s}_entry_model_ready')):pts+=4; reasons.append('entry model ready')
    rr=num(r.get(f'{s}_rr'))
    if rr is not None:pts+=6 if rr>=2 else 4 if rr>=1.5 else 0
    p=abs(num(r.get('price24h_pct')) or 0); chase=max(num(r.get(f'{s}_chase_penalty')) or 0,0)
    if p>=30 or chase>=3:return False,min(100,int(round(pts))),reasons+['rejected: overextended']
    pts-=min(10,int(round(chase*3)))
    pts=max(0,min(100,int(round(pts))))
    return pts>=MIN_SCORE,pts,reasons

rows=list(csv.DictReader(SCAN.open(encoding='utf-8-sig',newline='')))
candidates=[]
for r in rows:
    sym=r.get('symbol','').upper()
    for d in ('LONG','SHORT'):
        try:pb,meta=storj_pullback(sym,d)
        except Exception as e: print(sym,d,'1H validation error',e);continue
        if not pb:continue
        ok,score,reasons=score_v3(r,d,meta)
        grade='STRONG' if score>=80 else 'RECOMMEND' if score>=70 else 'WATCH' if score>=60 else 'REJECT'
        print(MODEL,sym,d,score,grade,meta,reasons)
        if ok:candidates.append((score,sym,d,reasons,meta,grade))
        time.sleep(.03)
candidates.sort(reverse=True);selected=candidates[:MAX_SELECTED]

w=json.loads(WATCH.read_text(encoding='utf-8'))
w['items']=[x for x in w.get('items',[]) if x.get('bucket')!='EARLY_MOMENTUM']
for score,sym,d,reasons,meta,grade in selected:
    existing=next((x for x in w['items'] if x.get('symbol')==sym and x.get('direction')==d),None)
    extra={'early_momentum':True,'early_momentum_model':MODEL,'early_momentum_score':score,'early_momentum_score_scale':100,'early_momentum_grade':grade,'early_momentum_reasons':reasons,'storj_pattern':meta}
    if existing:existing.update(extra)
    else:w['items'].append({'symbol':sym,'direction':d,'score':None,'score_scale':100,'poi_low':None,'poi_high':None,'status':'ACTIVE','bucket':'EARLY_MOMENTUM','trigger_model':'1H/15m POI -> 5m liquidity sweep -> MSS/CHoCH + displacement -> strict FVG/validated OB first retracement -> live RR >= 1.5','note':'STORJ_TYPE_V3 Early Momentum; 100-point score >=70 required; strict 5m trigger still required.','updated_at':w.get('updated_at'),**extra})

# Preserve every recommendation-track membership even when one symbol qualifies for multiple tracks.
for x in w.get('items',[]):
    tracks=[]
    if x.get('bucket')=='DAILY_RECOMMENDATION': tracks.append('DAILY_RECOMMENDATION')
    if x.get('early_momentum') is True or x.get('bucket')=='EARLY_MOMENTUM': tracks.append('EARLY_MOMENTUM')
    if x.get('high_volatility') is True or x.get('bucket')=='HIGH_VOLATILITY_CRYPTO': tracks.append('HIGH_VOLATILITY_CRYPTO')
    if tracks:x['recommendation_tracks']=tracks

p=w.setdefault('policy',{})
p.update({'early_momentum_model':MODEL,'early_momentum_count':sum(1 for x in w['items'] if x.get('early_momentum') is True or x.get('bucket')=='EARLY_MOMENTUM'),'early_momentum_max':MAX_SELECTED,'early_momentum_score_scale':100,'early_momentum_minimum_score':MIN_SCORE,'early_momentum_no_forced_pick':True,'high_volatility_crypto_count':sum(1 for x in w['items'] if x.get('high_volatility') is True or x.get('bucket')=='HIGH_VOLATILITY_CRYPTO'),'multi_track_membership_preserved':True,'location_gate':'SHORT in 1H discount and LONG in 1H premium are rejected as late entries','early_momentum_grades':{'STRONG':'80-100','RECOMMEND':'70-79','WATCH':'60-69 (not added)','REJECT':'0-59'},'early_momentum_rules':'STORJ V3.2: HTF alignment + verified completed 1H impulse/pullback/protected structure + 15m return + BTC RS + ICT confirmation + 5m FVG/entry readiness + RR + anti-chase + 1H dealing-range location gate; score >=70 only'})
# Never downgrade the builder schema version.
w['version']=max(int(w.get('version') or 0),22)
WATCH.write_text(json.dumps(w,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'ok':True,'model':MODEL,'version':w['version'],'minimum_score':MIN_SCORE,'high_volatility_crypto_count':p['high_volatility_crypto_count'],'selected':[(s,d,sc,g) for sc,s,d,_,_,g in selected]},ensure_ascii=False))
