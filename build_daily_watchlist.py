import csv
import json
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "BNBUSDT"]
DAILY_RECOMMENDATION_COUNT = 3
INPUT = Path("latest/scan_results.csv")
SCAN_INPUT = Path("latest/scan.json")
OUTPUT = Path("latest/watchlist.json")


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def truthy(v):
    return str(v).strip().lower() in {"1", "true", "yes"}


def clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


def choose_side(row):
    long_score = num(row.get("long_score"))
    short_score = num(row.get("short_score"))
    candidates = []
    if long_score is not None:
        candidates.append((long_score, "LONG"))
    if short_score is not None:
        candidates.append((short_score, "SHORT"))
    if not candidates:
        return None, None
    raw_score, direction = max(candidates, key=lambda x: x[0])
    return direction, raw_score


def score_100(row, direction, raw_score):
    if raw_score is None:
        return None
    side = direction.lower()
    grade = str(row.get(f"{side}_grade") or "").strip().upper()
    grade_bonus = {"A+": 8, "A": 7, "A-": 6, "B+": 5, "B": 3, "B-": 2, "C": 0}.get(grade, 0)
    rr = num(row.get(f"{side}_rr"))
    if rr is None:
        rr_bonus = 0
    elif rr >= 3:
        rr_bonus = 5
    elif rr >= 2:
        rr_bonus = 4
    elif rr >= 1.5:
        rr_bonus = 3
    elif rr >= 1:
        rr_bonus = 1
    else:
        rr_bonus = 0
    htf_bonus = 3 if truthy(row.get(f"{side}_htf_aligned")) else 0
    ready_bonus = 3 if truthy(row.get(f"{side}_entry_model_ready")) else 0
    chase = num(row.get(f"{side}_chase_penalty")) or 0.0
    chase_penalty = min(max(chase, 0.0), 5.0) * 2.0
    score = 40.0 + raw_score + grade_bonus + rr_bonus + htf_bonus + ready_bonus - chase_penalty
    return int(round(clamp(score)))


def normalize_position_side(side):
    side = str(side or "").strip().upper()
    if side in {"BUY", "LONG"}:
        return "LONG"
    if side in {"SELL", "SHORT"}:
        return "SHORT"
    return None


def extract_positions(scan):
    positions = scan.get("positions")
    if isinstance(positions, list):
        return [p for p in positions if isinstance(p, dict)]
    if isinstance(positions, dict):
        out = []
        for symbol, p in positions.items():
            if isinstance(p, dict):
                item = dict(p)
                item.setdefault("symbol", symbol)
                out.append(item)
        return out
    return []


def make_item(symbol, direction, raw_score, row, bucket, now):
    display_score = score_100(row, direction, raw_score) if row and raw_score is not None else None
    if bucket == "DEFAULT":
        note = "Core default symbol; always monitored. Direction and 100-point score are refreshed from the 09:00 KST daily scan."
    elif bucket == "CURRENT_POSITION":
        note = "Current Bybit open position; always included while the position remains open, regardless of scanner rank."
    else:
        note = "Daily recommendation; selected and ranked by the 100-point score, refreshed at 09:00 KST and replaced by the next daily scan."
    return {
        "symbol": symbol,
        "direction": direction,
        "score": display_score,
        "score_scale": 100,
        "raw_score": round(raw_score, 4) if raw_score is not None else None,
        "poi_low": None,
        "poi_high": None,
        "status": "ACTIVE",
        "bucket": bucket,
        "trigger_model": "1H/15m POI -> 5m liquidity sweep -> MSS/CHoCH + displacement -> strict FVG/validated OB retracement",
        "note": note,
        "updated_at": now,
    }


if not INPUT.exists():
    raise SystemExit(f"Missing input: {INPUT}")

with INPUT.open("r", encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))

by_symbol = {
    str(r.get("symbol", "")).strip().upper(): r
    for r in rows
    if str(r.get("symbol", "")).strip()
}

scan = {}
if SCAN_INPUT.exists():
    with SCAN_INPUT.open("r", encoding="utf-8") as f:
        scan = json.load(f)

position_fetch_ok = scan.get("position_fetch_ok") is True
positions = extract_positions(scan) if position_fetch_ok else []
position_side_by_symbol = {}
for p in positions:
    symbol = str(p.get("symbol") or "").strip().upper()
    if not symbol:
        continue
    side = normalize_position_side(p.get("side"))
    if side:
        position_side_by_symbol[symbol] = side

