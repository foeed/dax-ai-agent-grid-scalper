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
