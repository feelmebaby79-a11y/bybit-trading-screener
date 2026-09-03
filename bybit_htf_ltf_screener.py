#!/usr/bin/env python3

# -*- coding: utf-8 -*-

import argparse

import math

import time

from dataclasses import dataclass

import numpy as np

import pandas as pd

import requests

# =========================================================

# Cloudflare Worker -> Bybit V5

# =========================================================

BASE = "https://bybit-trading-screener.feelmebaby79.workers.dev"

CATEGORY = "linear"

S = requests.Session()

S.headers.update({

    "User-Agent": "bybit-htf-ltf-screener/2.0"

})

TFS = {

    "1D": "D",

    "4H": "240",

    "1H": "60",

    "15m": "15",

    "5m": "5",

}

LIMITS = {

    "1D": 220,

    "4H": 260,

    "1H": 300,

    "15m": 320,

    "5m": 400,

}

@dataclass

class Cfg:

    min_turnover: float = 5_000_000

    max_symbols: int = 160

    sleep: float = 0.08

    timeout: int = 20

    min_rr: float = 1.8

# =========================================================

# API

# =========================================================

def api(path, params, cfg, retries=4):
    """
    Requests public Bybit market data through our Cloudflare Worker.
    """

    err = None
    url = BASE + path

    for i in range(retries):
        try:
            r = S.get(
                url,
                params=params,
                timeout=cfg.timeout,
            )

            if not r.ok:
                print("\n========== HTTP DEBUG ==========")
                print("STATUS :", r.status_code)
                print("URL    :", r.url)
                print("SERVER :", r.headers.get("server"))
                print("CF-RAY :", r.headers.get("cf-ray"))
                print("TYPE   :", r.headers.get("content-type"))
                print("BODY   :", r.text[:2000])
                print("================================\n")

            r.raise_for_status()

            j = r.json()

            if j.get("retCode") == 0:
                return j

            err = RuntimeError(
                j.get("retMsg", "Unknown Bybit API error")
            )

        except Exception as e:
            err = e

        time.sleep(0.5 * (2 ** i))

    raise RuntimeError(f"{path}: {err}")

# =========================================================

# Universe

# =========================================================

def universe(cfg):

    all_ = []

    cur = None

    while True:

        p = {

            "category": CATEGORY,

            "limit": 1000,

        }

        if cur:

            p["cursor"] = cur

        j = api(

            "/v5/market/instruments-info",

            p,

            cfg,

        )["result"]

        all_ += j["list"]

        cur = j.get("nextPageCursor")

        if not cur:

            break

    inst = pd.DataFrame(all_)

    inst = inst[

        (inst.quoteCoin == "USDT")

        & (inst.status == "Trading")

        & (

            inst.contractType

            .str

            .contains("Perpetual", na=False)

        )

    ]

    tick = pd.DataFrame(

        api(

            "/v5/market/tickers",

            {

                "category": CATEGORY

            },

            cfg,

        )["result"]["list"]

    )

    for c in [

        "turnover24h",

        "lastPrice",

        "price24hPcnt",

    ]:

        tick[c] = pd.to_numeric(

            tick[c],

            errors="coerce",

        )

    out = inst[["symbol"]].merge(

        tick[

            [

                "symbol",

                "turnover24h",

                "lastPrice",

                "price24hPcnt",

            ]

        ],

        on="symbol",

        how="left",

    )

    out = out[

        out.turnover24h.fillna(0)

        >= cfg.min_turnover

    ]

    out = out.sort_values(

        "turnover24h",

        ascending=False,

    )

    return out.head(cfg.max_symbols)

# =========================================================

# Klines

# =========================================================

