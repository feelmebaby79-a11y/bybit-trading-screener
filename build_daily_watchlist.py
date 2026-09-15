import csv, json, os
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SYMBOLS=["BTCUSDT","ETHUSDT","XRPUSDT","SOLUSDT","BNBUSDT"]
MAX_DAILY_RECOMMENDATIONS=3
EARLY_MOMENTUM_COUNT=3
MIN_WATCH_SCORE=65
MIN_EARLY_MOMENTUM_SCORE=70
INPUT=Path("latest/scan_results.csv"); SCAN_INPUT=Path("latest/scan.json"); OUTPUT=Path("latest/watchlist.json")
LIVE_POSITIONS_INPUT=Path(os.environ.get("POSITION_SNAPSHOT","/tmp/bybit_positions.json"))

def num(v):
    try:return float(v)
    except (TypeError,ValueError):return None

def truthy(v):return str(v).strip().lower() in {"1","true","yes"}
def trend(direction):return "bullish" if direction=="LONG" else "bearish"
def opp(direction):return "bearish" if direction=="LONG" else "bullish"

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

def early_momentum_v3(row,direction):
    """STORJ_TYPE_V3: independent 100-point quality score; >=70 only."""
    if not row or direction not in {"LONG","SHORT"}:return False,0,[]
    t=trend(direction); o=opp(direction); s=direction.lower(); reasons=[]; pts=0
    # Hard gates: the defining early-momentum structure must exist.
    if row.get("1D")!=t or row.get("4H")!=t:return False,0,["HTF alignment failed"]
    if row.get("1H")!=o or row.get("15m")!=t:return False,0,["1H correction / 15m return failed"]
    pts+=25; reasons.append("HTF 1D/4H aligned (25)")
    pts+=20; reasons.append("1H correction + 15m return (20)")
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
    chase=max(num(row.get(f"{s}_chase_penalty")) or 0,0)
    penalty=min(20,int(round(chase*4)))
    if penalty:pts-=penalty;reasons.append(f"anti-chase penalty (-{penalty})")
    pts=int(max(0,min(100,pts)))
    return pts>=MIN_EARLY_MOMENTUM_SCORE,pts,reasons

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
    note="Current Bybit open position; always included while open." if bucket=="CURRENT_POSITION" else "STORJ_TYPE_V3 early momentum recommendation; 100-point score >=70; strict 5m trigger still required." if bucket=="EARLY_MOMENTUM" else "Daily recommendation selected from the current scan by exact 100-point rubric." if bucket=="DAILY_RECOMMENDATION" else "Core default symbol; monitored continuously."
    x={"symbol":sym,"direction":d,"score":score,"score_scale":100,"poi_low":None,"poi_high":None,"status":"ACTIVE","bucket":bucket,"trigger_model":"1H/15m POI -> 5m liquidity sweep -> MSS/CHoCH + displacement -> strict FVG/validated OB first retracement -> live RR >= 1.5","note":note,"updated_at":now}
    if extra:x.update(extra)
    items.append(x)

for sym in DEFAULT_SYMBOLS:
    sc,d=choose_side(by_symbol.get(sym)); add(sym,d or "LONG","DEFAULT")
for sym in position_symbols:
    d=position_side.get(sym,"LONG"); existing=next((x for x in items if x["symbol"]==sym and x["direction"]==d),None)
    if existing:existing["bucket"]="CURRENT_POSITION";existing["note"]="Current Bybit open position; always included while open."
    else:add(sym,d,"CURRENT_POSITION")

ranked=[]
for sym,row in by_symbol.items():
    if sym in DEFAULT_SYMBOLS or sym in position_symbols:continue
    sc,d=choose_side(row)
    if sc is None:continue
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
for em,sc,sym,d,reasons in early[:EARLY_MOMENTUM_COUNT]:
    add(sym,d,"EARLY_MOMENTUM",{"early_momentum":True,"early_momentum_model":"STORJ_TYPE_V3","early_momentum_score":em,"early_momentum_score_scale":100,"early_momentum_grade":"STRONG" if em>=80 else "RECOMMEND","early_momentum_reasons":reasons})

payload={"ok":True,"version":18,"updated_at":now,"scan_fresh_only":True,"policy":{"mode":"CORE_PLUS_POSITIONS_PLUS_CURRENT_SCAN_RANKED","default_symbols":DEFAULT_SYMBOLS,"include_current_positions":True,"position_source":position_source,"position_fetch_ok":position_fetch_ok,"position_symbols":position_symbols,"daily_recommendation_count":len([x for x in items if x["bucket"]=="DAILY_RECOMMENDATION"]),"daily_recommendation_max":MAX_DAILY_RECOMMENDATIONS,"minimum_watch_score":MIN_WATCH_SCORE,"early_momentum_count":len([x for x in items if x.get("early_momentum") and x["bucket"]=="EARLY_MOMENTUM"]),"early_momentum_max":EARLY_MOMENTUM_COUNT,"early_momentum_model":"STORJ_TYPE_V3","early_momentum_score_scale":100,"early_momentum_minimum_score":MIN_EARLY_MOMENTUM_SCORE,"early_momentum_no_forced_pick":True,"early_momentum_grades":{"STRONG":"80-100","RECOMMEND":"70-79","WATCH":"60-69 (not added)","REJECT":"0-59"},"persistent_setup_count":0,"persistent_after_poi_touch":False,"recommendations_must_exist_in_current_scan":True,"reuse_prior_watchlist_recommendations":False,"score_scale":100,"no_retrace_no_trade":True,"poi_arrival_is_entry":False,"poi_timeframes":["1H","15m"],"entry_timeframe":"5m","entry_model":"1H/15m POI -> touch -> confirmed 5m liquidity sweep -> MSS/CHoCH + displacement -> strict FVG/validated OB first retracement -> live RR >= 1.5","minimum_rr":1.5},"items":items}
OUTPUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps({"ok":True,"version":18,"scan_fresh_only":True,"positions":position_symbols,"daily":[(x["symbol"],x["direction"],x["score"]) for x in items if x["bucket"]=="DAILY_RECOMMENDATION"],"early":[(x["symbol"],x["direction"],x.get("early_momentum_score")) for x in items if x["bucket"]=="EARLY_MOMENTUM"],"early_min":MIN_EARLY_MOMENTUM_SCORE,"persistent":[]},ensure_ascii=False))