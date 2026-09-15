#!/usr/bin/env python3
import csv, json, math, time
from pathlib import Path
import requests

BASE='https://bybit-trading-screener.feelmebaby79.workers.dev'
SCAN=Path('latest/scan_results.csv'); WATCH=Path('latest/watchlist.json')
S=requests.Session(); S.headers.update({'User-Agent':'storj-early-filter/1.0'})

def num(v):
    try:return float(v)
    except:return None

def truthy(v): return str(v).lower() in {'1','true','yes'}
def trend(d): return 'bullish' if d=='LONG' else 'bearish'
def opp(d): return 'bearish' if d=='LONG' else 'bullish'

def candles(sym, interval, limit=140):
    r=S.get(BASE+'/v5/market/kline',params={'category':'linear','symbol':sym,'interval':interval,'limit':limit},timeout=15)
    r.raise_for_status(); j=r.json()
    if j.get('retCode')!=0: raise RuntimeError(j.get('retMsg'))
    out=[]
    for x in reversed(j['result']['list']):
        out.append({'ms':int(x[0]),'o':float(x[1]),'h':float(x[2]),'l':float(x[3]),'c':float(x[4])})
    return out

def pivots(a,left=2,right=2):
    hs=[]; ls=[]
    for i in range(left,len(a)-right):
        w=a[i-left:i+right+1]
        if a[i]['h']==max(x['h'] for x in w) and sum(x['h']==a[i]['h'] for x in w)==1: hs.append(i)
        if a[i]['l']==min(x['l'] for x in w) and sum(x['l']==a[i]['l'] for x in w)==1: ls.append(i)
    return hs,ls

def storj_pullback(sym,direction):
    a=candles(sym,'60',160)
    # Ignore the still-forming 1H candle for structural validation.
    if len(a)>2: a=a[:-1]
    hs,ls=pivots(a)
    if len(hs)<2 or len(ls)<2:return False,0,{'reason':'insufficient_1h_swings'}
    last=a[-1]['c']; atr=sum(x['h']-x['l'] for x in a[-15:])/15
    if direction=='LONG':
        # Find the latest completed bullish impulse low -> higher high.
        hi_i=hs[-1]; prior_h=hs[-2]
        lows_before=[i for i in ls if i<hi_i]
        if len(lows_before)<2:return False,0,{'reason':'no_impulse_low'}
        lo_i=lows_before[-1]; prev_lo=lows_before[-2]
        lo=a[lo_i]['l']; hi=a[hi_i]['h']; impulse=hi-lo
        structural=hi>a[prior_h]['h'] and lo>a[prev_lo]['l']
        bars=len(a)-1-hi_i
        depth=(hi-last)/impulse if impulse>0 else 99
        protected=min(x['l'] for x in a[hi_i+1:])>lo if hi_i+1<len(a) else False
        actual_pullback=bars>=2 and bars<=18 and depth>=0.10 and depth<=0.65 and last<hi and (hi-last)>=0.35*atr
    else:
        lo_i=ls[-1]; prior_l=ls[-2]
        highs_before=[i for i in hs if i<lo_i]
        if len(highs_before)<2:return False,0,{'reason':'no_impulse_high'}
        hi_i=highs_before[-1]; prev_hi=highs_before[-2]
        hi=a[hi_i]['h']; lo=a[lo_i]['l']; impulse=hi-lo
        structural=lo<a[prior_l]['l'] and hi<a[prev_hi]['h']
        bars=len(a)-1-lo_i
        depth=(last-lo)/impulse if impulse>0 else 99
        protected=max(x['h'] for x in a[lo_i+1:])<hi if lo_i+1<len(a) else False
        actual_pullback=bars>=2 and bars<=18 and depth>=0.10 and depth<=0.65 and last>lo and (last-lo)>=0.35*atr
    ok=structural and protected and actual_pullback
    pts=(4 if structural else 0)+(3 if protected else 0)+(3 if actual_pullback else 0)
    return ok,pts,{'structural_impulse':structural,'protected_structure':protected,'actual_pullback':actual_pullback,'pullback_depth':round(depth,4),'pullback_bars':bars}

