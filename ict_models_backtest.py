"""ICT original-lesson replication gate.

All seven previous proxy signal generators have been REMOVED, not merely disabled.
Only lesson-verified, fully specified entry/exit strategies may be added here.
"""
from dataclasses import dataclass

MODELS = (
    "ICT_2022", "UNICORN", "BREAKER", "OTE",
    "SILVER_BULLET", "MITIGATION_BLOCK", "OB_CONTINUATION",
)
ASSETS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
LOOKBACK_DAYS = 90
CONTEXT_TIMEFRAME = "15m"
EXECUTION_TIMEFRAME = "2m"

@dataclass(frozen=True)
class VerifiedLessonRules:
    model: str
    primary_source: str
    entry: str
    stop: str
    take_profit: str
    invalidation: str
    # A deterministic specification must cite exact original lecture timestamps.
    timestamps: tuple[str, ...]

VERIFIED_RULES: dict[str, VerifiedLessonRules] = {}

def run_backtest() -> None:
    missing = sorted(set(MODELS) - set(VERIFIED_RULES))
    if missing:
        raise RuntimeError(
            "ORIGINAL ICT BACKTEST NOT AVAILABLE: original-lesson-verified "
            "entry/stop/target/invalidation rules missing for: " + ", ".join(missing)
            + ". No proxy trades or performance statistics will be generated."
        )
    raise NotImplementedError(
        "Execution engine must be independently validated against all "
        "seven primary-source specifications before results are published."
    )

if __name__ == "__main__":
    run_backtest()
