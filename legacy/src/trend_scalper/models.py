from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


Action = Literal["BUY", "SELL", "HOLD"]
Rate = dict[str, Any]


@dataclass(frozen=True)
class TradeSignal:
    action: Action
    confidence: float
    reason: str
    sl_distance: float = 0.0
    tp_distance: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_trade(self) -> bool:
        return self.action in {"BUY", "SELL"}


@dataclass(frozen=True)
class EntrySignal:
    """Enhanced signal with entry pre-trade analysis for smart exits."""
    action: Action
    confidence: float
    reason: str
    sl_distance: float = 0.0
    tp_distance: float = 0.0
    entry_price: float = 0.0
    entry_atr: float = 0.0
    trend_direction: int = 0
    trend_strength: float = 0.0
    exit_rules: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_trade(self) -> bool:
        return self.action in {"BUY", "SELL"}

    def as_trade_signal(self) -> TradeSignal:
        return TradeSignal(
            action=self.action,
            confidence=self.confidence,
            reason=self.reason,
            sl_distance=self.sl_distance,
            tp_distance=self.tp_distance,
            metadata=self.metadata,
        )

    @classmethod
    def no_trade(cls, reason: str, entry_price: float = 0.0, entry_atr: float = 0.0, metadata: dict[str, Any] | None = None) -> EntrySignal:
        return cls(
            action="HOLD", confidence=0.0, reason=reason,
            entry_price=entry_price, entry_atr=entry_atr,
            metadata=metadata or {},
        )


@dataclass(frozen=True)
class ExitAction:
    action: Literal["HOLD", "CLOSE", "TRAIL", "BREAKEVEN"]
    reason: str


@dataclass(frozen=True)
class LLMDecision:
    approved: bool
    score: float
    reason: str


@dataclass(frozen=True)
class AccountSnapshot:
    balance: float
    equity: float
    currency: str = "USD"


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    side: str
    volume: float
    profit: float
    magic: int


@dataclass(frozen=True)
class OrderResult:
    success: bool
    message: str
    order_id: int | None = None
    retcode: int | None = None


@dataclass
class RegimeConfig:
    """In-memory trading regime set by the async LLM brain.

    This is the single source of truth for the fast-path strategy engine.
    Updated atomically by the background LLM regime thread every 5-15 minutes.
    """

    trading_bias: str = "neutral"  # bullish, bearish, neutral
    strategy_mode: str = "trend_following"  # trend_following, mean_reversion, cautious
    max_risk_percent: float = 0.25
    max_positions: int = 2
    min_signal_confidence: float = 0.64
    sl_atr_multiplier: float = 1.3
    tp_atr_multiplier: float = 1.8
    cooldown_seconds: int = 113
    ema_fast: int = 8
    ema_slow: int = 21
    ema_trend: int = 55
    atr_period: int = 14
    rsi_period: int = 14
    min_stop_points: int = 80
    updated_at: float = 0.0
    llm_reasoning: str = ""
    llm_confidence: float = 0.5
    source: str = "default"  # "default", "llm_regime", "manual"

    def as_dict(self) -> dict[str, Any]:
        return {
            "trading_bias": self.trading_bias,
            "strategy_mode": self.strategy_mode,
            "max_risk_percent": self.max_risk_percent,
            "max_positions": self.max_positions,
            "min_signal_confidence": self.min_signal_confidence,
            "sl_atr_multiplier": self.sl_atr_multiplier,
            "tp_atr_multiplier": self.tp_atr_multiplier,
            "cooldown_seconds": self.cooldown_seconds,
            "ema_fast": self.ema_fast,
            "ema_slow": self.ema_slow,
            "ema_trend": self.ema_trend,
            "atr_period": self.atr_period,
            "rsi_period": self.rsi_period,
            "min_stop_points": self.min_stop_points,
            "updated_at": self.updated_at,
            "llm_reasoning": self.llm_reasoning,
            "llm_confidence": self.llm_confidence,
            "source": self.source,
        }

    def effective_runtime(self, base: dict[str, Any] | None = None) -> dict[str, Any]:
        """Merge regime config into a runtime dict for the strategy engine."""
        result: dict[str, Any] = {
            "ema_fast": self.ema_fast,
            "ema_slow": self.ema_slow,
            "ema_trend": self.ema_trend,
            "atr_period": self.atr_period,
            "rsi_period": self.rsi_period,
            "sl_atr_multiplier": self.sl_atr_multiplier,
            "tp_atr_multiplier": self.tp_atr_multiplier,
            "min_stop_points": self.min_stop_points,
            "min_signal_confidence": self.min_signal_confidence,
            "risk_percent": self.max_risk_percent,
            "max_positions": self.max_positions,
            "cooldown_seconds": self.cooldown_seconds,
            "use_risk_sizing": True,
            "auto_tune": True,
            "auto_tune_profile": f"llm-{self.strategy_mode}-{self.trading_bias}",
            "auto_tune_summary": f"LLM regime: {self.strategy_mode} / {self.trading_bias} bias (conf={self.llm_confidence:.2f})",
            "use_llm": bool(self.llm_reasoning),
            "llm_fail_closed": True,
            "one_trade_per_bar": True,
            "settings_refresh_seconds": 60,
        }
        if base:
            result = {**base, **result}
        return result