def klines(sym, tf, cfg):

    rows = api(

        "/v5/market/kline",

        {

            "category": CATEGORY,

            "symbol": sym,

            "interval": TFS[tf],

            "limit": LIMITS[tf],

        },

        cfg,

    )["result"]["list"]

    if not rows:

        return pd.DataFrame()

    d = pd.DataFrame(

        rows,

        columns=[

            "ms",

            "open",

            "high",

            "low",

            "close",

            "volume",

            "turnover",

        ],

    )

    for c in [

        "open",

        "high",

        "low",

        "close",

        "volume",

        "turnover",

    ]:

        d[c] = pd.to_numeric(

            d[c],

            errors="coerce",

        )

    d["ms"] = pd.to_numeric(

        d["ms"],

        errors="coerce",

    )

    d["time"] = pd.to_datetime(

        d.ms,

        unit="ms",

        utc=True,

    )

    return (

        d

        .sort_values("time")

        .dropna()

        .reset_index(drop=True)

    )

# =========================================================

# Indicators

# =========================================================

def rsi(s, n=14):

    x = s.diff()

    g = x.clip(lower=0)

    l = -x.clip(upper=0)

    ag = g.ewm(

        alpha=1 / n,

        adjust=False,

        min_periods=n,

    ).mean()

    al = l.ewm(

        alpha=1 / n,

        adjust=False,

        min_periods=n,

    ).mean()

    return 100 - 100 / (

        1 + ag / al.replace(0, np.nan)

    )

def atr(d, n=14):

    p = d.close.shift(1)

    tr = pd.concat(

        [

            d.high - d.low,

            (d.high - p).abs(),

            (d.low - p).abs(),

        ],

        axis=1,

    ).max(axis=1)

    return tr.ewm(

        alpha=1 / n,

        adjust=False,

        min_periods=n,

    ).mean()

# =========================================================

# Market Structure

# =========================================================

def swings(d, left=2, right=2):

    x = d.copy()

    sh = np.zeros(

        len(x),

        dtype=bool,

    )

    sl = np.zeros(

        len(x),

        dtype=bool,

    )

    for i in range(

        left,

        len(x) - right,

    ):

        wh = x.high.iloc[

            i-left:i+right+1

        ]

        wl = x.low.iloc[

            i-left:i+right+1

        ]

        sh[i] = (

            x.high.iloc[i] == wh.max()

            and

            (

                wh == x.high.iloc[i]

            ).sum() == 1

        )

        sl[i] = (

            x.low.iloc[i] == wl.min()

            and

            (

                wl == x.low.iloc[i]

            ).sum() == 1

        )

    x["swing_high"] = sh

    x["swing_low"] = sl

    return x

def recent(x, n=3):

    hs = [

        float(v)

        for v in

        x.loc[

            x.swing_high,

            "high",

        ].tail(n)

    ]

    ls = [

        float(v)

        for v in

        x.loc[

            x.swing_low,

            "low",

        ].tail(n)

    ]

    return hs, ls

def structure(d):

    x = swings(d)

    hs, ls = recent(

        x,

        3,

    )

    tr = "range"

    sc = 0

    c = float(

        x.close.iloc[-1]

    )

    if (

        len(hs) >= 2

        and

        len(ls) >= 2

    ):

        if (

            hs[-1] > hs[-2]

            and

            ls[-1] > ls[-2]

        ):

            tr = "bullish"

            sc = 2

        elif (

            hs[-1] < hs[-2]

            and

            ls[-1] < ls[-2]

        ):

            tr = "bearish"

            sc = -2

    if hs and c > hs[-1]:

        tr = "bullish"

        sc += 1

    if ls and c < ls[-1]:

        tr = "bearish"

        sc -= 1

    return (

        tr,

        sc,

        hs[-1] if hs else np.nan,

        ls[-1] if ls else np.nan,

    )

# =========================================================

# Premium / Discount

# =========================================================

def location(d, n=60):

    z = d.tail(

        min(

            n,

            len(d),

        )

    )

    hi = float(

        z.high.max()

    )

    lo = float(

        z.low.min()

    )

    c = float(

        z.close.iloc[-1]

    )

    pos = (

        (c - lo) / (hi - lo)

        if hi > lo

        else 0.5

    )

    if pos <= 0.38:

        zone = "discount"

    elif pos >= 0.62:

        zone = "premium"

    else:

        zone = "equilibrium"

    return (

        zone,

        pos,

        hi,

        lo,

        (hi + lo) / 2,

    )

# =========================================================

# Strict ICT FVG

# =========================================================

