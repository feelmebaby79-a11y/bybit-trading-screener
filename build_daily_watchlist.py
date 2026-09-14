import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "BNBUSDT"]
DAILY_RECOMMENDATION_COUNT = 3
EARLY_MOMENTUM_COUNT = 3
INPUT = Path("latest/scan_results.csv")
SCAN_INPUT = Path("latest/scan.json")
OUTPUT = Path("latest/watchlist.json")
LIVE_POSITIONS_INPUT = Path(os.environ.get("POSITION_SNAPSHOT", "/tmp/bybit_positions.json"))

def num(v):
    try: return float(v)
    except (TypeError, ValueError): return None

def truthy(v): return str(v).strip().lower() in {"1", "true", "yes"}
def clamp(v, lo=0.0, hi=100.0): return max(lo, min(hi, v))

def choose_side(row):
    if not row: return None, None
    vals=[]
    for d in ("LONG","SHORT"):
        s=num(row.get(f"{d.lower()}_score"))
        if s is not None: vals.append((s,d))
    if not vals: return None,None
    raw,direction=max(vals,key=lambda x:x[0])
    return direction,raw

def score_100(row,direction,raw_score):
    if raw_score is None or not row: return None
    side=direction.lower(); grade=str(row.get(f"{side}_grade") or "").strip().upper()
    grade_bonus={"A+":8,"A":7,"A-":6,"B+":5,"B":3,"B-":2,"C":0}.get(grade,0)
    rr=num(row.get(f"{side}_rr")); rr_bonus=5 if rr is not None and rr>=3 else 4 if rr is not None and rr>=2 else 3 if rr is not None and rr>=1.5 else 1 if rr is not None and rr>=1 else 0
    htf_bonus=3 if truthy(row.get(f"{side}_htf_aligned")) else 0
    ready_bonus=3 if truthy(row.get(f"{side}_entry_model_ready")) else 0
    chase=num(row.get(f"{side}_chase_penalty")) or 0.0
    return int(round(clamp(40.0+raw_score+grade_bonus+rr_bonus+htf_bonus+ready_bonus-min(max(chase,0.0),5.0)*2.0)))

def early_momentum(row,direction):
    """POLYX-style pre-expansion pattern: HTF trend intact, 1H correction, 15m turns back with relative strength."""
    if not row or direction not in {"LONG","SHORT"}: return False, 0.0, []
    side=direction.lower(); opp="bearish" if direction=="LONG" else "bullish"; trend="bullish" if direction=="LONG" else "bearish"
    reasons=[]; pts=0.0
    if row.get("1D")==trend and row.get("4H")==trend: pts+=4; reasons.append("1D/4H structure aligned")
    else: return False, pts, reasons
    if row.get("1H")==opp and row.get("15m")==trend: pts+=4; reasons.append("1H correction + 15m re-turn")
    else: return False, pts, reasons
    # The protected 1D structure is already encoded by the structural trend; require last BOS/CHoCH not to oppose the side.
    bos=str(row.get("1D_last_bos_side") or "").lower(); choch=str(row.get("1D_last_choch_side") or "").lower()
    if bos==trend: pts+=2; reasons.append("1D BOS supports side")
    if choch and choch!=trend: pts-=2
    rs=[num(row.get("rs_vs_btc_30m_pct")),num(row.get("rs_vs_btc_1h_pct")),num(row.get("rs_vs_btc_4h_pct"))]
    signed=[x if direction=="LONG" else -x for x in rs if x is not None]
    positive=sum(x>0 for x in signed)
    if positive>=2: pts+=3; reasons.append("relative strength confirms")
    if truthy(row.get(f"{side}_mss15")): pts+=2; reasons.append("15m MSS")
    fvg=str(row.get(f"{side}_fvg5") or "").lower()
    if fvg==trend: pts+=1; reasons.append("5m FVG aligned")
    chase=num(row.get(f"{side}_chase_penalty")) or 0
    if chase>0: pts-=min(chase,3); reasons.append("chase penalty")
    return pts>=8, pts, reasons

