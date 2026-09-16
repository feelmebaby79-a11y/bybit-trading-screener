import csv, json, os
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SYMBOLS=["BTCUSDT","ETHUSDT","XRPUSDT","SOLUSDT","BNBUSDT"]
MAX_DAILY_RECOMMENDATIONS=3
EARLY_MOMENTUM_COUNT=3
HIGH_VOLATILITY_COUNT=5
MIN_WATCH_SCORE=65
MIN_EARLY_MOMENTUM_SCORE=70
MIN_HIGH_VOLATILITY_SCORE=65
INPUT=Path("latest/scan_results.csv"); SCAN_INPUT=Path("latest/scan.json"); OUTPUT=Path("latest/watchlist.json")
LIVE_POSITIONS_INPUT=Path(os.environ.get("POSITION_SNAPSHOT","/tmp/bybit_positions.json"))
# Bybit synthetic/tokenized TradFi symbols seen in this universe. Keep this explicit until instrument metadata is persisted in scan CSV.
NON_CRYPTO_BASES={"XAU","XAG","XAUT","TSLA","DELL","MSTR","CRCL","SOXL","AAPL","AMZN","GOOG","GOOGL","META","MSFT","NVDA","NFLX","COIN","SPY","QQQ"}

def num(v):
    try:return float(v)
    except (TypeError,ValueError):return None

def truthy(v):return str(v).strip().lower() in {"1","true","yes"}
def trend(direction):return "bullish" if direction=="LONG" else "bearish"
def opp(direction):return "bearish" if direction=="LONG" else "bullish"
def late_location(row,direction):
    loc=str((row or {}).get("1H_location") or "").strip().lower()
    return (direction=="LONG" and loc=="premium") or (direction=="SHORT" and loc=="discount")
def crypto_native(sym):
    base=str(sym or "").upper().removesuffix("USDT")
    return bool(base) and base not in NON_CRYPTO_BASES

def score_100(row,direction):
    if not row or direction not in {"LONG","SHORT"}:return None
    s=direction.lower(); t=trend(direction); total=0.0
    total += 13 if row.get("1D")==t else 0; total += 12 if row.get("4H")==t else 0
    total += 8 if row.get("1H")==t else 0
    loc=str(row.get("1H_location") or "").lower(); favorable="discount" if direction=="LONG" else "premium"
    total += 7 if loc==favorable else 3 if loc=="equilibrium" else 0
    total += 5 if row.get("15m")==t else 0
    total += 5 if truthy(row.get(f"{s}_sweep15")) else 0; total += 5 if truthy(row.get(f"{s}_mss15")) else 0; total += 5 if truthy(row.get(f"{s}_disp15")) else 0
    fvg=str(row.get(f"{s}_fvg5") or "").lower(); trig=num(row.get(f"{s}_5m_trigger")) or 0
    total += 7 if fvg==t else 0; total += min(max(trig,0),4); total += 4 if truthy(row.get(f"{s}_entry_model_ready")) else 0
    chase=max(num(row.get(f"{s}_chase_penalty")) or 0,0); total += max(0,10-min(chase,5)*2)
    rr=num(row.get(f"{s}_rr"))
    if rr is not None: total += 10 if rr>=3 else 8 if rr>=2 else 6 if rr>=1.5 else 3 if rr>=1 else 0
    rs=[num(row.get("rs_vs_btc_30m_pct")),num(row.get("rs_vs_btc_1h_pct")),num(row.get("rs_vs_btc_4h_pct"))]; signed=[x if direction=="LONG" else -x for x in rs if x is not None]
    wins=sum(x>0 for x in signed); total += 5 if wins>=2 else 2 if wins==1 else 0
    return int(round(max(0,min(100,total))))

def choose_side(row):
    if not row:return None,None
    scored=[(score_100(row,d),d) for d in ("LONG","SHORT")]; scored=[x for x in scored if x[0] is not None]
    return max(scored,key=lambda x:x[0]) if scored else (None,None)