def strict_fvg(d):

    for i in range(

        len(d) - 1,

        1,

        -1,

    ):

        candle1 = d.iloc[i - 2]

        candle3 = d.iloc[i]

        # bullish FVG

        if candle3.low > candle1.high:

            return (

                "bullish",

                float(candle1.high),

                float(candle3.low),

            )

        # bearish FVG

        if candle3.high < candle1.low:

            return (

                "bearish",

                float(candle3.high),

                float(candle1.low),

            )

    return (

        None,

        np.nan,

        np.nan,

    )

# =========================================================

# Liquidity Sweep

# =========================================================

def sweep(d, side, n=25):

    if len(d) < n + 2:

        return False

    previous = d.iloc[

        -n-1:-1

    ]

    candle = d.iloc[-1]

    if side == "LONG":

        liquidity = float(

            previous.low.min()

        )

        return (

            candle.low < liquidity

            and

            candle.close > liquidity

        )

    liquidity = float(

        previous.high.max()

    )

    return (

        candle.high > liquidity

        and

        candle.close < liquidity

    )

# =========================================================

# Displacement

# =========================================================

def displacement(

    d,

    side,

    multiplier=0.9,

):

    a = atr(d).iloc[-1]

    candle = d.iloc[-1]

    if (

        not np.isfinite(a)

        or

        a <= 0

    ):

        return False

    body = abs(

        candle.close

        - candle.open

    )

    if side == "LONG":

        direction = (

            candle.close

            > candle.open

        )

    else:

        direction = (

            candle.close

            < candle.open

        )

    return (

        direction

        and

        body >= multiplier * a

    )

# =========================================================

# LTF trigger

# =========================================================

def trigger(d, side):

    x = swings(d)

    hs, ls = recent(

        x,

        2,

    )

    c = float(

        x.close.iloc[-1]

    )

    sw = sweep(

        x,

        side,

    )

    disp = displacement(

        x,

        side,

    )

    ft, fl, fh = strict_fvg(x)

    if side == "LONG":

        mss = (

            c > hs[-1]

            if hs

            else False

        )

        fvg_ok = (

            ft == "bullish"

        )

    else:

        mss = (

            c < ls[-1]

            if ls

            else False

        )

        fvg_ok = (

            ft == "bearish"

        )

    score = (

        2 * int(sw)

        + 2 * int(mss)

        + 2 * int(disp)

        + int(fvg_ok)

    )

    return {

        "sweep": sw,

        "mss": mss,

        "disp": disp,

        "fvg": ft,

        "score": score,

    }

# =========================================================

# Rough RR

# =========================================================

def rr_hint(d, side):

    x = swings(d)

    hs, ls = recent(

        x,

        3,

    )

    entry = float(

        x.close.iloc[-1]

    )

    if side == "LONG":

        stop = (

            ls[-1]

            if ls

            else

            float(

                x.low.tail(20).min()

            )

        )

        target = (

            hs[-1]

            if (

                hs

                and

                hs[-1] > entry

            )

            else

            float(

                x.high.tail(80).max()

            )

        )

        risk = entry - stop

        reward = target - entry

    else:

        stop = (

            hs[-1]

            if hs

            else

            float(

                x.high.tail(20).max()

            )

        )

        target = (

            ls[-1]

            if (

                ls

                and

                ls[-1] < entry

            )

            else

            float(

                x.low.tail(80).min()

            )

        )

        risk = stop - entry

        reward = entry - target

    rr = (

        reward / risk

        if (

            risk > 0

            and

            reward > 0

        )

        else np.nan

    )

    return (

        entry,

        stop,

        target,

        rr,

    )

# =========================================================

# Feature extraction

# =========================================================

def feat(d):

    tr, sc, _, _ = structure(d)

    zone, pos, hi, lo, eq = location(d)

    rv = rsi(

        d.close

    ).iloc[-1]

    return {

        "trend": tr,

        "score": sc,

        "zone": zone,

        "pos": pos,

        "rsi": (

            float(rv)

            if np.isfinite(rv)

            else np.nan

        ),

        "hi": hi,

        "lo": lo,

        "eq": eq,

    }