position_symbols = sorted(position_side_by_symbol)
now = datetime.now(timezone.utc).isoformat()
items = []
seen = set()


def add_item(symbol, direction, raw_score, row, bucket):
    key = (symbol, direction)
    if key in seen:
        return
    seen.add(key)
    items.append(make_item(symbol, direction, raw_score, row, bucket, now))


for symbol in DEFAULT_SYMBOLS:
    row = by_symbol.get(symbol)
    if row:
        direction, raw_score = choose_side(row)
    else:
        direction, raw_score = None, None
    if direction is None:
        add_item(symbol, "LONG", None, row, "DEFAULT")
        add_item(symbol, "SHORT", None, row, "DEFAULT")
    else:
        add_item(symbol, direction, raw_score, row, "DEFAULT")

# Always include all current Bybit positions, regardless of scanner rank.
for symbol in position_symbols:
    direction = position_side_by_symbol[symbol]
    row = by_symbol.get(symbol)
    raw_score = None
    if row:
        scan_direction, scan_raw_score = choose_side(row)
        if scan_direction == direction:
            raw_score = scan_raw_score

    matching = [i for i in items if i["symbol"] == symbol and i["direction"] == direction]
    if matching:
        matching[0]["bucket"] = "CURRENT_POSITION"
        matching[0]["note"] = "Current Bybit open position; always included while the position remains open, regardless of scanner rank."
    else:
        add_item(symbol, direction, raw_score, row, "CURRENT_POSITION")

# Daily recommendations exclude symbols already covered by defaults or current positions.
ranked = []
for symbol, row in by_symbol.items():
    if symbol in DEFAULT_SYMBOLS or symbol in position_symbols:
        continue
    direction, raw_score = choose_side(row)
    if direction is None or raw_score is None:
        continue
    display_score = score_100(row, direction, raw_score)
    if display_score is None:
        continue
    ranked.append((display_score, raw_score, symbol, direction, row))

ranked.sort(key=lambda x: (x[0], x[1]), reverse=True)
for display_score, raw_score, symbol, direction, row in ranked[:DAILY_RECOMMENDATION_COUNT]:
    add_item(symbol, direction, raw_score, row, "DAILY_RECOMMENDATION")

payload = {
    "ok": True,
    "version": 11,
    "updated_at": now,
    "policy": {
        "mode": "CORE_PLUS_CURRENT_POSITIONS_PLUS_DAILY_RECOMMENDATIONS",
        "default_symbols": DEFAULT_SYMBOLS,
        "include_current_positions": True,
        "position_source": "latest/scan.json",
        "position_fetch_ok": position_fetch_ok,
        "position_symbols": position_symbols,
        "daily_recommendation_count": DAILY_RECOMMENDATION_COUNT,
        "daily_refresh_time_kst": "09:00",
        "daily_recommendations_replace_each_day": True,
        "score_scale": 100,
        "score_field": "score",
        "ranking_field": "score",
        "raw_score_field": "raw_score",
        "no_retrace_no_trade": True,
        "poi_arrival_is_entry": False,
        "poi_timeframes": ["1H", "15m"],
        "entry_timeframe": "5m",
        "entry_model": "1H/15m POI -> touch activation -> 5m liquidity sweep -> 5m MSS/CHoCH + displacement -> 5m strict FVG/validated OB retracement -> live RR >= 1.5",
        "minimum_rr": 1.5,
        "note": "Current open positions are always included while position_fetch_ok is true. Daily recommendations exclude symbols already included as current positions."
    },
    "items": items,
}

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
with OUTPUT.open("w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
    f.write("\n")

print(json.dumps({
    "ok": True,
    "output": str(OUTPUT),
    "score_scale": 100,
    "ranking_field": "score",
    "default_symbols": DEFAULT_SYMBOLS,
    "position_fetch_ok": position_fetch_ok,
    "position_symbols": position_symbols,
    "daily_recommendations": [
        {
            "symbol": symbol,
            "direction": direction,
            "score": display_score,
            "raw_score": round(raw_score, 4),
        }
        for display_score, raw_score, symbol, direction, row in ranked[:DAILY_RECOMMENDATION_COUNT]
    ],
    "item_count": len(items),
}, ensure_ascii=False))