def high_volatility_score(row,direction):
    """Crypto-only tradable-volatility score. Rewards movement/liquidity/RS/OI/structure, rejects extreme chase and late location."""
    if not row or direction not in {"LONG","SHORT"}:return False,0,[]
    if late_location(row,direction):return False,0,["late 1H location"]
    pct=abs(num(row.get("price24h_pct")) or 0); turnover=num(row.get("turnover24h")) or 0
    s=direction.lower(); t=trend(direction); chase=max(num(row.get(f"{s}_chase_penalty")) or 0,0)
    if pct>=40 or chase>=4:return False,0,["overextended/chase rejected"]
    pts=0; reasons=[]
    # Volatility 30
    v=30 if pct>=20 else 25 if pct>=12 else 20 if pct>=8 else 14 if pct>=5 else 8 if pct>=3 else 0
    pts+=v; reasons.append(f"24h move {pct:.2f}% ({v})")
    # Liquidity 15
    l=15 if turnover>=100_000_000 else 12 if turnover>=50_000_000 else 9 if turnover>=20_000_000 else 5 if turnover>=5_000_000 else 0
    pts+=l; reasons.append(f"turnover ({l})")
    # Directional structure 20
    aligned=sum(row.get(tf)==t for tf in ("1D","4H","1H","15m")); st=[0,5,10,15,20][aligned]; pts+=st; reasons.append(f"TF alignment {aligned}/4 ({st})")
    # BTC RS 15
    rs=[num(row.get("rs_vs_btc_30m_pct")),num(row.get("rs_vs_btc_1h_pct")),num(row.get("rs_vs_btc_4h_pct"))]
    signed=[x if direction=="LONG" else -x for x in rs if x is not None]; wins=sum(x>0 for x in signed)
    r=15 if wins==3 else 10 if wins==2 else 4 if wins==1 else 0; pts+=r; reasons.append(f"BTC RS {wins}/3 ({r})")
    # OI participation 10
    oi=[num(row.get("oi_change_15m_pct")),num(row.get("oi_change_1h_pct")),num(row.get("oi_change_4h_pct"))]
    signed_oi=[x if direction=="LONG" else -x for x in oi if x is not None]; ow=sum(x>0 for x in signed_oi)
    o=10 if ow==3 else 7 if ow==2 else 3 if ow==1 else 0; pts+=o; reasons.append(f"OI support {ow}/3 ({o})")
    # ICT confirmation 10
    ict=sum([truthy(row.get(f"{s}_sweep15")),truthy(row.get(f"{s}_mss15")),truthy(row.get(f"{s}_disp15")),str(row.get(f"{s}_fvg5") or "").lower()==t,truthy(row.get(f"{s}_entry_model_ready"))])
    ip=min(10,ict*2); pts+=ip; reasons.append(f"ICT confirmations {ict}/5 ({ip})")
    penalty=min(15,int(round(chase*4)))
    if penalty:pts-=penalty;reasons.append(f"anti-chase (-{penalty})")
    pts=int(max(0,min(100,pts)))
    return pts>=MIN_HIGH_VOLATILITY_SCORE,pts,reasons

def early_momentum_v3(row,direction):
    if not row or direction not in {"LONG","SHORT"}:return False,0,[]
    if late_location(row,direction):return False,0,["rejected: late 1H location"]
    t=trend(direction); o=opp(direction); s=direction.lower(); reasons=[]; pts=0
    if row.get("1D")!=t or row.get("4H")!=t:return False,0,["HTF alignment failed"]
    if row.get("1H")!=o or row.get("15m")!=t:return False,0,["1H correction / 15m return failed"]
    pts+=25; reasons.append("HTF 1D/4H aligned (25)"); pts+=20; reasons.append("1H correction + 15m return (20)")
    bos=str(row.get("1D_last_bos_side") or "").lower(); choch=str(row.get("1D_last_choch_side") or "").lower()
    if bos==t:pts+=10;reasons.append("1D BOS supports direction (10)")
    if choch and choch!=t:pts-=10;reasons.append("opposing 1D CHoCH (-10)")
    rs=[num(row.get("rs_vs_btc_30m_pct")),num(row.get("rs_vs_btc_1h_pct")),num(row.get("rs_vs_btc_4h_pct"))]
    signed=[x if direction=="LONG" else -x for x in rs if x is not None]; wins=sum(x>0 for x in signed)
    if wins>=3:pts+=15;reasons.append("BTC relative strength 3/3 (15)")
    elif wins>=2:pts+=10;reasons.append("BTC relative strength 2/3 (10)")
    if truthy(row.get(f"{s}_sweep15")):pts+=5;reasons.append("15m liquidity sweep (5)")
    if truthy(row.get(f"{s}_mss15")):pts+=8;reasons.append("15m MSS (8)")
    if truthy(row.get(f"{s}_disp15")):pts+=7;reasons.append("15m displacement (7)")
    if str(row.get(f"{s}_fvg5") or "").lower()==t:pts+=5;reasons.append("5m FVG aligned (5)")
    if truthy(row.get(f"{s}_entry_model_ready")):pts+=5;reasons.append("entry model ready (5)")
    rr=num(row.get(f"{s}_rr"))
    if rr is not None and rr>=2:pts+=5;reasons.append("RR >= 2 (5)")
    elif rr is not None and rr>=1.5:pts+=3;reasons.append("RR >= 1.5 (3)")
    chase=max(num(row.get(f"{s}_chase_penalty")) or 0,0); penalty=min(20,int(round(chase*4)))
    if penalty:pts-=penalty;reasons.append(f"anti-chase penalty (-{penalty})")
    pts=int(max(0,min(100,pts))); return pts>=MIN_EARLY_MOMENTUM_SCORE,pts,reasons