def normalize_position_side(side):
    side=str(side or "").strip().upper()
    if side in {"BUY","LONG"}: return "LONG"
    if side in {"SELL","SHORT"}: return "SHORT"
    return None

def extract_position_records(scan):
    positions=scan.get("positions")
    if isinstance(positions,list): return [p for p in positions if isinstance(p,dict)]
    if isinstance(positions,dict):
        out=[]
        for symbol,p in positions.items():
            if isinstance(p,dict):
                item=dict(p); item.setdefault("symbol",symbol); out.append(item)
        return out
    return []

def make_item(symbol,direction,raw_score,row,bucket,now,extra=None):
    note=("Core default symbol; always monitored. Direction and 100-point score are refreshed from the daily scan." if bucket=="DEFAULT" else "Current Bybit open position; always included while the position remains open, regardless of scanner rank." if bucket=="CURRENT_POSITION" else "POLYX-style early momentum candidate: HTF structure intact, 1H correction and 15m re-turn; ENTRY still requires the strict 5m trigger." if bucket=="EARLY_MOMENTUM" else "Daily recommendation; selected and ranked by the 100-point score and replaced by the next daily scan.")
    item={"symbol":symbol,"direction":direction,"score":score_100(row,direction,raw_score),"score_scale":100,"raw_score":round(raw_score,4) if raw_score is not None else None,"poi_low":None,"poi_high":None,"status":"ACTIVE","bucket":bucket,"trigger_model":"1H/15m POI -> 5m liquidity sweep -> MSS/CHoCH + displacement -> strict FVG/validated OB retracement","note":note,"updated_at":now}
    if extra: item.update(extra)
    return item

if not INPUT.exists(): raise SystemExit(f"Missing input: {INPUT}")
with INPUT.open("r",encoding="utf-8-sig",newline="") as f: rows=list(csv.DictReader(f))
by_symbol={str(r.get("symbol","")).strip().upper():r for r in rows if str(r.get("symbol","")).strip()}
scan={}
if SCAN_INPUT.exists():
    with SCAN_INPUT.open("r",encoding="utf-8") as f: scan=json.load(f)
position_fetch_ok=scan.get("position_fetch_ok") is True
scan_position_symbols=[str(s).strip().upper() for s in (scan.get("position_symbols") or []) if str(s).strip()]
position_side_by_symbol={}; position_source="scan.json"
if LIVE_POSITIONS_INPUT.exists():
    try:
        live=json.loads(LIVE_POSITIONS_INPUT.read_text(encoding="utf-8"))
        if live.get("ok") is True and isinstance(live.get("positions"),list):
            for p in live["positions"]:
                symbol=str(p.get("symbol") or "").strip().upper(); side=normalize_position_side(p.get("side"))
                if symbol and side: position_side_by_symbol[symbol]=side
            if position_side_by_symbol:
                position_source="Bybit Private Position API via Worker"; position_fetch_ok=True; scan_position_symbols=sorted(position_side_by_symbol)
    except Exception as e: print(f"Warning: failed to read live position snapshot: {e}")
if not position_side_by_symbol and position_fetch_ok:
    for p in extract_position_records(scan):
        symbol=str(p.get("symbol") or "").strip().upper(); side=normalize_position_side(p.get("side"))
        if symbol and side: position_side_by_symbol[symbol]=side
for symbol in scan_position_symbols:
    if symbol not in position_side_by_symbol:
        direction,_=choose_side(by_symbol.get(symbol))
        if direction: position_side_by_symbol[symbol]=direction
position_symbols=sorted(set(scan_position_symbols)|set(position_side_by_symbol)) if position_fetch_ok else []
now=datetime.now(timezone.utc).isoformat(); items=[]; seen=set()
def add_item(symbol,direction,raw_score,row,bucket,extra=None):
    key=(symbol,direction)
    if key in seen:
        # Promote an existing item to EARLY_MOMENTUM metadata without losing CURRENT_POSITION priority.
        for i in items:
            if i["symbol"]==symbol and i["direction"]==direction and extra:
                i.update(extra)
        return
    seen.add(key); items.append(make_item(symbol,direction,raw_score,row,bucket,now,extra))

