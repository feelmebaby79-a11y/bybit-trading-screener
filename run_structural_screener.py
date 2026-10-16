#!/usr/bin/env python3
"""Run screener with protected-level market structure and expose 1D structure diagnostics."""
from pathlib import Path

SOURCE = Path(__file__).with_name("bybit_htf_ltf_screener.py")
code = SOURCE.read_text(encoding="utf-8")
# The base scanner owns LuxAlgo-style SMC structure logic.
# Preserve its implementation; only enrich its output with metadata.
start = code.index("def structure(d):")
end = code.index("# =========================================================\n# LuxAlgo trailing swing Premium / Discount zones", start)
replacement = '''def structure(d):
    bias, events, high, low = smc_structure(d, SMC_SWING_LENGTH)
    trend = "bullish" if bias == 1 else "bearish" if bias == -1 else "range"
    bos = next((e for e in reversed(events) if e[2] == "BOS"), None)
    choch = next((e for e in reversed(events) if e[2] == "CHoCH"), None)
    d.attrs["structure_meta"] = {
        "protected_low": low if low is not None else np.nan,
        "protected_high": high if high is not None else np.nan,
        "last_bos_side": bos[1] if bos else None,
        "last_bos_level": bos[3] if bos else np.nan,
        "last_choch_side": choch[1] if choch else None,
        "last_choch_level": choch[3] if choch else np.nan,
    }
    return trend, bias * 2, high if high is not None else np.nan, low if low is not None else np.nan

'''
patched = code[:start] + replacement + code[end:]

# Full-universe guarantee: the base screener historically truncated active
# Bybit USDT perpetuals to turnover TOP 100.  Keep turnover sorting for
# deterministic processing, but never discard lower-turnover active symbols.
# This guarantees current positions such as IOTA are analyzed even when they
# fall outside the recommendation liquidity shortlist.
old_universe_cap = '    return out.head(cfg.top_turnover)'
if old_universe_cap in patched:
    patched = patched.replace(old_universe_cap, '    return out', 1)

# Add structural metadata to feature dictionaries without changing scoring fields.
old_feat = '''    tr, sc, _, _ = structure(d)\n\n    zone, pos, hi, lo, eq = location(d, reference_price=reference_price)'''
new_feat = '''    tr, sc, _, _ = structure(d)\n\n    structure_meta = d.attrs.get("structure_meta", {})\n\n    zone, pos, hi, lo, eq = location(d)'''
if old_feat not in patched: raise RuntimeError("Feature patch target missing; abort scan")
patched = patched.replace(old_feat, new_feat, 1)
old_eq = '''        "eq": eq,\n\n    }'''
new_eq = '''        "eq": eq,\n\n        "protected_low": structure_meta.get("protected_low", np.nan),\n\n        "protected_high": structure_meta.get("protected_high", np.nan),\n\n        "last_bos_side": structure_meta.get("last_bos_side"),\n\n        "last_bos_level": structure_meta.get("last_bos_level", np.nan),\n\n        "last_choch_side": structure_meta.get("last_choch_side"),\n\n        "last_choch_level": structure_meta.get("last_choch_level", np.nan),\n\n    }'''
if old_eq not in patched: raise RuntimeError("Structure metadata patch target missing; abort scan")
patched = patched.replace(old_eq, new_eq, 1)

# Persist 1D external-structure diagnostics in every scan CSV/JSON row.
old_15m = '''        "15m": F["15m"]["trend"],\n\n        "4H_location": F["4H"]["zone"],'''
new_15m = '''        "15m": F["15m"]["trend"],\n\n        "1D_protected_low": F["1D"]["protected_low"],\n\n        "1D_protected_high": F["1D"]["protected_high"],\n\n        "1D_last_bos_side": F["1D"]["last_bos_side"],\n\n        "1D_last_bos_level": F["1D"]["last_bos_level"],\n\n        "1D_last_choch_side": F["1D"]["last_choch_side"],\n\n        "1D_last_choch_level": F["1D"]["last_choch_level"],\n\n        "4H_location": F["4H"]["zone"],'''
patched = patched.replace(old_15m, new_15m, 1)

exec(compile(patched, str(SOURCE), "exec"), {"__name__": "__main__", "__file__": str(SOURCE)})