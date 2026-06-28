"""Account-size-aware auto-configuration.

Auto-selects symbol, timeframe, and risk parameters based on account equity
to prevent over-leverage on small accounts and under-utilization on large ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AccountProfile:
    """Trading profile tuned for a specific account size bracket."""

    bracket: str               # "micro" | "mini" | "standard" | "institutional"
    min_equity: float          # minimum equity for this bracket
    recommended_symbol: str    # primary trading symbol
    timeframe: str             # recommended timeframe
    max_risk_percent: float    # per-trade risk %
    max_daily_loss_percent: float
    max_session_dd_percent: float
    max_positions: int
    max_trades_per_day: int
    cooldown_seconds: int
    sl_atr_multiplier: float
    tp_atr_multiplier: float
    min_signal_confidence: float
    min_risk_reward: float
    trailing_stop_atr: float


PROFILES: list[AccountProfile] = [
    AccountProfile(
        bracket="micro",
        min_equity=0.0,
        recommended_symbol="EURUSD",
        timeframe="M15",
        max_risk_percent=2.0,
        max_daily_loss_percent=8.0,
        max_session_dd_percent=15.0,
        max_positions=1,
        max_trades_per_day=3,
        cooldown_seconds=600,
        sl_atr_multiplier=1.5,
        tp_atr_multiplier=4.0,
        min_signal_confidence=0.58,
        min_risk_reward=2.0,
        trailing_stop_atr=0.8,
    ),
    AccountProfile(
        bracket="mini",
        min_equity=200.0,
        recommended_symbol="EURUSD",
        timeframe="M5",
        max_risk_percent=1.0,
        max_daily_loss_percent=5.0,
        max_session_dd_percent=10.0,
        max_positions=1,
        max_trades_per_day=5,
        cooldown_seconds=300,
        sl_atr_multiplier=1.2,
        tp_atr_multiplier=3.0,
        min_signal_confidence=0.55,
        min_risk_reward=1.8,
        trailing_stop_atr=0.7,
    ),
    AccountProfile(
        bracket="standard",
        min_equity=1000.0,
        recommended_symbol="XAUUSD",
        timeframe="M5",
        max_risk_percent=0.5,
        max_daily_loss_percent=3.0,
        max_session_dd_percent=6.0,
        max_positions=1,
        max_trades_per_day=8,
        cooldown_seconds=180,
        sl_atr_multiplier=1.0,
        tp_atr_multiplier=2.8,
        min_signal_confidence=0.52,
        min_risk_reward=1.5,
        trailing_stop_atr=0.6,
    ),
    AccountProfile(
        bracket="institutional",
        min_equity=10000.0,
        recommended_symbol="XAUUSD",
        timeframe="M1",
        max_risk_percent=0.25,
        max_daily_loss_percent=2.0,
        max_session_dd_percent=5.0,
        max_positions=1,
        max_trades_per_day=12,
        cooldown_seconds=90,
        sl_atr_multiplier=1.0,
        tp_atr_multiplier=2.8,
        min_signal_confidence=0.50,
        min_risk_reward=1.5,
        trailing_stop_atr=0.6,
    ),
]


def select_profile(equity: float) -> AccountProfile:
    """Return the account profile for the given equity."""
    for profile in reversed(PROFILES):
        if equity >= profile.min_equity:
            return profile
    return PROFILES[0]


def profile_to_runtime(profile: AccountProfile, symbol: str | None = None) -> dict[str, Any]:
    """Convert an AccountProfile to a runtime dict for strategy/risk overrides."""
    return {
        "symbol": symbol or profile.recommended_symbol,
        "timeframe": profile.timeframe,
        "risk_percent": profile.max_risk_percent,
        "daily_loss_limit_percent": profile.max_daily_loss_percent,
        "max_session_drawdown_percent": profile.max_session_dd_percent,
        "max_positions": profile.max_positions,
        "max_trades_per_day": profile.max_trades_per_day,
        "cooldown_seconds": profile.cooldown_seconds,
        "sl_atr_multiplier": profile.sl_atr_multiplier,
        "tp_atr_multiplier": profile.tp_atr_multiplier,
        "min_signal_confidence": profile.min_signal_confidence,
        "min_risk_reward": profile.min_risk_reward,
        "trailing_stop_atr_multiplier": profile.trailing_stop_atr,
        "use_risk_sizing": True,
        "auto_tune": False,
        "auto_tune_profile": f"account-{profile.bracket}",
    }


def validate_account_size(equity: float, symbol: str, min_lot: float) -> str | None:
    """Check if the account is too small for the chosen symbol. Returns error string or None."""
    profile = select_profile(equity)
    margin_estimate = _estimate_margin(symbol, min_lot)
    if margin_estimate is None:
        return None
    margin_pct = (margin_estimate / equity) * 100.0
    if margin_pct > 50.0:
        return (
            f"DANGER: {symbol} {min_lot} lots requires ~${margin_estimate:.0f} margin "
            f"({margin_pct:.0f}% of ${equity:.0f} account). "
            f"Switch to {profile.recommended_symbol} or use a cent account."
        )
    if margin_pct > 25.0:
        return (
            f"WARNING: {symbol} {min_lot} lots uses {margin_pct:.0f}% margin. "
            f"Consider switching to {profile.recommended_symbol}."
        )
    return None


def _estimate_margin(symbol: str, min_lot: float) -> float | None:
    """Rough margin estimate for 1:100 leverage."""
    upper = symbol.upper()
    if upper.startswith("XAU"):
        return 2300.0 * min_lot / 100.0 * 100.0  # ~$23 per 0.01 lot @ 1:100
    if upper in ("BTCUSD", "ETHUSD"):
        return 60000.0 * min_lot / 100.0 * 100.0
    if any(upper.startswith(p) for p in ("SOL", "BNB", "XRP", "ADA", "DOGE")):
        return 100.0 * min_lot / 100.0 * 100.0
    # Forex majors: standard lot = 100,000 units, 0.01 lot = 1,000 units
    # Margin = notional / leverage = (1000 * ~1.05) / 100 ≈ $10.50 per 0.01 lot
    return 100000.0 * min_lot / 100.0