def early_base(r,d):
    t=trend(d); s=d.lower(); reasons=[]; pts=0
    if r.get('1D')!=t or r.get('4H')!=t:return False,0,reasons
    pts+=4; reasons.append('1D/4H aligned')
    # 15m must already be turning back with HTF. 1H label itself is NOT accepted as proof of pullback.
    if r.get('15m')!=t:return False,pts,reasons
    pts+=3; reasons.append('15m returned to HTF direction')
    rs=[num(r.get('rs_vs_btc_30m_pct')),num(r.get('rs_vs_btc_1h_pct')),num(r.get('rs_vs_btc_4h_pct'))]
    signed=[x if d=='LONG' else -x for x in rs if x is not None]
    wins=sum(x>0 for x in signed)
    if wins>=2:pts+=3; reasons.append('BTC relative strength persistent')
    else:return False,pts,reasons
    if truthy(r.get(f'{s}_mss15')):pts+=2; reasons.append('15m MSS')
    if str(r.get(f'{s}_fvg5') or '').lower()==t:pts+=1; reasons.append('5m FVG aligned')
    # Anti-chase: STORJ-type means early, not a post-parabolic catch.
    p=abs(num(r.get('price24h_pct')) or 0)
    chase=num(r.get(f'{s}_chase_penalty')) or 0
    if p>=30 or chase>=3:return False,pts,reasons+['rejected: overextended']
    return True,pts,reasons

rows=list(csv.DictReader(SCAN.open(encoding='utf-8-sig',newline='')))
candidates=[]
for r in rows:
    sym=r.get('symbol','').upper()
    for d in ('LONG','SHORT'):
        ok,base,reasons=early_base(r,d)
        if not ok:continue
        try:
            pb,pbpts,meta=storj_pullback(sym,d)
        except Exception as e:
            print(sym,d,'1H validation error',e); continue
        if not pb:
            print('REJECT',sym,d,meta); continue
        score=base+pbpts
        candidates.append((score,sym,d,reasons,meta))
        print('STORJ_TYPE',sym,d,score,meta)
        time.sleep(.03)
candidates.sort(reverse=True)
selected=candidates[:3]

w=json.loads(WATCH.read_text(encoding='utf-8'))
# Remove builder's loose Early Momentum selections. Current daily recommendations/positions/defaults are untouched.
w['items']=[x for x in w.get('items',[]) if x.get('bucket')!='EARLY_MOMENTUM']
for score,sym,d,reasons,meta in selected:
    existing=next((x for x in w['items'] if x.get('symbol')==sym and x.get('direction')==d),None)
    extra={'early_momentum':True,'early_momentum_model':'STORJ_TYPE_V2','early_momentum_score':score,'early_momentum_reasons':reasons+['verified 1H impulse/pullback/protected structure'],'storj_pattern':meta}
    if existing:
        existing.update(extra)
    else:
        w['items'].append({'symbol':sym,'direction':d,'score':None,'score_scale':100,'poi_low':None,'poi_high':None,'status':'ACTIVE','bucket':'EARLY_MOMENTUM','trigger_model':'1H/15m POI -> 5m liquidity sweep -> MSS/CHoCH + displacement -> strict FVG/validated OB first retracement -> live RR >= 1.5','note':'Strict STORJ-type early momentum: verified 1H impulse + real pullback + protected structure + 15m return + BTC relative strength; no chase.','updated_at':w.get('updated_at'),**extra})
p=w.setdefault('policy',{})
p['early_momentum_model']='STORJ_TYPE_V2'
p['early_momentum_count']=len([x for x in w['items'] if x.get('bucket')=='EARLY_MOMENTUM'])
p['early_momentum_rules']='HTF aligned + verified completed 1H impulse + 2-18 bar 10-65% pullback + protected 1H structure + 15m HTF return + BTC RS >=2/3 + anti-chase'
WATCH.write_text(json.dumps(w,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'ok':True,'storj_type_early':[(s,d,sc) for sc,s,d,_,_ in selected]},ensure_ascii=False))
