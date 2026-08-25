"""MT5 Live Backtesting — fetch real historical data and run the backtest engine.

Requires MT5 terminal running and logged into an account.
Install: pip install -r requirements-mt5.txt
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import MetaTrader5 as mt5
except ImportError:
    print("ERROR: MetaTrader5 package not installed.")
    print("Run: pip install MetaTrader5==5.0.45")
    print("MT5 terminal must be running and logged in.")
    sys.exit(1)


def init_mt5(path: str | None = None) -> bool:
    """Initialize MT5 connection."""
    kwargs = {}
    if path:
        kwargs["path"] = path
    if not mt5.initialize(**kwargs):
        code, msg = mt5.last_error()
        print(f"MT5 init failed [{code}]: {msg}")
        print("Make sure MT5 terminal is running and logged in.")
        return False
    print(f"Connected to MT5. Terminal: {mt5.terminal_info().path}")
    return True


def fetch_rates(symbol: str, timeframe_str: str, bars: int) -> list[dict] | None:
    """Fetch OHLC rates from MT5."""
    tf_map = {
        "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }
    tf = tf_map.get(timeframe_str.upper())
    if tf is None:
        print(f"Unknown timeframe: {timeframe_str}")
        return None

    if not mt5.symbol_select(symbol, True):
        print(f"Symbol not available: {symbol}")
        return None

    rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
    if rates is None or len(rates) == 0:
        code, msg = mt5.last_error()
        print(f"Failed to fetch {symbol} {timeframe_str} rates [{code}]: {msg}")
        return None

    result = []
    for r in rates:
        ts = datetime.fromtimestamp(int(r["time"]), timezone.utc)
        result.append({
            "time": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "tick_volume": int(r["tick_volume"]),
        })
    return result


def save_csv(rates: list[dict], path: str | Path) -> Path:
    """Save rates to CSV."""
    import csv
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["time", "open", "high", "low", "close", "tick_volume"])
        writer.writeheader()
        writer.writerows(rates)
    return p


def main():
    parser = argparse.ArgumentParser(description="MT5 Live Backtester")
    parser.add_argument("--symbol", default="XAUUSD", help="Symbol to test")
    parser.add_argument("--timeframe", default="M5", help="Entry timeframe (M5, M15)")
    parser.add_argument("--trend-tf", default="M15", help="Trend timeframe for confirmation")
    parser.add_argument("--days", type=int, default=30, help="Days of history to fetch")
    parser.add_argument("--equity", type=float, default=100, help="Starting equity")
    parser.add_argument("--mt5-path", help="Path to terminal64.exe")
    parser.add_argument("--save-csv", action="store_true", help="Save fetched data to CSV for later use")
    parser.add_argument("--csv-dir", default="data", help="Directory for CSV files")
    args = parser.parse_args()

    if not init_mt5(args.mt5_path):
        return 1

    raw_symbol = args.symbol
    # Split suffix (e.g., EURUSD.m -> base=EURUSD, suffix=m)
    if "." in raw_symbol:
        base, suffix = raw_symbol.rsplit(".", 1)
        symbol = base.upper() + "." + suffix
    else:
        symbol = raw_symbol.upper()
    tf = args.timeframe.upper()
    trend_tf = args.trend_tf.upper()

    # Calculate bars needed
    tf_minutes = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
    bars_needed = (args.days * 24 * 60) // tf_minutes.get(tf, 15)
    trend_bars = (args.days * 24 * 60) // tf_minutes.get(trend_tf, 15)

    print(f"\nFetching {symbol} {tf} history ({args.days} days = ~{bars_needed} bars)...")
    rates_m1 = fetch_rates(symbol, tf, bars_needed)
    if not rates_m1:
        mt5.shutdown()
        return 1
    print(f"  Got {len(rates_m1)} {tf} bars")

    print(f"Fetching {symbol} {trend_tf} history ({args.days} days = ~{trend_bars} bars)...")
    rates_trend = fetch_rates(symbol, trend_tf, trend_bars)
    if not rates_trend:
        print("  Warning: trend TF data unavailable, using entry TF for trend")
        rates_trend = rates_m1
    else:
        print(f"  Got {len(rates_trend)} {trend_tf} bars")

    mt5.shutdown()

    # Save to CSV if requested
    if args.save_csv:
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        p1 = save_csv(rates_m1, f"{args.csv_dir}/{symbol}_{tf}_{ts}.csv")
        p2 = save_csv(rates_trend, f"{args.csv_dir}/{symbol}_{trend_tf}_{ts}.csv")
        print(f"Saved: {p1}\nSaved: {p2}")

    # Run backtest
    from trend_scalper.backtest import BacktestEngine, generate_report
    from trend_scalper.config import load_settings
    from trend_scalper.models import AccountSnapshot

    settings = load_settings(None)
    state_path = Path("data") / f"bt_mt5_{symbol}_{tf}.json"
    if state_path.exists():
        state_path.unlink()
    settings = settings.__class__(**{**settings.__dict__,
        "symbol": symbol, "timeframe": tf,
        "min_lot": 0.01, "max_lot": 0.01,
        "state_path": state_path, "cooldown_seconds": 0})

    spread_points = 3.5 if symbol.startswith("XAU") else 1.5

    runtime = {
        "symbol": symbol, "timeframe": tf,
        "risk_percent": 0.5 if symbol.startswith("XAU") else 1.5,
        "min_signal_confidence": 0.55,
        "min_risk_reward": 2.0,
        "min_trend_strength": 0.40,
        "min_entry_bar_gap": 3,
        "max_positions": 1, "max_trades_per_day": 1000, "cooldown_seconds": 0,
        "daily_loss_limit_percent": 90.0, "max_session_drawdown_percent": 90.0,
        "max_consecutive_losses": 999,
        "spread_points": spread_points,
        "sl_atr_multiplier": 1.8, "tp_atr_multiplier": 3.5,
        "trailing_stop_atr_multiplier": 0.8,
        "time_stop_bars": 20,
        "ema_fast": 8, "ema_slow": 21, "ema_trend": 55,
        "atr_period": 14, "rsi_period": 14,
    }

    account = AccountSnapshot(balance=args.equity, equity=args.equity)
    engine = BacktestEngine(settings)
    result = engine.run(rates_m1, rates_trend, account, runtime)
    print(generate_report(result))

    # Cleanup state
    if state_path.exists():
        state_path.unlink()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
