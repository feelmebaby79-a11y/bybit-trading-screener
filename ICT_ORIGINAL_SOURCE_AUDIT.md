# ICT original-source verification gate (2026-10-20)

Target: BTCUSDT / ETHUSDT / SOLUSDT, Binance USD-M 15m candles, last 90 days. Results MUST NOT be described as ICT-original until each model's rules and exit logic are traceable to specific official ICT lessons.

Verified primary source so far:
- Inner Circle Trader, 2022-02-03, https://www.youtube.com/watch?v=Bkt8B3kLATQ — official description explicitly covers Fair Value Gap and Market Structure Shifts. This alone does NOT verify a complete entry/exit system.

Unverified models (DO NOT substitute proxy definitions):
- ICT 2022: exact sequencing, timeframe, killzone, target and invalidation details need lesson-level checks.
- Breaker: source-specific liquidity/market-structure requirements need verification.
- Unicorn: source-specific breaker/FVG overlap and context need verification.
- OTE: source-specific dealing range, premium/discount and entry/exit requirements need verification.
- Silver Bullet: source-specific sessions, market structure, FVG, stop and liquidity targets need verification.
- Mitigation Block: distinguish from ordinary unbroken order block, per original lessons.
- OB Continuation: independent original-source model definition required; do not duplicate Mitigation Block.

Backtest engine requirements:
1. Use native 15m candles; no 24h forced exit; no arbitrary fixed 2R exit.
2. One trade per eligible setup unless original lesson permits reentry; distinguish simultaneous overlapping positions.
3. Explicit entry trigger, invalidation, opposing liquidity target and cancellation condition with lesson URLs/timestamps.
4. No future-looking pivots; include fees, slippage assumptions and same-candle ambiguity treatment.
5. Keep open trades separate from realized outcomes; report trade-by-trade audit trail and per-symbol counts.

STATUS: SOURCE VERIFICATION INCOMPLETE; ORIGINAL-ICT BACKTEST NOT RUN. Existing historical backtest artifacts are proxy simulations, not original-strategy evidence.

## Primary-source correction: 2022 Mentorship Episode 3

Official recording: https://www.youtube.com/watch?v=nQfHZ2DEJ8c (2022-01-25 lecture, Internal Range Liquidity and Market Structure Shift). Transcript mirror (NOT official): https://youtubetotranscript.com/transcript?current_language_code=en&v=nQfHZ2DEJ8c .

Explicit points in the speaker's example:
- 15-minute NQ chart identifies old lows / relative equal highs and resting liquidity; the demonstration then **drops to the 2-minute chart** to identify the intraday market structure shift and execution setup.
- The short-term swing high break after taking sell stops is meaningful *because* liquidity has been taken; the speaker says a close above that high is **not required**.
- The next completed candle is checked for a three-candle FVG; the retracement into FVG and the opening price of the down-close candle series (order block) are discussed for limit entry.
- Targets are opposing buy stops / sell stops and, in examples, FVGs. No universal fixed-R exit stated in this passage.

**BLOCKER:** The currently downloaded **15m-only** candles cannot reconstruct the speaker's **2m entry sequence**. Do not claim 15m-only tests replicate this primary-source example. Download 1m or 2m execution candles, resample 15m for context, and ensure all lower-timeframe data used are time-available at entry. If 2m data are unavailable, 1m->2m resampling is acceptable when verified against source timestamps.

## Primary source: Silver Bullet
Official video: https://www.youtube.com/watch?v=tRq1hyGGtl4 (2023-05-15, ICT Silver Bullet Time Based Trading Model). Source-specific time windows and exits must be transcribed and validated before coding. A trading window is NOT a forced position close time.

## Remaining source work
Official original lesson IDs and exact timestamp-level rules remain unverified for Unicorn, Breaker, OTE, Mitigation Block, and OB Continuation. A seven-model original-ICT performance claim is not currently supported.
