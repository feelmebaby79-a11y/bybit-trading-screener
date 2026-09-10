import csv
import json
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "BNBUSDT"]
DAILY_RECOMMENDATION_COUNT = 3
INPUT = Path("latest/scan_results.csv")
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
    """Convert scanner internals into the agreed user-facing 0-100 score."""
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


def make_item(symbol, direction, raw_score, row, bucket, now):
    display_score = score_100(row, direction, raw_score) if row else None
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
        "note": (
            "Core default symbol; always monitored. Direction and 100-point score are refreshed from the 09:00 KST daily scan."
            if bucket == "DEFAULT"
            else "Daily recommendation; selected and ranked by the 100-point score, refreshed at 09:00 KST and replaced by the next daily scan."
        ),
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

now = datetime.now(timezone.utc).isoformat()
items = []

for symbol in DEFAULT_SYMBOLS:
    row = by_symbol.get(symbol)
    if row:
        direction, raw_score = choose_side(row)
    else:
        direction, raw_score = None, None

    if direction is None:
        items.append(make_item(symbol, "LONG", None, row, "DEFAULT", now))
        items.append(make_item(symbol, "SHORT", None, row, "DEFAULT", now))
    else:
        items.append(make_item(symbol, direction, raw_score, row, "DEFAULT", now))

# Daily recommendations are selected AND ranked by the agreed 0-100 score.
# Raw score remains only an internal audit/tie-break value.
ranked = []
for symbol, row in by_symbol.items():
    if symbol in DEFAULT_SYMBOLS:
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
    items.append(make_item(symbol, direction, raw_score, row, "DAILY_RECOMMENDATION", now))

payload = {
    "ok": True,
    "version": 10,
    "updated_at": now,
    "policy": {
        "mode": "CORE_PLUS_DAILY_RECOMMENDATIONS",
        "default_symbols": DEFAULT_SYMBOLS,
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
        "note": "Daily recommendations are selected and ranked by the user-facing 0-100 score. Scanner raw composite is retained separately only for audit/tie-breaks."
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
