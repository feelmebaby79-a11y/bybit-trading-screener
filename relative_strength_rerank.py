#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import math
import time

import numpy as np
import pandas as pd
import requests

BASE = "https://bybit-trading-screener.feelmebaby79.workers.dev"
CATEGORY = "linear"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "bybit-relative-strength-rerank/1.1"})


def fetch_klines(symbol, interval, limit=8, retries=3):
    url = BASE + "/v5/market/kline"
    params = {"category": CATEGORY, "symbol": symbol, "interval": interval, "limit": limit}
    err = None
    for i in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=20)
            r.raise_for_status()
            payload = r.json()
            if payload.get("retCode") != 0:
                raise RuntimeError(payload.get("retMsg", "Bybit API error"))
            rows = payload.get("result", {}).get("list", [])
            if len(rows) < 2:
                raise RuntimeError(f"Not enough klines for {symbol} {interval}")
            parsed = [(int(row[0]), float(row[4])) for row in rows]
            parsed.sort(key=lambda x: x[0])
            return parsed
        except Exception as exc:
            err = exc
            time.sleep(0.4 * (2 ** i))
    raise RuntimeError(f"{symbol} {interval}: {err}")


def period_return(symbol, interval, bars_back):
    rows = fetch_klines(symbol, interval, limit=max(bars_back + 2, 8))
    if len(rows) <= bars_back:
        return np.nan
    latest = rows[-1][1]
    past = rows[-1 - bars_back][1]
    if past <= 0:
        return np.nan
    return (latest / past - 1.0) * 100.0


def clamp(value, low, high):
    if not np.isfinite(value):
        return 0.0
    return max(low, min(high, value))


def persistent_strength_bonus(rs30, rs1h, rs4h, side):
    """Reward sustained BTC outperformance across horizons, not one-bar pumps."""
    directional = 1.0 if side == "LONG" else -1.0
    vals = [directional * rs30, directional * rs1h, directional * rs4h]
    if not all(np.isfinite(v) for v in vals):
        return 0.0, 0

    positive = sum(v > 0 for v in vals)
    bonus = 0.0
    if positive == 3:
        bonus += 1.25
        # FIL-like profile: outperformance strengthens as the horizon expands.
        if vals[2] >= vals[1] >= vals[0] > 0:
            bonus += 0.75
        if vals[1] >= 0.50 and vals[2] >= 1.00:
            bonus += 0.75
    elif positive == 2 and vals[1] > 0 and vals[2] > 0:
        bonus += 0.50

    return clamp(bonus, 0.0, 2.75), positive


