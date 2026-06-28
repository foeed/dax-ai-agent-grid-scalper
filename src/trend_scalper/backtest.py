from __future__ import annotations

import csv
import logging
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import Settings, load_settings
from .exit_manager import ExitManager
from .models import AccountSnapshot, EntrySignal, Rate, TradeSignal
from .risk import RiskManager
from .strategy import PullbackScalperStrategy

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    entry_time: str = ""
    exit_time: str = ""
    action: str = "BUY"
    entry_price: float = 0.0
    exit_price: float = 0.0
    volume: float = 0.01
    sl_price: float = 0.0
    tp_price: float = 0.0
    exit_reason: str = ""
    pnl: float = 0.0
    pnl_pct: float = 0.0
    mfe: float = 0.0
    mae: float = 0.0
    bars_held: int = 0
    confidence: float = 0.0
    trend_strength: float = 0.0


@dataclass
class BacktestResult:
    symbol: str = ""
    timeframe: str = ""
    period: str = ""
    starting_equity: float = 100.0
    ending_equity: float = 100.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    max_drawdown_duration_bars: int = 0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    avg_rr: float = 0.0
    sharpe_ratio: float = 0.0
    total_commission: float = 0.0
    trades: list[TradeRecord] = field(default_factory=list)


class BacktestEngine:
    """Candle-by-candle backtest of the pullback strategy with exit management."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.strategy = PullbackScalperStrategy()
        self.exit_mgr = ExitManager()
        self.risk = RiskManager(settings)
        self._trade_counter = 0
        self._last_exit_bar_index = -999
        self._current_bar_index = 0
        self._signals_evaluated = 0
        self._signals_traded = 0
        self._signals_blocked_risk = 0
        self._signals_blocked_rr = 0
        self._equity: float = 0.0
        self._peak_equity: float = 0.0
        self._starting_equity: float = 0.0
        self._trades: list[TradeRecord] = []
        self._equity_curve: list[float] = []
        self._active_trade: TradeRecord | None = None
        self._active_signal: EntrySignal | None = None
        self._spread_points: float = 0.0
        self._point_value: float = 0.01

    def run(
        self,
        rates_m1: list[Rate],
        rates_m5: list[Rate] | None = None,
        account: AccountSnapshot | None = None,
        runtime: dict[str, Any] | None = None,
    ) -> BacktestResult:
        """Run the backtest on historical data."""
        acct = account or AccountSnapshot(balance=self.settings.min_lot * 1000 + 100,
                                          equity=self.settings.min_lot * 1000 + 100)
        self._equity = acct.equity
        self._peak_equity = acct.equity
        self._starting_equity = acct.equity
        rt = runtime or {}
        # Clean any stale backtest state
        if self.settings.state_path.exists():
            try:
                self.settings.state_path.write_text("")
            except OSError:
                pass

        symbol = str(rt.get("symbol", self.settings.symbol))
        self._point_value = 0.01 if symbol.upper().startswith("XAU") else 0.00001
        self._spread_points = float(rt.get("spread_points", 5 if symbol.upper().startswith("XAU") else 1.5))

        warmup_bars = int(rt.get("ema_trend", 55)) + 30
        total_bars = len(rates_m1)

        for i in range(warmup_bars, total_bars):
            self._current_bar_index = i
            bar = rates_m1[i]
            bar_time = str(bar.get("time", ""))
            close = float(bar.get("close", 0))
            high = float(bar.get("high", 0))
            low = float(bar.get("low", 0))

            # Check exits for active trade
            if self._active_trade is not None and self._active_signal is not None:
                self._check_exit(rates_m1[:i + 1], rates_m5[-300:] if rates_m5 and len(rates_m5) >= 65 else None,
                                 bar_time, close, high, low)

            # Check for new entries
            if self._active_trade is None:
                # Enforce bar gap since last exit
                min_gap = int(rt.get("min_entry_bar_gap", 3))
                if self._current_bar_index - self._last_exit_bar_index < min_gap:
                    continue
                entry_m1 = rates_m1[max(0, i - 300):i + 1]
                # Trend data: pass last 300 bars for EMA warmup (work for any TF)
                if rates_m5 and len(rates_m5) >= 65:
                    entry_m5 = rates_m5[-300:] if len(rates_m5) >= 300 else rates_m5
                else:
                    entry_m5 = None

                if len(entry_m1) < warmup_bars:
                    continue

                current_acct = AccountSnapshot(balance=self._equity, equity=self._equity)
                allowed, risk_reason = self.risk.can_trade(current_acct, rt)
                if not allowed:
                    continue

                signal = self.strategy.analyze(entry_m1, entry_m5, self._point_value, rt)
                if not signal.is_trade:
                    continue

                # Apply R:R gate
                est_spread = self._spread_points * self._point_value
                net_tp = signal.tp_distance - est_spread
                net_sl = signal.sl_distance + est_spread
                min_rr = float(rt.get("min_risk_reward", 1.5))
                if net_tp > 0 and net_sl > 0 and net_tp / net_sl < min_rr:
                    continue

                # Enter trade
                self._enter_trade(signal, close, bar_time, rt)

        # Close any remaining open trade at last bar
        if self._active_trade is not None:
            last_close = float(rates_m1[-1].get("close", 0))
            self._close_trade(last_close, str(rates_m1[-1].get("time", "")), "End of data")

        return self._build_result(symbol, str(rt.get("timeframe", self.settings.timeframe)),
                                  str(rates_m1[0].get("time", "")), str(rates_m1[-1].get("time", "")))

    def _enter_trade(self, signal: EntrySignal, price: float, bar_time: str, rt: dict) -> None:
        self._trade_counter += 1
        risk_pct = float(rt.get("risk_percent", 0.5))
        risk_amount = self._equity * (risk_pct / 100.0)
        sl_distance = max(signal.sl_distance, 0.0001)
        volume = self.settings.min_lot
        commission = volume * 7.0  # ~$7 per standard lot round-turn, scaled to min_lot

        sl_price = price - sl_distance if signal.action == "BUY" else price + sl_distance
        tp_price = price + signal.tp_distance if signal.action == "BUY" else price - signal.tp_distance

        trade = TradeRecord(
            entry_time=bar_time,
            action=signal.action,
            entry_price=price,
            volume=volume,
            sl_price=sl_price,
            tp_price=tp_price,
            confidence=signal.confidence,
            trend_strength=signal.trend_strength,
            pnl=-commission,
        )
        self._active_trade = trade
        self._active_signal = signal
        self.exit_mgr.register_trade(self._trade_counter, signal)
        self.risk.record_trade(AccountSnapshot(balance=self._equity, equity=self._equity), success=True)

    def _check_exit(self, m1_rates: list[Rate], m5_rates: list[Rate] | None,
                    bar_time: str, close: float, high: float, low: float) -> None:
        if self._active_trade is None or self._active_signal is None:
            return

        trade = self._active_trade
        is_long = trade.action == "BUY"

        # Check hard SL/TP
        sl_hit = (is_long and low <= trade.sl_price) or (not is_long and high >= trade.sl_price)
        tp_hit = (is_long and high >= trade.tp_price) or (not is_long and low <= trade.tp_price)

        if sl_hit:
            exit_price = trade.sl_price
            self._close_trade(exit_price, bar_time, "Hard SL")
            return
        if tp_hit:
            exit_price = trade.tp_price
            self._close_trade(exit_price, bar_time, "Hard TP")
            return

        # Check exit manager rules
        trend_dir = 0
        if m5_rates and len(m5_rates) >= 60:
            from .indicators import add_indicators
            data = add_indicators(m5_rates, 8, 21, 55, 14, 14)
            ready = [r for r in data if all(r.get(k) is not None for k in ("ema_fast", "ema_slow", "ema_trend"))]
            if ready:
                last = ready[-1]
                ef, es, et = float(last["ema_fast"]), float(last["ema_slow"]), float(last["ema_trend"])
                if ef > es > et:
                    trend_dir = 1
                elif ef < es < et:
                    trend_dir = -1

        bar = {"close": close, "high": high, "low": low}
        exit_result = self.exit_mgr.evaluate(self._trade_counter, [bar], m5_rates, trend_dir)
        if exit_result.action == "CLOSE":
            self._close_trade(close, bar_time, exit_result.reason)  # noqa: F821

    def _close_trade(self, exit_price: float, bar_time: str, reason: str) -> None:
        if self._active_trade is None or self._active_signal is None:
            return

        trade = self._active_trade
        signal = self._active_signal
        is_long = trade.action == "BUY"
        point_val = self._point_value

        # Calculate P&L
        price_diff = exit_price - trade.entry_price if is_long else trade.entry_price - exit_price
        symbol_upper = self.settings.symbol.upper()
        if symbol_upper.startswith("XAU") or symbol_upper.startswith("XAG"):
            contract_size = 100.0
        else:
            contract_size = 100000.0
        pnl_dollars = price_diff * trade.volume * contract_size
        commission = trade.volume * 7.0
        net_pnl = pnl_dollars - commission

        trade.exit_time = bar_time
        trade.exit_reason = reason
        trade.exit_price = exit_price
        trade.pnl = round(net_pnl, 2)
        trade.pnl_pct = round((net_pnl / self._equity) * 100, 4) if self._equity > 0 else 0.0

        # Calculate MFE/MAE from exit manager state
        state = self.exit_mgr.get_state(self._trade_counter)
        if state:
            trade.mfe = round(state.get("highest_profit", 0.0), 6)
            trade.bars_held = state.get("bars_held", 0)

        # Update equity
        self._equity += net_pnl
        self._peak_equity = max(self._peak_equity, self._equity)
        self._equity_curve.append(self._equity)

        self._trades.append(trade)
        self.exit_mgr.remove_trade(self._trade_counter)
        self._last_exit_bar_index = self._current_bar_index
        self._active_trade = None
        self._active_signal = None

    def _build_result(self, symbol: str, timeframe: str, start: str, end: str) -> BacktestResult:
        trades = self._trades
        total = len(trades)
        winners = [t for t in trades if t.pnl > 0]
        losers = [t for t in trades if t.pnl < 0]
        num_wins = len(winners)
        num_loss = len(losers)

        win_rate = (num_wins / total * 100) if total > 0 else 0.0
        gross_profit = sum(t.pnl for t in winners)
        gross_loss = abs(sum(t.pnl for t in losers))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
        avg_win = (gross_profit / num_wins) if num_wins > 0 else 0.0
        avg_loss = (gross_loss / num_loss) if num_loss > 0 else 0.0
        avg_rr = (avg_win / avg_loss) if avg_loss > 0 else 0.0

        # Drawdown from equity curve
        peak = self._starting_equity
        max_dd = 0.0
        max_dd_bars = 0
        current_dd_bars = 0
        for eq in self._equity_curve:
            if eq > peak:
                peak = eq
                current_dd_bars = 0
            dd = (peak - eq) / peak * 100
            max_dd = max(max_dd, dd)
            if dd > 0:
                current_dd_bars += 1
            else:
                current_dd_bars = 0
            max_dd_bars = max(max_dd_bars, current_dd_bars)

        # Sharpe ratio (simplified: assumes risk-free = 0)
        returns = []
        for i in range(1, len(self._equity_curve)):
            r = (self._equity_curve[i] - self._equity_curve[i - 1]) / self._equity_curve[i - 1]
            returns.append(r)
        avg_return = sum(returns) / len(returns) if returns else 0.0
        variance = sum((r - avg_return) ** 2 for r in returns) / len(returns) if returns else 0.0
        std_dev = math.sqrt(variance) if variance > 0 else 0.0001
        sharpe = (avg_return / std_dev * math.sqrt(252 * 96)) if std_dev > 0 else 0.0  # Annualized (96 = M15 bars/day)

        total_commission = total * self.settings.min_lot * 7.0

        return BacktestResult(
            symbol=symbol,
            timeframe=timeframe,
            period=f"{start} to {end}",
            starting_equity=round(self._starting_equity, 2),
            ending_equity=round(self._equity, 2),
            total_trades=total,
            winning_trades=num_wins,
            losing_trades=num_loss,
            win_rate=round(win_rate, 1),
            gross_profit=round(gross_profit, 2),
            gross_loss=round(gross_loss, 2),
            profit_factor=round(profit_factor, 2),
            max_drawdown_pct=round(max_dd, 2),
            max_drawdown_duration_bars=max_dd_bars,
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            avg_rr=round(avg_rr, 2),
            sharpe_ratio=round(sharpe, 2),
            total_commission=round(total_commission, 2),
            trades=trades,
        )


def generate_report(result: BacktestResult) -> str:
    """Generate a human-readable backtest report."""
    lines = []
    lines.append("=" * 60)
    lines.append("  TREND SCALPER AI — BACKTEST REPORT")
    lines.append("=" * 60)
    lines.append(f"  Symbol:      {result.symbol}")
    lines.append(f"  Timeframe:   {result.timeframe}")
    lines.append(f"  Period:      {result.period}")
    lines.append("-" * 60)
    lines.append(f"  Start Equity:   ${result.starting_equity:>10.2f}")
    lines.append(f"  End Equity:     ${result.ending_equity:>10.2f}")
    net = result.ending_equity - result.starting_equity
    net_pct = (net / result.starting_equity * 100) if result.starting_equity > 0 else 0
    lines.append(f"  Net P&L:        ${net:>10.2f}  ({net_pct:+.1f}%)")
    lines.append("-" * 60)
    lines.append(f"  Total Trades:   {result.total_trades:>10}")
    lines.append(f"  Winners:        {result.winning_trades:>10}  ({result.win_rate:.1f}%)")
    lines.append(f"  Losers:         {result.losing_trades:>10}")
    lines.append(f"  Profit Factor:  {result.profit_factor:>10.2f}")
    lines.append(f"  Avg Win:        ${result.avg_win:>10.2f}")
    lines.append(f"  Avg Loss:       ${result.avg_loss:>10.2f}")
    lines.append(f"  Avg R:R:        {result.avg_rr:>10.2f}")
    lines.append("-" * 60)
    lines.append(f"  Max Drawdown:   {result.max_drawdown_pct:>9.1f}%")
    lines.append(f"  Max DD Bars:    {result.max_drawdown_duration_bars:>10}")
    lines.append(f"  Sharpe Ratio:   {result.sharpe_ratio:>10.2f}")
    lines.append(f"  Commission:     ${result.total_commission:>10.2f}")
    lines.append("=" * 60)

    # Trade log
    if result.trades:
        lines.append("")
        lines.append("TRADE LOG:")
        lines.append(f"{'#':<4} {'Entry':<20} {'Exit':<20} {'Act':<5} {'P&L':>8} {'%':>7} {'Reason':<30}")
        lines.append("-" * 100)
        for i, t in enumerate(result.trades, 1):
            lines.append(
                f"{i:<4} {t.entry_time[:19]:<20} {t.exit_time[:19]:<20} {t.action:<5} "
                f"${t.pnl:>7.2f} {t.pnl_pct:>6.2f}% {t.exit_reason[:28]:<30}"
            )

    # Verdict
    lines.append("")
    lines.append("VERDICT:")
    if result.profit_factor >= 1.5 and result.win_rate >= 45 and result.sharpe_ratio >= 0.5:
        lines.append("  PASS — Strategy shows positive expectancy. Paper trade next.")
    elif result.profit_factor >= 1.0 and result.win_rate >= 35:
        lines.append("  MARGINAL — Break-even. Needs parameter tuning before live.")
    else:
        lines.append("  FAIL — Strategy loses money in this period. Do NOT go live.")
    lines.append("=" * 60)
    return "\n".join(lines)


def load_csv_rates(path: str | Path, limit_bars: int = 0) -> list[Rate]:
    """Load OHLC rates from a CSV file (MT5 export format)."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"CSV not found: {p}")

    with p.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    required = {"open", "high", "low", "close"}
    if reader.fieldnames:
        missing = required.difference(reader.fieldnames)
        if missing:
            raise ValueError(f"Missing columns: {missing}")

    rates: list[Rate] = []
    for row in rows:
        rates.append({
            "time": row.get("time", row.get("date", "")),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "tick_volume": int(float(row.get("tick_volume", row.get("volume", 0)))),
        })

    if limit_bars > 0:
        rates = rates[-limit_bars:]

    return rates