for symbol in DEFAULT_SYMBOLS:
    row=by_symbol.get(symbol); direction,raw=choose_side(row)
    if direction:add_item(symbol,direction,raw,row,"DEFAULT")
    else: add_item(symbol,"LONG",None,row,"DEFAULT"); add_item(symbol,"SHORT",None,row,"DEFAULT")
for symbol in position_symbols:
    row=by_symbol.get(symbol); direction=position_side_by_symbol.get(symbol)
    if not direction: continue
    scan_dir,scan_raw=choose_side(row); raw=scan_raw if scan_dir==direction else None
    matched=[i for i in items if i["symbol"]==symbol and i["direction"]==direction]
    if matched:
        matched[0]["bucket"]="CURRENT_POSITION"; matched[0]["note"]="Current Bybit open position; always included while the position remains open, regardless of scanner rank."
    else:add_item(symbol,direction,raw,row,"CURRENT_POSITION")
ranked=[]
for symbol,row in by_symbol.items():
    if symbol in DEFAULT_SYMBOLS or symbol in position_symbols: continue
    direction,raw=choose_side(row)
    if direction is None or raw is None: continue
    display=score_100(row,direction,raw)
    if display is not None: ranked.append((display,raw,symbol,direction,row))
ranked.sort(key=lambda x:(x[0],x[1]),reverse=True)
for display,raw,symbol,direction,row in ranked[:DAILY_RECOMMENDATION_COUNT]: add_item(symbol,direction,raw,row,"DAILY_RECOMMENDATION")

early=[]
for symbol,row in by_symbol.items():
    direction,raw=choose_side(row)
    if direction is None or raw is None: continue
    ok,em_score,reasons=early_momentum(row,direction)
    if ok: early.append((em_score,score_100(row,direction,raw) or 0,raw,symbol,direction,row,reasons))
early.sort(key=lambda x:(x[0],x[1],x[2]),reverse=True)
for em_score,display,raw,symbol,direction,row,reasons in early[:EARLY_MOMENTUM_COUNT]:
    add_item(symbol,direction,raw,row,"EARLY_MOMENTUM",{"early_momentum":True,"early_momentum_score":round(em_score,2),"early_momentum_reasons":reasons})

payload={"ok":True,"version":14,"updated_at":now,"policy":{"mode":"CORE_PLUS_CURRENT_POSITIONS_PLUS_DAILY_RECOMMENDATIONS_PLUS_EARLY_MOMENTUM","default_symbols":DEFAULT_SYMBOLS,"include_current_positions":True,"position_source":position_source,"position_fetch_ok":position_fetch_ok,"position_symbols":position_symbols,"daily_recommendation_count":DAILY_RECOMMENDATION_COUNT,"early_momentum_count":EARLY_MOMENTUM_COUNT,"early_momentum_model":"POLYX pattern: 1D+4H aligned -> 1H correction -> 15m re-turn, supported by structure/RS/MSS; not an ENTRY signal","daily_refresh_time_kst":"09:00","daily_recommendations_replace_each_day":True,"score_scale":100,"score_field":"score","ranking_field":"score","raw_score_field":"raw_score","no_retrace_no_trade":True,"poi_arrival_is_entry":False,"poi_timeframes":["1H","15m"],"entry_timeframe":"5m","entry_model":"1H/15m POI -> touch activation -> 5m liquidity sweep -> 5m MSS/CHoCH + displacement -> 5m strict FVG/validated OB retracement -> live RR >= 1.5","minimum_rr":1.5},"items":items}
OUTPUT.parent.mkdir(parents=True,exist_ok=True)
with OUTPUT.open("w",encoding="utf-8") as f: json.dump(payload,f,ensure_ascii=False,indent=2); f.write("\n")
print(json.dumps({"ok":True,"position_fetch_ok":position_fetch_ok,"position_source":position_source,"position_symbols":position_symbols,"position_sides":position_side_by_symbol,"early_momentum":[{"symbol":x[3],"direction":x[4],"early_score":x[0]} for x in early[:EARLY_MOMENTUM_COUNT]],"item_count":len(items)},ensure_ascii=False))