def strength_adjustment(rs30, rs1h, rs4h, btc30, btc1h, btc4h, side):
    directional = 1.0 if side == "LONG" else -1.0
    weighted = (
        0.25 * clamp(directional * rs30 / 0.75, -1.5, 1.5)
        + 0.35 * clamp(directional * rs1h / 1.25, -1.5, 1.5)
        + 0.40 * clamp(directional * rs4h / 2.00, -1.5, 1.5)
    )
    adjustment = weighted * 2.0

    if side == "LONG":
        if np.isfinite(btc30) and btc30 < -0.20 and np.isfinite(rs30) and rs30 > 0: adjustment += 0.75
        if np.isfinite(btc1h) and btc1h < -0.35 and np.isfinite(rs1h) and rs1h > 0: adjustment += 1.00
        if np.isfinite(btc4h) and btc4h < -0.60 and np.isfinite(rs4h) and rs4h > 0: adjustment += 0.75
    else:
        if np.isfinite(btc30) and btc30 > 0.20 and np.isfinite(rs30) and rs30 < 0: adjustment += 0.75
        if np.isfinite(btc1h) and btc1h > 0.35 and np.isfinite(rs1h) and rs1h < 0: adjustment += 1.00
        if np.isfinite(btc4h) and btc4h > 0.60 and np.isfinite(rs4h) and rs4h < 0: adjustment += 0.75

    persistence_bonus, positive_horizons = persistent_strength_bonus(rs30, rs1h, rs4h, side)
    adjustment += persistence_bonus
    # Larger cap allows persistent multi-horizon leaders to outrank merely well-located coins,
    # while entry eligibility remains independently protected by live RR >= 1.5.
    return clamp(adjustment, -4.0, 6.0), persistence_bonus, positive_horizons


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="scan_results.csv")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()
    df = pd.read_csv(args.input, encoding="utf-8-sig")
    if df.empty: raise SystemExit("No scan results to rerank")

    btc30 = period_return("BTCUSDT", "5", 6)
    btc1h = period_return("BTCUSDT", "15", 4)
    btc4h = period_return("BTCUSDT", "60", 4)
    print("BTC reference returns:", f"30m={btc30:.3f}%", f"1h={btc1h:.3f}%", f"4h={btc4h:.3f}%")

    cols = {k: [] for k in ["rs30","rs1h","rs4h","ladj","sadj","lpersist","spersist","lhorizons","shorizons"]}
    for idx, row in df.iterrows():
        symbol = str(row.get("symbol", "")).upper().strip()
        try:
            r30 = period_return(symbol, "5", 6); r1h = period_return(symbol, "15", 4); r4h = period_return(symbol, "60", 4)
            rs30, rs1h, rs4h = r30-btc30, r1h-btc1h, r4h-btc4h
        except Exception as exc:
            print(f"[WARN] relative strength unavailable for {symbol}: {exc}")
            rs30 = rs1h = rs4h = np.nan
        ladj, lpersist, lh = strength_adjustment(rs30,rs1h,rs4h,btc30,btc1h,btc4h,"LONG")
        sadj, spersist, sh = strength_adjustment(rs30,rs1h,rs4h,btc30,btc1h,btc4h,"SHORT")
        for k,v in [("rs30",rs30),("rs1h",rs1h),("rs4h",rs4h),("ladj",ladj),("sadj",sadj),("lpersist",lpersist),("spersist",spersist),("lhorizons",lh),("shorizons",sh)]: cols[k].append(v)
        print(f"[{idx+1}/{len(df)}] {symbol} RS30={rs30:.2f} RS1H={rs1h:.2f} RS4H={rs4h:.2f} Ladj={ladj:.2f} Lpersist={lpersist:.2f}")
        time.sleep(0.03)

    df["btc_return_30m_pct"] = btc30; df["btc_return_1h_pct"] = btc1h; df["btc_return_4h_pct"] = btc4h
    df["rs_vs_btc_30m_pct"] = cols["rs30"]; df["rs_vs_btc_1h_pct"] = cols["rs1h"]; df["rs_vs_btc_4h_pct"] = cols["rs4h"]
    df["long_relative_strength_adj"] = cols["ladj"]; df["short_relative_strength_adj"] = cols["sadj"]
    df["long_rs_persistence_bonus"] = cols["lpersist"]; df["short_rs_persistence_bonus"] = cols["spersist"]
    df["long_rs_positive_horizons"] = cols["lhorizons"]; df["short_rs_positive_horizons"] = cols["shorizons"]
    df["long_score_base"] = pd.to_numeric(df["long_score"], errors="coerce"); df["short_score_base"] = pd.to_numeric(df["short_score"], errors="coerce")
    df["long_score"] = df["long_score_base"] + df["long_relative_strength_adj"]; df["short_score"] = df["short_score_base"] + df["short_relative_strength_adj"]
    df.to_csv(args.input, index=False, encoding="utf-8-sig")

    long_df = df.sort_values(["long_score","long_rr"], ascending=False, na_position="last").head(args.top)
    short_df = df.sort_values(["short_score","short_rr"], ascending=False, na_position="last").head(args.top)
    long_file = args.input.replace(".csv","_long.csv"); short_file = args.input.replace(".csv","_short.csv")
    long_df.to_csv(long_file,index=False,encoding="utf-8-sig"); short_df.to_csv(short_file,index=False,encoding="utf-8-sig")

    show_long = ["symbol","long_score_base","long_relative_strength_adj","long_rs_persistence_bonus","long_rs_positive_horizons","long_score","long_rr","rs_vs_btc_30m_pct","rs_vs_btc_1h_pct","rs_vs_btc_4h_pct"]
    show_short = ["symbol","short_score_base","short_relative_strength_adj","short_rs_persistence_bonus","short_rs_positive_horizons","short_score","short_rr","rs_vs_btc_30m_pct","rs_vs_btc_1h_pct","rs_vs_btc_4h_pct"]
    print("\nTOP LONG after BTC-relative strength adjustment"); print(long_df[show_long].to_string(index=False))
    print("\nTOP SHORT after BTC-relative strength adjustment"); print(short_df[show_short].to_string(index=False))

if __name__ == "__main__": main()
