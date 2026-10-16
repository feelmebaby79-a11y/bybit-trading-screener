"""Offline smoke tests for the deployed Bybit SMC screener."""
import ast
import pathlib
import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent
source = (ROOT / "bybit_htf_ltf_screener.py").read_text()
wrapper = (ROOT / "run_structural_screener.py").read_text()
ast.parse(source)
ast.parse(wrapper)
assert 'SMC_SWING_LENGTH = 50' in source
assert 'SMC_INTERNAL_LENGTH = 5' in source
assert 'def smc_snapshot(' in source
assert 'def smc_equal_levels(' in source
assert 'def smc_order_block(' in source

# Import without running the CLI.
scope = {"__name__": "smc_test", "__file__": str(ROOT / "bybit_htf_ltf_screener.py")}
exec(compile(source, "bybit_htf_ltf_screener.py", "exec"), scope)

n = 240
rng = np.random.default_rng(20261016)
close = 100 + np.cumsum(rng.normal(0, 1, n))
open_ = close - rng.normal(0, 0.3, n)
data = pd.DataFrame({
    "open": open_, "high": np.maximum(open_, close) + 0.6,
    "low": np.minimum(open_, close) - 0.6, "close": close,
    "volume": np.full(n, 1000.0),
})
snap = scope["smc_snapshot"](data)
assert snap["trend"] in ("bullish", "bearish", "range")
assert snap["zone"] in ("discount", "premium", "equilibrium", "neutral", "unknown")
assert "ob_side" in snap and "eqh" in snap and "fvg_side" in snap
assert scope["feat"](data)["trend"] == snap["trend"]
assert isinstance(scope["trigger"](data, "LONG")["score"], int)
assert len(scope["rr_hint"](data, "LONG")) == 4

# Wrapper must delegate to the canonical scanner without rewriting its code.
assert 'runpy.run_path(str(SOURCE), run_name="__main__")' in wrapper
assert 'patched = code' not in wrapper
assert 'old_15m' not in wrapper
assert '1D_last_bos_side' in source
assert '1D_last_choch_side' in source
print("SMC smoke tests passed")
