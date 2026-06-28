from __future__ import annotations

import logging
from typing import Any

from .models import EntrySignal, ExitAction, Rate

logger = logging.getLogger(__name__)


class ExitManager:
    """Manages trade exits using a multi-tier rule system.

    Exit rules (evaluated in priority order):
      1. Hard SL — never moved beyond initial SL distance from entry
      2. Hard TP — fixed take-profit target
      3. Trailing stop — activates at `activation_atr` profit, trails at `trail_atr` distance
      4. Breakeven — SL moves to entry when profit exceeds `breakeven_atr` * ATR
      5. Time stop — close after `time_bars` bars without hitting TP/SL
      6. Condition exit — close if trend direction reverses on higher TF
    """

    def __init__(self) -> None:
        self._active_exits: dict[int, _ExitState] = {}

    def register_trade(self, trade_id: int, signal: EntrySignal) -> None:
        rules = signal.exit_rules
        self._active_exits[trade_id] = _ExitState(
            entry_price=signal.entry_price,
            entry_atr=signal.entry_atr,
            direction=signal.action,
            sl_distance=signal.sl_distance,
            tp_distance=signal.tp_distance,
            highest_profit=0.0,
            bars_held=0,
            trail_atr=float(rules.get("trailing_atr_mult", 0.7)),
            breakeven_atr=float(rules.get("breakeven_atr_distance", 0.5)),
            activation_atr=float(rules.get("trailing_activation_atr", 0.8)),
            time_bars=int(rules.get("time_stop_bars", 15)),
            trend_reversal_exit=bool(rules.get("trend_reversal_exit", True)),
            breakeven_triggered=False,
            trailing_active=False,
            trailing_sl=None,
        )

    def remove_trade(self, trade_id: int) -> None:
        self._active_exits.pop(trade_id, None)

    def evaluate(
        self,
        trade_id: int,
        rates: list[Rate],
        trend_rates: list[Rate] | None,
        trend_direction: int,
    ) -> ExitAction:
        """Evaluate exit rules for an active trade.

        Args:
            trade_id: The trade identifier.
            rates: Entry timeframe rates (M1).
            trend_rates: Higher timeframe rates (M5) for trend reversal check.
            trend_direction: Current HTF trend direction (+1 bullish, -1 bearish).

        Returns:
            ExitAction with action type and reason.
        """
        state = self._active_exits.get(trade_id)
        if state is None:
            return ExitAction("HOLD", "trade not found in exit manager")

        if not rates:
            return ExitAction("HOLD", "no rates available")

        bar = rates[-1]
        close = float(bar.get("close", 0))
        high = float(bar.get("high", 0))
        low = float(bar.get("low", 0))
        state.bars_held += 1

        is_long = state.direction == "BUY"
        entry = state.entry_price
        atr = state.entry_atr

        # Current profit in price units
        if is_long:
            current_profit = close - entry
            worst_price = low
        else:
            current_profit = entry - close
            worst_price = high

        state.highest_profit = max(state.highest_profit, current_profit)

        # ── 1. Hard SL check (worst price touched SL?) ──
        sl_price = entry - state.sl_distance if is_long else entry + state.sl_distance
        if (is_long and worst_price <= sl_price) or (not is_long and worst_price >= sl_price):
            logger.info("Trade %d: hard SL hit at %.5f", trade_id, sl_price)
            return ExitAction("CLOSE", f"SL hit ({state.sl_distance:.5f} from entry)")

        # ── 2. Hard TP check ──
        tp_price = entry + state.tp_distance if is_long else entry - state.tp_distance
        if (is_long and high >= tp_price) or (not is_long and low <= tp_price):
            logger.info("Trade %d: hard TP hit at %.5f", trade_id, tp_price)
            return ExitAction("CLOSE", f"TP hit ({state.tp_distance:.5f} from entry)")

        # ── 3. Breakeven activation ──
        if not state.breakeven_triggered and state.highest_profit >= atr * state.breakeven_atr:
            state.sl_distance = 0.0
            state.breakeven_triggered = True
            logger.info("Trade %d: breakeven activated (profit=%.5f, atr=%.5f)", trade_id, state.highest_profit, atr)

        # ── 4. Trailing stop ──
        if not state.trailing_active and state.highest_profit >= atr * state.activation_atr:
            state.trailing_active = True
            state.trailing_sl = current_profit - atr * state.trail_atr
            logger.info("Trade %d: trailing stop activated at profit=%.5f", trade_id, state.highest_profit)

        if state.trailing_active and state.trailing_sl is not None:
            new_trail = state.highest_profit - atr * state.trail_atr
            state.trailing_sl = max(state.trailing_sl, new_trail)
            if current_profit < state.trailing_sl:
                logger.info("Trade %d: trailing stop hit (profit=%.5f, trail=%.5f)", trade_id, current_profit, state.trailing_sl)
                return ExitAction("CLOSE", f"Trailing stop (profit={current_profit:.5f})")

        # ── 5. Time stop ──
        if state.bars_held >= state.time_bars:
            logger.info("Trade %d: time stop (%d bars)", trade_id, state.time_bars)
            return ExitAction("CLOSE", f"Time stop ({state.bars_held} bars)")

        # ── 6. Trend reversal exit ──
        if state.trend_reversal_exit and trend_direction != 0:
            opp_direction = (trend_direction > 0 and not is_long) or (trend_direction < 0 and is_long)
            if opp_direction:
                logger.info("Trade %d: trend reversal exit (trend=%d)", trade_id, trend_direction)
                return ExitAction("CLOSE", f"Trend reversal (trend_dir={trend_direction})")

        return ExitAction("HOLD", f"bars={state.bars_held} profit={current_profit:.5f} trail={state.trailing_active}")

    def get_state(self, trade_id: int) -> dict[str, Any] | None:
        state = self._active_exits.get(trade_id)
        if state is None:
            return None
        return {
            "bars_held": state.bars_held,
            "highest_profit": round(state.highest_profit, 6),
            "breakeven_triggered": state.breakeven_triggered,
            "trailing_active": state.trailing_active,
        }


class _ExitState:
    __slots__ = (
        "entry_price", "entry_atr", "direction", "sl_distance", "tp_distance",
        "highest_profit", "bars_held", "trail_atr", "breakeven_atr",
        "activation_atr", "time_bars", "trend_reversal_exit",
        "breakeven_triggered", "trailing_active", "trailing_sl",
    )

    def __init__(
        self,
        entry_price: float,
        entry_atr: float,
        direction: str,
        sl_distance: float,
        tp_distance: float,
        highest_profit: float,
        bars_held: int,
        trail_atr: float,
        breakeven_atr: float,
        activation_atr: float,
        time_bars: int,
        trend_reversal_exit: bool,
        breakeven_triggered: bool,
        trailing_active: bool,
        trailing_sl: float | None,
    ) -> None:
        self.entry_price = entry_price
        self.entry_atr = entry_atr
        self.direction = direction
        self.sl_distance = sl_distance
        self.tp_distance = tp_distance
        self.highest_profit = highest_profit
        self.bars_held = bars_held
        self.trail_atr = trail_atr
        self.breakeven_atr = breakeven_atr
        self.activation_atr = activation_atr
        self.time_bars = time_bars
        self.trend_reversal_exit = trend_reversal_exit
        self.breakeven_triggered = breakeven_triggered
        self.trailing_active = trailing_active
        self.trailing_sl = trailing_sl
