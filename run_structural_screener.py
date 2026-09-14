#!/usr/bin/env python3
"""
Run the existing screener with structural market-direction logic.

Only the market-structure classifier is replaced. The existing scoring,
RR, liquidity sweep, displacement, FVG and ranking logic remain unchanged.

Direction rules:
- Swings still use the existing confirmed 2-left / 2-right fractals.
- A trend is established by a close breaking a confirmed swing level.
- In a bullish trend, minor LH/LL noise does NOT flip the HTF direction.
  Direction flips bearish only when the bullish protected low is closed below.
- In a bearish trend, direction flips bullish only when the bearish protected
  high is closed above.
- A continuation BOS ratchets the protected level to the latest confirmed
  opposite-side swing available before that BOS.
- The returned score scale remains compatible with the old structure():
  bullish +2, bearish -2, range 0, with the same +/-1 current-break bonus.
"""

from pathlib import Path

SOURCE = Path(__file__).with_name("bybit_htf_ltf_screener.py")
code = SOURCE.read_text(encoding="utf-8")

start = code.index("def structure(d):")
end_marker = "# =========================================================\n\n# Premium / Discount"
end = code.index(end_marker, start)

replacement = r'''def structure(d):

    x = swings(d)

    hs, ls = recent(
        x,
        3,
    )

    # Preserve the original function contract for downstream scoring.
    last_h = hs[-1] if hs else np.nan
    last_l = ls[-1] if ls else np.nan

    if len(x) < 5:
        return (
            "range",
            0,
            last_h,
            last_l,
        )

    # A 2-right fractal is only confirmed two candles after the pivot.
    # Store each structural pivot at its confirmation bar so historical
    # processing does not use future information.
    confirm_highs = {}
    confirm_lows = {}

    for idx in x.index[x.swing_high]:
        confirm_idx = int(idx) + 2
        if confirm_idx < len(x):
            confirm_highs.setdefault(confirm_idx, []).append(
                (int(idx), float(x.high.iloc[idx]))
            )

    for idx in x.index[x.swing_low]:
        confirm_idx = int(idx) + 2
        if confirm_idx < len(x):
            confirm_lows.setdefault(confirm_idx, []).append(
                (int(idx), float(x.low.iloc[idx]))
            )

    trend = "range"

    last_high = None
    last_low = None

    # Protected levels define the external structure. Minor internal swings
    # are allowed without reversing the HTF direction.
    protected_low = np.nan
    protected_high = np.nan

    broken_highs = set()
    broken_lows = set()

    for i in range(len(x)):

        for pivot in confirm_highs.get(i, []):
            last_high = pivot

        for pivot in confirm_lows.get(i, []):
            last_low = pivot

        c = float(x.close.iloc[i])

        # -------------------------------------------------------------
        # Existing bullish structure
        # -------------------------------------------------------------
        if trend == "bullish":

            # CHoCH / structural reversal only when the protected low breaks.
            if (
                np.isfinite(protected_low)
                and c < protected_low
            ):
                trend = "bearish"

                # On a bearish reversal, protect the most recent confirmed high.
                protected_high = (
                    last_high[1]
                    if last_high is not None
                    else np.nan
                )

                # The broken protected low must not repeatedly trigger.
                if last_low is not None:
                    broken_lows.add(last_low[0])

                continue

            # Bullish BOS. Ratchet the protected low to the latest confirmed
            # swing low that existed before this continuation break.
            if (
                last_high is not None
                and last_high[0] not in broken_highs
                and c > last_high[1]
            ):
                broken_highs.add(last_high[0])

                if last_low is not None:
                    protected_low = last_low[1]

            continue

        # -------------------------------------------------------------
        # Existing bearish structure
        # -------------------------------------------------------------
        if trend == "bearish":

            # CHoCH / structural reversal only when the protected high breaks.
            if (
                np.isfinite(protected_high)
                and c > protected_high
            ):
                trend = "bullish"

                # On a bullish reversal, protect the most recent confirmed low.
                protected_low = (
                    last_low[1]
                    if last_low is not None
                    else np.nan
                )

                if last_high is not None:
                    broken_highs.add(last_high[0])

                continue

            # Bearish BOS. Ratchet the protected high to the latest confirmed
            # swing high that existed before this continuation break.
            if (
                last_low is not None
                and last_low[0] not in broken_lows
                and c < last_low[1]
            ):
                broken_lows.add(last_low[0])

                if last_high is not None:
                    protected_high = last_high[1]

            continue

        # -------------------------------------------------------------
        # No established external structure yet.
        # First confirmed structural break establishes direction.
        # -------------------------------------------------------------
        bullish_break = (
            last_high is not None
            and last_high[0] not in broken_highs
            and c > last_high[1]
        )

        bearish_break = (
            last_low is not None
            and last_low[0] not in broken_lows
            and c < last_low[1]
        )

        if bullish_break:
            trend = "bullish"
            broken_highs.add(last_high[0])
            protected_low = (
                last_low[1]
                if last_low is not None
                else np.nan
            )

        elif bearish_break:
            trend = "bearish"
            broken_lows.add(last_low[0])
            protected_high = (
                last_high[1]
                if last_high is not None
                else np.nan
            )

    # Keep the OLD score scale exactly: structural direction is +/-2,
    # and current close beyond the latest swing adds the existing +/-1 bonus.
    if trend == "bullish":
        sc = 2
        if hs and float(x.close.iloc[-1]) > hs[-1]:
            sc += 1

    elif trend == "bearish":
        sc = -2
        if ls and float(x.close.iloc[-1]) < ls[-1]:
            sc -= 1

    else:
        sc = 0

    return (
        trend,
        sc,
        last_h,
        last_l,
    )

'''

patched = code[:start] + replacement + code[end:]

# Execute as the original CLI program so every downstream calculation,
# argument and output filename stays unchanged.
exec(compile(patched, str(SOURCE), "exec"), {"__name__": "__main__", "__file__": str(SOURCE)})
