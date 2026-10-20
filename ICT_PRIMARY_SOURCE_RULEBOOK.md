# ICT seven-model original-source implementation checklist

**Status: incomplete; do not report proxy backtests as Michael J. Huddleston original.**

## 1. ICT 2022 Mentorship
Primary: https://www.youtube.com/watch?v=nQfHZ2DEJ8c (Episode 3; Jan 2022).
Timestamped transcript: https://sozai.app/transcript/2022-ict-mentorship-episode-3/
- 15m: locate old lows / equal highs and their sell-side / buy-side liquidity.
- 2m: **after** liquidity raid, watch short-term swing high (bullish) / low (bearish) taken; **wick is sufficient**, no closing-price break required.
- On next candle close, determine if three-candle fair value gap exists.
- Example limit entry: opening price of contiguous down-close order-block candle series **within FVG**, plus applicable spread; bearish mirror requires source confirmation.
- Draw on opposing buy-side/sell-side liquidity for profit objectives.
- **Do not invent** 12-bar or 30-bar raid expiry, 1.5x displacement, 3-bar 15m pivot, automatic 50% FVG entry, or a fixed 24h exit. The lecture explicitly says a down-close candle is not automatically an order block.
- Open items before code: exact structural stop in all example variants; competing FVG selection; opposing liquidity hierarchy; time-of-day applicability; ambiguous entry if OB open not inside FVG.

## 2. Unicorn
Primary Michael J. Huddleston lecture with exact timestamps for overlap of breaker and FVG, entry, stop, TP, invalidation: **NOT YET VERIFIED**.

## 3. Breaker
Primary candidate: https://www.youtube.com/watch?v=UrS-mtGHtAA (ICT Mentorship Core Content Month 04, ICT Breaker Block). Exact lecture timestamps and executable rules: **NOT YET VERIFIED**.

## 4. OTE
Primary original lecture and standalone setup vs entry technique status: **NOT YET VERIFIED**. Do not treat arbitrary 70.5% fib touch as a complete strategy.

## 5. Silver Bullet
Primary candidate: https://www.youtube.com/watch?v=tRq1hyGGtl4 (ICT Silver Bullet Time Based Trading Model). Exact time zone / DST, entry, stop, TP, invalidation: **NOT YET VERIFIED**. Do not treat FVG alone as Silver Bullet.

## 6. Mitigation Block
Primary original lecture and rules distinguishing mitigation from breaker/OB: **NOT YET VERIFIED**.

## 7. Order Block Continuation
Original naming and standalone model status; primary lecture with full entry/stop/target: **NOT YET VERIFIED**.

## Execution protocol
BTCUSDT, ETHUSDT, SOLUSDT, trailing 90 days; 15m context + 2m execution, where supported by the specific original lecture. Use only completed bars, no future swing confirmation, stop-first for intrabar OHLC collisions or 1m reconstruction. Record model, source timestamps, signal, fill, stop, TP, invalidation, fees, net R, unfilled and open positions. Never publish win rate for any model without a fully verified deterministic source rulebook.

## Source caution
Michael's examples are often discretionary and taught on Nasdaq futures; transposing to crypto is an explicit **research extrapolation**, not a claim he personally tested crypto.