def normalize_side(v):
    v=str(v or "").upper(); return "LONG" if v in {"BUY","LONG"} else "SHORT" if v in {"SELL","SHORT"} else None

with INPUT.open("r",encoding="utf-8-sig",newline="") as f:rows=list(csv.DictReader(f))
by_symbol={str(r.get("symbol","")).upper():r for r in rows if r.get("symbol")}
scan=json.loads(SCAN_INPUT.read_text(encoding="utf-8")) if SCAN_INPUT.exists() else {}
position_fetch_ok=scan.get("position_fetch_ok") is True
position_symbols=[str(x).upper() for x in scan.get("position_symbols",[])]; position_side={}; position_source="scan.json"
if LIVE_POSITIONS_INPUT.exists():
    live=json.loads(LIVE_POSITIONS_INPUT.read_text(encoding="utf-8"))
    if live.get("ok") is True and isinstance(live.get("positions"),list):
        for p in live["positions"]:
            sym=str(p.get("symbol") or "").upper(); side=normalize_side(p.get("side"))
            if sym and side:position_side[sym]=side
        position_symbols=sorted(position_side);position_fetch_ok=True;position_source="Bybit Private Position API via Worker"
for sym in position_symbols:
    if sym not in position_side:
        sc,d=choose_side(by_symbol.get(sym)); position_side[sym]=d or "LONG"

now=datetime.now(timezone.utc).isoformat();items=[];seen=set()
def add(sym,d,bucket,extra=None):
    key=(sym,d)
    if key in seen:
        if extra:
            for x in items:
                if (x["symbol"],x["direction"])==key:x.update(extra)
        return
    seen.add(key);row=by_symbol.get(sym);score=score_100(row,d)
    notes={"CURRENT_POSITION":"Current Bybit open position; always included while open.","EARLY_MOMENTUM":"STORJ_TYPE_V3 Early Momentum; strict 5m trigger still required.","DAILY_RECOMMENDATION":"Daily recommendation selected from current scan; late-location hard gate applied.","HIGH_VOLATILITY_CRYPTO":"Crypto-only high-volatility recommendation from current scan; overextension/location gates applied; strict 5m trigger still required."}
    x={"symbol":sym,"direction":d,"score":score,"score_scale":100,"poi_low":None,"poi_high":None,"status":"ACTIVE","bucket":bucket,"trigger_model":"1H/15m POI -> 5m liquidity sweep -> MSS/CHoCH + displacement -> strict FVG/validated OB first retracement -> live RR >= 1.5","note":notes.get(bucket,"Core default symbol; monitored continuously."),"updated_at":now}
    if extra:x.update(extra)
    items.append(x)

for sym in DEFAULT_SYMBOLS:
    sc,d=choose_side(by_symbol.get(sym)); add(sym,d or "LONG","DEFAULT")
for sym in position_symbols:
    d=position_side.get(sym,"LONG"); existing=next((x for x in items if x["symbol"]==sym and x["direction"]==d),None)
    if existing:existing["bucket"]="CURRENT_POSITION";existing["note"]="Current Bybit open position; always included while open."
    else:add(sym,d,"CURRENT_POSITION")

