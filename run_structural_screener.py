#!/usr/bin/env python3
"""Run screener with protected-level market structure and expose 1D structure diagnostics."""
from pathlib import Path

SOURCE = Path(__file__).with_name("bybit_htf_ltf_screener.py")
code = SOURCE.read_text(encoding="utf-8")
start = code.index("def structure(d):")
end_marker = "# =========================================================\n\n# Premium / Discount"
end = code.index(end_marker, start)

replacement = r'''def structure(d):
    x = swings(d)
    hs, ls = recent(x, 3)
    last_h = hs[-1] if hs else np.nan
    last_l = ls[-1] if ls else np.nan

    meta = {
        "protected_low": np.nan,
        "protected_high": np.nan,
        "last_bos_side": None,
        "last_bos_level": np.nan,
        "last_choch_side": None,
        "last_choch_level": np.nan,
    }
    if len(x) < 5:
        d.attrs["structure_meta"] = meta
        return "range", 0, last_h, last_l

    confirm_highs, confirm_lows = {}, {}
    for idx in x.index[x.swing_high]:
        ci = int(idx) + 2
        if ci < len(x):
            confirm_highs.setdefault(ci, []).append((int(idx), float(x.high.iloc[idx])))
    for idx in x.index[x.swing_low]:
        ci = int(idx) + 2
        if ci < len(x):
            confirm_lows.setdefault(ci, []).append((int(idx), float(x.low.iloc[idx])))

    trend = "range"
    last_high = last_low = None
    protected_low = protected_high = np.nan
    broken_highs, broken_lows = set(), set()
    last_bos_side, last_bos_level = None, np.nan
    last_choch_side, last_choch_level = None, np.nan

    for i in range(len(x)):
        for pivot in confirm_highs.get(i, []): last_high = pivot
        for pivot in confirm_lows.get(i, []): last_low = pivot
        c = float(x.close.iloc[i])

        if trend == "bullish":
            if np.isfinite(protected_low) and c < protected_low:
                last_choch_side, last_choch_level = "bearish", float(protected_low)
                trend = "bearish"
                protected_high = last_high[1] if last_high is not None else np.nan
                if last_low is not None: broken_lows.add(last_low[0])
                continue
            if last_high is not None and last_high[0] not in broken_highs and c > last_high[1]:
                broken_highs.add(last_high[0])
                last_bos_side, last_bos_level = "bullish", float(last_high[1])
                if last_low is not None: protected_low = last_low[1]
            continue

        if trend == "bearish":
            if np.isfinite(protected_high) and c > protected_high:
                last_choch_side, last_choch_level = "bullish", float(protected_high)
                trend = "bullish"
                protected_low = last_low[1] if last_low is not None else np.nan
                if last_high is not None: broken_highs.add(last_high[0])
                continue
            if last_low is not None and last_low[0] not in broken_lows and c < last_low[1]:
                broken_lows.add(last_low[0])
                last_bos_side, last_bos_level = "bearish", float(last_low[1])
                if last_high is not None: protected_high = last_high[1]
            continue

        bullish_break = last_high is not None and last_high[0] not in broken_highs and c > last_high[1]
        bearish_break = last_low is not None and last_low[0] not in broken_lows and c < last_low[1]
        if bullish_break:
            trend = "bullish"
            broken_highs.add(last_high[0])
            last_bos_side, last_bos_level = "bullish", float(last_high[1])
            protected_low = last_low[1] if last_low is not None else np.nan
        elif bearish_break:
            trend = "bearish"
            broken_lows.add(last_low[0])
            last_bos_side, last_bos_level = "bearish", float(last_low[1])
            protected_high = last_high[1] if last_high is not None else np.nan

    sc = 0
    if trend == "bullish":
        sc = 2
        if hs and float(x.close.iloc[-1]) > hs[-1]: sc += 1
    elif trend == "bearish":
        sc = -2
        if ls and float(x.close.iloc[-1]) < ls[-1]: sc -= 1

    meta = {
        "protected_low": float(protected_low) if np.isfinite(protected_low) else np.nan,
        "protected_high": float(protected_high) if np.isfinite(protected_high) else np.nan,
        "last_bos_side": last_bos_side,
        "last_bos_level": float(last_bos_level) if np.isfinite(last_bos_level) else np.nan,
        "last_choch_side": last_choch_side,
        "last_choch_level": float(last_choch_level) if np.isfinite(last_choch_level) else np.nan,
    }
    d.attrs["structure_meta"] = meta
    return trend, sc, last_h, last_l

'''
patched = code[:start] + replacement + code[end:]

# Full-universe guarantee: the base screener historically truncated active
# Bybit USDT perpetuals to turnover TOP 100.  Keep turnover sorting for
# deterministic processing, but never discard lower-turnover active symbols.
# This guarantees current positions such as IOTA are analyzed even when they
# fall outside the recommendation liquidity shortlist.
old_universe_cap = '    return out.head(cfg.top_turnover)'
if old_universe_cap not in patched:
    raise RuntimeError("Universe cap patch target not found")
patched = patched.replace(old_universe_cap, '    return out', 1)

# Add structural metadata to feature dictionaries without changing scoring fields.
old_feat = '''    tr, sc, _, _ = structure(d)\n\n    zone, pos, hi, lo, eq = location(d)'''
new_feat = '''    tr, sc, _, _ = structure(d)\n\n    structure_meta = d.attrs.get("structure_meta", {})\n\n    zone, pos, hi, lo, eq = location(d)'''
patched = patched.replace(old_feat, new_feat, 1)
old_eq = '''        "eq": eq,\n\n    }'''
new_eq = '''        "eq": eq,\n\n        "protected_low": structure_meta.get("protected_low", np.nan),\n\n        "protected_high": structure_meta.get("protected_high", np.nan),\n\n        "last_bos_side": structure_meta.get("last_bos_side"),\n\n        "last_bos_level": structure_meta.get("last_bos_level", np.nan),\n\n        "last_choch_side": structure_meta.get("last_choch_side"),\n\n        "last_choch_level": structure_meta.get("last_choch_level", np.nan),\n\n    }'''
patched = patched.replace(old_eq, new_eq, 1)

# Persist 1D external-structure diagnostics in every scan CSV/JSON row.
old_15m = '''        "15m": F["15m"]["trend"],\n\n        "4H_location": F["4H"]["zone"],'''
new_15m = '''        "15m": F["15m"]["trend"],\n\n        "1D_protected_low": F["1D"]["protected_low"],\n\n        "1D_protected_high": F["1D"]["protected_high"],\n\n        "1D_last_bos_side": F["1D"]["last_bos_side"],\n\n        "1D_last_bos_level": F["1D"]["last_bos_level"],\n\n        "1D_last_choch_side": F["1D"]["last_choch_side"],\n\n        "1D_last_choch_level": F["1D"]["last_choch_level"],\n\n        "4H_location": F["4H"]["zone"],'''
patched = patched.replace(old_15m, new_15m, 1)

exec(compile(patched, str(SOURCE), "exec"), {"__name__": "__main__", "__file__": str(SOURCE)})