# =========================================================

# HTF weighting

# =========================================================

def side_score(

    side,

    F,

    turnover,

    p24,

):

    want = (

        "bullish"

        if side == "LONG"

        else "bearish"

    )

    opposite = (

        "bearish"

        if side == "LONG"

        else "bullish"

    )

    score = 0

    reasons = []

    weights = {

        "1D": 5.5,

        "4H": 4.0,

        "1H": 2.5,

        "15m": 1.0,

    }

    for tf, weight in weights.items():

        if F[tf]["trend"] == want:

            score += weight

            reasons.append(

                f"{tf} {want}"

            )

        elif F[tf]["trend"] == opposite:

            score -= weight * 1.35

            reasons.append(

                f"{tf} reverse"

            )

    # HTF location

    if side == "LONG":

        if F["4H"]["zone"] == "discount":

            score += 2.0

            reasons.append(

                "4H discount"

            )

        if F["1H"]["zone"] == "discount":

            score += 1.0

        if F["4H"]["zone"] == "premium":

            score -= 1.8

    else:

        if F["4H"]["zone"] == "premium":

            score += 2.0

            reasons.append(

                "4H premium"

            )

        if F["1H"]["zone"] == "premium":

            score += 1.0

        if F["4H"]["zone"] == "discount":

            score -= 1.8

    # avoid chasing extreme RSI

    r15 = F["15m"]["rsi"]

    if np.isfinite(r15):

        if (

            side == "LONG"

            and

            r15 > 76

        ):

            score -= 1.3

        if (

            side == "SHORT"

            and

            r15 < 24

        ):

            score -= 1.3

    # 24h performance only minor factor

    if np.isfinite(p24):

        directional = (

            p24 * 100

            if side == "LONG"

            else

            -p24 * 100

        )

        score += float(

            np.clip(

                directional / 5,

                -1,

                1,

            )

        )

    # liquidity

    if turnover > 0:

        score += min(

            1.2,

            max(

                0,

                (

                    math.log10(turnover)

                    - 6.5

                ) * 0.4,

            ),

        )

    return score, reasons

# =========================================================

# Scan one symbol

# =========================================================

def scan_symbol(

    sym,

    turnover,

    p24,

    cfg,

):

    D = {}

    for tf in TFS:

        D[tf] = klines(

            sym,

            tf,

            cfg,

        )

        time.sleep(

            cfg.sleep

        )

        if len(D[tf]) < 60:

            return None

    F = {

        tf: feat(D[tf])

        for tf in TFS

    }

    out = {

        "symbol": sym,

        "last": float(

            D["5m"].close.iloc[-1]

        ),

        "turnover24h": turnover,

        "price24h_pct": (

            p24 * 100

            if np.isfinite(p24)

            else np.nan

        ),

        "1D": F["1D"]["trend"],

        "4H": F["4H"]["trend"],

        "1H": F["1H"]["trend"],

        "15m": F["15m"]["trend"],

        "4H_location": F["4H"]["zone"],

        "1H_location": F["1H"]["zone"],

        "15m_rsi": F["15m"]["rsi"],

    }

    for side in [

        "LONG",

        "SHORT",

    ]:

        base, reasons = side_score(

            side,

            F,

            turnover,

            p24,

        )

        t15 = trigger(

            D["15m"],

            side,

        )

        t5 = trigger(

            D["5m"],

            side,

        )

        (

            entry,

            stop,

            target,

            rr,

        ) = rr_hint(

            D["15m"],

            side,

        )

        score = (

            base

            + 0.45 * t15["score"]

            + 0.55 * t5["score"]

        )

        if np.isfinite(rr):

            score += min(

                2,

                max(

                    -1,

                    rr - 1,

                ),

            )

        # Mandatory HTF alignment

        if side == "LONG":

            htf_aligned = (

                F["1D"]["trend"]

                == "bullish"

                and

                F["4H"]["trend"]

                != "bearish"

            )

        else:

            htf_aligned = (

                F["1D"]["trend"]

                == "bearish"

                and

                F["4H"]["trend"]

                != "bullish"

            )

        trigger_ok = (

            t15["mss"]

            and

            (

                t15["disp"]

                or

                t5["disp"]

            )

        )

        if (

            score >= 13

            and

            trigger_ok

            and

            htf_aligned

            and

            np.isfinite(rr)

            and

            rr >= cfg.min_rr

        ):

            grade = "A"

        elif (

            score >= 10

            and

            htf_aligned

        ):

            grade = "B+"

        elif score >= 7:

            grade = "B"

        else:

            grade = "C"

        k = side.lower()

        out.update({

            f"{k}_score": score,

            f"{k}_grade": grade,

            f"{k}_rr": rr,

            f"{k}_entry": entry,

            f"{k}_stop_hint": stop,

            f"{k}_target_hint": target,

            f"{k}_15m_trigger": t15["score"],

            f"{k}_5m_trigger": t5["score"],

            f"{k}_sweep15": t15["sweep"],

            f"{k}_mss15": t15["mss"],

            f"{k}_disp15": t15["disp"],

            f"{k}_fvg5": t5["fvg"],

            f"{k}_htf_aligned": htf_aligned,

            f"{k}_reasons": " | ".join(reasons),

        })

    return out

