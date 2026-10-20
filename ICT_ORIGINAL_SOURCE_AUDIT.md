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