ranked=[]; late_rejected=[]
for sym,row in by_symbol.items():
    if sym in DEFAULT_SYMBOLS or sym in position_symbols:continue
    sc,d=choose_side(row)
    if sc is None:continue
    if late_location(row,d):late_rejected.append((sym,d,str(row.get("1H_location") or ""),sc));continue
    rr=num(row.get(f"{d.lower()}_rr")); htf=truthy(row.get(f"{d.lower()}_htf_aligned"))
    if sc>=MIN_WATCH_SCORE and htf and rr is not None and rr>=1.5:ranked.append((sc,sym,d))
ranked.sort(reverse=True)
for sc,sym,d in ranked[:MAX_DAILY_RECOMMENDATIONS]:add(sym,d,"DAILY_RECOMMENDATION")

early=[]
for sym,row in by_symbol.items():
    for d in ("LONG","SHORT"):
        ok,em,reasons=early_momentum_v3(row,d)
        if ok:early.append((em,score_100(row,d) or 0,sym,d,reasons))
early.sort(reverse=True)
for em,sc,sym,d,reasons in early[:EARLY_MOMENTUM_COUNT]:add(sym,d,"EARLY_MOMENTUM",{"early_momentum":True,"early_momentum_model":"STORJ_TYPE_V3","early_momentum_score":em,"early_momentum_score_scale":100,"early_momentum_grade":"STRONG" if em>=80 else "RECOMMEND","early_momentum_reasons":reasons})

highvol=[]
for sym,row in by_symbol.items():
    if not crypto_native(sym) or sym in position_symbols:continue
    for d in ("LONG","SHORT"):
        ok,hv,reasons=high_volatility_score(row,d)
        if ok:highvol.append((hv,abs(num(row.get("price24h_pct")) or 0),sym,d,reasons))
highvol.sort(reverse=True)
for hv,pct,sym,d,reasons in highvol[:HIGH_VOLATILITY_COUNT]:add(sym,d,"HIGH_VOLATILITY_CRYPTO",{"high_volatility":True,"high_volatility_score":hv,"high_volatility_score_scale":100,"high_volatility_24h_abs_pct":round(pct,4),"high_volatility_reasons":reasons})

payload={"ok":True,"version":22,"updated_at":now,"scan_fresh_only":True,"policy":{"mode":"CORE_PLUS_POSITIONS_PLUS_CURRENT_SCAN_THREE_TRACKS","default_symbols":DEFAULT_SYMBOLS,"include_current_positions":True,"position_source":position_source,"position_fetch_ok":position_fetch_ok,"position_symbols":position_symbols,"daily_recommendation_count":len([x for x in items if x["bucket"]=="DAILY_RECOMMENDATION"]),"daily_recommendation_max":MAX_DAILY_RECOMMENDATIONS,"minimum_watch_score":MIN_WATCH_SCORE,"daily_late_location_hard_gate":True,"early_momentum_count":len([x for x in items if x["bucket"]=="EARLY_MOMENTUM"]),"early_momentum_max":EARLY_MOMENTUM_COUNT,"early_momentum_model":"STORJ_TYPE_V3","early_momentum_minimum_score":MIN_EARLY_MOMENTUM_SCORE,"high_volatility_crypto_count":len([x for x in items if x["bucket"]=="HIGH_VOLATILITY_CRYPTO"]),"high_volatility_crypto_max":HIGH_VOLATILITY_COUNT,"high_volatility_crypto_minimum_score":MIN_HIGH_VOLATILITY_SCORE,"high_volatility_crypto_only":True,"high_volatility_overextension_reject":"abs(24h)>=40% or chase>=4","high_volatility_components":"24h move 30 + turnover 15 + TF alignment 20 + BTC RS 15 + OI 10 + ICT 10 - chase penalty","persistent_setup_count":0,"recommendations_must_exist_in_current_scan":True,"reuse_prior_watchlist_recommendations":False,"no_retrace_no_trade":True,"poi_arrival_is_entry":False,"entry_timeframe":"5m","minimum_rr":1.5},"items":items}
OUTPUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps({"ok":True,"version":22,"daily":[(x["symbol"],x["direction"],x["score"]) for x in items if x["bucket"]=="DAILY_RECOMMENDATION"],"early":[(x["symbol"],x["direction"],x.get("early_momentum_score")) for x in items if x["bucket"]=="EARLY_MOMENTUM"],"high_volatility_crypto":[(x["symbol"],x["direction"],x.get("high_volatility_score"),x.get("high_volatility_24h_abs_pct")) for x in items if x["bucket"]=="HIGH_VOLATILITY_CRYPTO"]},ensure_ascii=False))