# =========================================================

# Main

# =========================================================

def main():

    ap = argparse.ArgumentParser()

    ap.add_argument(

        "--min-turnover",

        type=float,

        default=5_000_000,

    )

    ap.add_argument(

        "--max-symbols",

        type=int,

        default=160,

    )

    ap.add_argument(

        "--top",

        type=int,

        default=15,

    )

    ap.add_argument(

        "--output",

        default="scan_results.csv",

    )

    a = ap.parse_args()

    cfg = Cfg(

        min_turnover=a.min_turnover,

        max_symbols=a.max_symbols,

    )

    U = universe(cfg)

    print(

        f"Universe: {len(U)}"

    )

    rows = []

    for i, r in (

        U

        .reset_index(drop=True)

        .iterrows()

    ):

        try:

            turnover = (

                float(r.turnover24h)

                if pd.notna(r.turnover24h)

                else 0

            )

            p24 = (

                float(r.price24hPcnt)

                if pd.notna(r.price24hPcnt)

                else np.nan

            )

            z = scan_symbol(

                r.symbol,

                turnover,

                p24,

                cfg,

            )

            if z:

                rows.append(z)

                print(

                    f"[{i+1}/{len(U)}] "

                    f"{r.symbol} "

                    f"L={z['long_score']:.1f} "

                    f"S={z['short_score']:.1f}"

                )

        except Exception as e:

            print(

                "[WARN]",

                r.symbol,

                e,

            )

    if not rows:

        raise SystemExit(

            "No results"

        )

    df = pd.DataFrame(rows)

    df.to_csv(

        a.output,

        index=False,

        encoding="utf-8-sig",

    )

    L = (

        df

        .sort_values(

            [

                "long_score",

                "long_rr",

            ],

            ascending=False,

        )

        .head(a.top)

    )

    S_ = (

        df

        .sort_values(

            [

                "short_score",

                "short_rr",

            ],

            ascending=False,

        )

        .head(a.top)

    )

    long_file = a.output.replace(

        ".csv",

        "_long.csv",

    )

    short_file = a.output.replace(

        ".csv",

        "_short.csv",

    )

    L.to_csv(

        long_file,

        index=False,

        encoding="utf-8-sig",

    )

    S_.to_csv(

        short_file,

        index=False,

        encoding="utf-8-sig",

    )

    print("\nTOP LONG")

    print(

        L[

            [

                "symbol",

                "long_grade",

                "long_score",

                "long_rr",

                "long_htf_aligned",

                "1D",

                "4H",

                "1H",

                "4H_location",

            ]

        ].to_string(

            index=False

        )

    )

    print("\nTOP SHORT")

    print(

        S_[

            [

                "symbol",

                "short_grade",

                "short_score",

                "short_rr",

                "short_htf_aligned",

                "1D",

                "4H",

                "1H",

                "4H_location",

            ]

        ].to_string(

            index=False

        )

    )

if __name__ == "__main__":

    main()
