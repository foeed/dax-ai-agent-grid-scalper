from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .backtest import BacktestEngine, generate_report, load_csv_rates
from .backtest_data import HistoricalDataGenerator
from .config import Settings, load_settings
from .models import AccountSnapshot

logger = logging.getLogger(__name__)


def run_backtest_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backtest the pullback scalper strategy")
    parser.add_argument("--symbol", default="EURUSD", help="Trading symbol (EURUSD, XAUUSD)")
    parser.add_argument("--timeframe", default="M15", help="Bar timeframe (M1, M5, M15, H1)")
    parser.add_argument("--days", type=int, default=21, help="Days of data to generate (default: 21 = 3 weeks)")
    parser.add_argument("--equity", type=float, default=100, help="Starting account equity")
    parser.add_argument("--risk", type=float, default=0.5, help="Risk per trade %%")
    parser.add_argument("--csv-m1", help="Path to M1 CSV file (overrides data generation)")
    parser.add_argument("--csv-m5", help="Path to M5 CSV file")
    parser.add_argument("--generate-csv", help="Path to save generated data CSV")
    parser.add_argument("--compare", action="store_true", help="Run comparison on both EURUSD and XAUUSD")
    parser.add_argument("--auto-account", action="store_true", help="Auto-configure for account size")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s - %(message)s")

    if args.compare:
        return _run_comparison(args)

    return _run_single(args)


def _run_single(args) -> int:
    symbol = args.symbol.upper()
    timeframe = args.timeframe.upper()
    equity = args.equity

    if args.auto_account:
        from .accounts import select_profile, profile_to_runtime
        profile = select_profile(equity)
        symbol = profile.recommended_symbol
        timeframe = profile.timeframe
        print(f"\n  Auto-config: ${equity:.0f} -> {profile.bracket} bracket: {symbol} {timeframe}")
        print(f"  Risk: {profile.max_risk_percent}%  Trades/day: {profile.max_trades_per_day}\n")

    # Load or generate data
    if args.csv_m1:
        print(f"  Loading M1 data from: {args.csv_m1}")
        rates_m1 = load_csv_rates(args.csv_m1)
        rates_m5 = load_csv_rates(args.csv_m5) if args.csv_m5 else None
    else:
        print(f"  Generating {args.days} days of {symbol} {timeframe} data...")
        gen = HistoricalDataGenerator(symbol, timeframe, days=args.days)
        rates_m1 = gen.generate()
        rates_m5 = HistoricalDataGenerator(symbol, "M5", gen.start_date, args.days).generate()
        if args.generate_csv:
            gen.save_to_csv(rates_m1, args.generate_csv)
            print(f"  Saved to: {args.generate_csv}")

    print(f"  Data: {len(rates_m1)} {timeframe} bars, {len(rates_m5) if rates_m5 else 0} M5 bars")

    # Configure risk for budget
    risk_pct = args.risk
    if equity <= 100:
        risk_pct = max(risk_pct, 1.5)
        print(f"  Small account (${equity:.0f}): using {risk_pct}% risk per trade")

    settings = load_settings(None)
    settings = settings.__class__(**{**settings.__dict__, "symbol": symbol,
        "timeframe": timeframe, "min_lot": 0.01,
        "max_lot": 0.01, "risk_percent": risk_pct,
        "state_path": Path("data") / f"bt_state_{symbol}_{timeframe}.json",
        "max_trades_per_day": 1000, "cooldown_seconds": 0})
    # Clean stale backtest state
    import os
    state_path = Path("data") / f"bt_state_{symbol}_{timeframe}.json"
    if state_path.exists():
        state_path.unlink()

    runtime = {
        "symbol": symbol, "timeframe": timeframe,
        "risk_percent": risk_pct,
        "min_signal_confidence": 0.55,
        "min_risk_reward": 2.0,
        "min_trend_strength": 0.45,
        "min_entry_bar_gap": 3,
        "max_positions": 1,
        "max_trades_per_day": 1000,
        "cooldown_seconds": 0,
        "daily_loss_limit_percent": 90.0,
        "max_session_drawdown_percent": 90.0,
        "max_consecutive_losses": 999,
        "spread_points": 3.5 if symbol == "XAUUSD" else 1.5,
        "sl_atr_multiplier": 1.8,
        "tp_atr_multiplier": 3.5,
        "trailing_stop_atr_multiplier": 0.8,
        "time_stop_bars": 25,
        "ema_fast": 8, "ema_slow": 21, "ema_trend": 55,
        "atr_period": 14, "rsi_period": 14,
        "min_trend_strength": 0.40,
        "min_entry_bar_gap": 3,
    }

    account = AccountSnapshot(balance=equity, equity=equity)

    engine = BacktestEngine(settings)
    result = engine.run(rates_m1, rates_m5, account, runtime)
    print(generate_report(result))
    return 0


