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
    score, direction = max(candidates, key=lambda x: x[0])
    return direction, score


def make_item(symbol, direction, score, bucket, now):
    return {
        "symbol": symbol,
        "direction": direction,
        "score": round(score, 2) if score is not None else None,
        "poi_low": None,
        "poi_high": None,
        "status": "ACTIVE",
        "bucket": bucket,
        "trigger_model": "1H/15m POI -> 5m liquidity sweep -> MSS/CHoCH + displacement -> strict FVG/validated OB retracement",
        "note": (
            "Core default symbol; always monitored. Direction is refreshed from the 09:00 KST daily scan."
            if bucket == "DEFAULT"
            else "Daily recommendation; refreshed at 09:00 KST and replaced by the next daily scan."
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

# Five core symbols are always present. Their directional bias is refreshed daily.
for symbol in DEFAULT_SYMBOLS:
    row = by_symbol.get(symbol)
    if row:
        direction, score = choose_side(row)
    else:
        direction, score = None, None

    # If a core symbol is unexpectedly absent from the scan universe, keep it alive
    # in both directions rather than silently dropping it from monitoring.
    if direction is None:
        items.append(make_item(symbol, "LONG", None, "DEFAULT", now))
        items.append(make_item(symbol, "SHORT", None, "DEFAULT", now))
    else:
        items.append(make_item(symbol, direction, score, "DEFAULT", now))

# Daily recommendations are the strongest non-core directional scores from this scan.
ranked = []
for symbol, row in by_symbol.items():
    if symbol in DEFAULT_SYMBOLS:
        continue
    direction, score = choose_side(row)
    if direction is None or score is None:
        continue
    ranked.append((score, symbol, direction))

ranked.sort(reverse=True)
for score, symbol, direction in ranked[:DAILY_RECOMMENDATION_COUNT]:
    items.append(make_item(symbol, direction, score, "DAILY_RECOMMENDATION", now))

payload = {
    "ok": True,
    "version": 8,
    "updated_at": now,
    "policy": {
        "mode": "CORE_PLUS_DAILY_RECOMMENDATIONS",
        "default_symbols": DEFAULT_SYMBOLS,
        "daily_recommendation_count": DAILY_RECOMMENDATION_COUNT,
        "daily_refresh_time_kst": "09:00",
        "daily_recommendations_replace_each_day": True,
        "no_retrace_no_trade": True,
        "poi_arrival_is_entry": False,
        "poi_timeframes": ["1H", "15m"],
        "entry_timeframe": "5m",
        "entry_model": "1H/15m POI -> 5m liquidity sweep -> 5m MSS/CHoCH + displacement -> 5m strict FVG/validated OB retracement -> live RR >= 1.5",
        "minimum_rr": 1.5,
        "note": "BTC/ETH/XRP/SOL/BNB are always monitored. Daily recommendations are added after the 09:00 KST scan and replaced at the next 09:00 KST scan."
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
    "default_symbols": DEFAULT_SYMBOLS,
    "daily_recommendations": [
        {"symbol": s, "direction": d, "score": round(sc, 2)}
        for sc, s, d in ranked[:DAILY_RECOMMENDATION_COUNT]
    ],
    "item_count": len(items),
}, ensure_ascii=False))