def _run_comparison(args) -> int:
    """Run backtest on both EURUSD and XAUUSD for comparison."""
    equity = args.equity

    configs = [
        ("EURUSD", "M15", 1.5, 1.8, 3.5),
        ("XAUUSD", "M5", 0.5, 1.8, 3.5),
    ]

    results = []
    for symbol, tf, risk, sl, tp in configs:
        print(f"\n{'='*60}")
        print(f"  Testing: {symbol} {tf}  Risk: {risk}%  SL: {sl}xATR  TP: {tp}xATR")
        print(f"{'='*60}")

        gen = HistoricalDataGenerator(symbol, tf, days=args.days)
        rates_m1 = gen.generate()
        rates_m5 = HistoricalDataGenerator(symbol, "M5", gen.start_date, args.days).generate()

        # Isolated state per backtest run
        import os
        state_path = Path("data") / f"bt_compare_{symbol}_{tf}.json"
        if state_path.exists():
            state_path.unlink()

        settings = load_settings(None)
        settings = settings.__class__(**{**settings.__dict__, "symbol": symbol,
            "timeframe": tf, "min_lot": 0.01, "max_lot": 0.01,
            "state_path": state_path, "cooldown_seconds": 0})

        runtime = {
            "symbol": symbol, "timeframe": tf, "risk_percent": risk,
            "min_signal_confidence": 0.55, "min_risk_reward": 2.0,
            "min_trend_strength": 0.45, "min_entry_bar_gap": 3,
            "max_positions": 1, "max_trades_per_day": 1000, "cooldown_seconds": 0,
            "daily_loss_limit_percent": 90.0, "max_session_drawdown_percent": 90.0,
            "max_consecutive_losses": 999,
            "spread_points": 3.5 if symbol == "XAUUSD" else 1.5,
            "sl_atr_multiplier": sl, "tp_atr_multiplier": tp,
            "trailing_stop_atr_multiplier": 0.8,
            "time_stop_bars": 20,
            "ema_fast": 8, "ema_slow": 21, "ema_trend": 55,
            "atr_period": 14, "rsi_period": 14,
        }
        account = AccountSnapshot(balance=equity, equity=equity)
        engine = BacktestEngine(settings)
        result = engine.run(rates_m1, rates_m5, account, runtime)
        print(generate_report(result))
        results.append(result)

    # Comparison summary
    print("\n" + "=" * 60)
    print("  COMPARISON SUMMARY")
    print("=" * 60)
    print(f"  {'Symbol':<10} {'TF':<5} {'Trades':>7} {'Win%':>7} {'PF':>6} {'Net$':>8} {'DD%':>6} {'Sharpe':>7}")
    print("-" * 60)
    best = None
    best_pf = 0
    for r in results:
        net = r.ending_equity - r.starting_equity
        print(f"  {r.symbol:<10} {r.timeframe:<5} {r.total_trades:>7} {r.win_rate:>6.1f}% {r.profit_factor:>5.2f} "
              f"${net:>7.2f} {r.max_drawdown_pct:>5.1f}% {r.sharpe_ratio:>6.2f}")
        if r.profit_factor > best_pf:
            best_pf = r.profit_factor
            best = r

    if best:
        print(f"\n  RECOMMENDATION: {best.symbol} {best.timeframe} (PF={best.profit_factor:.2f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_backtest_cli())
