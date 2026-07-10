"""
DAX V2 Backtest Script - Connects to MT5, downloads bars, runs simulation.
Run this on Windows where MT5 is installed.

Usage:
    python backtest.py
    python backtest.py --symbol XAUUSD --days 30 --balance 10000 --timeframe M5
    python backtest.py --symbol EURUSD --days 30 --balance 40 --timeframe M1
"""

import sys
import os
import io
import argparse
import json
from datetime import datetime, timedelta

# Fix Windows console encoding for unicode
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Add the app directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import MetaTrader5 as mt5
except ImportError:
    print("ERROR: MetaTrader5 package not installed.")
    print("Run: pip install MetaTrader5")
    sys.exit(1)

try:
    from app.services.backtest_engine import run_backtest, Bar
except ImportError:
    print("ERROR: Cannot import backtest_engine.")
    print("Make sure you're running from the Backend directory.")
    sys.exit(1)


# === TIMEFRAME MAPPING ===

TF_MAP = {
    "M1":  mt5.TIMEFRAME_M1,
    "M5":  mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "H1":  mt5.TIMEFRAME_H1,
    "H4":  mt5.TIMEFRAME_H4,
    "D1":  mt5.TIMEFRAME_D1,
}

SPREAD_DEFAULTS = {
    "XAUUSD": 20,
    "XAUUSD.m": 20,
    "GOLD": 20,
    "EURUSD": 10,
    "EURUSD.m": 10,
    "GBPUSD": 12,
    "GBPUSD.m": 12,
    "USDJPY": 10,
    "USDJPY.m": 10,
}


def connect_mt5():
    """Initialize and connect to MT5 terminal."""
    print("\n[1/4] Connecting to MetaTrader 5...")

    if not mt5.initialize():
        error = mt5.last_error()
        print(f"  ERROR: MT5 initialization failed: {error}")
        print("  Make sure MT5 terminal is running and logged in.")
        sys.exit(1)

    account_info = mt5.account_info()
    if account_info:
        print(f"  Connected to: {account_info.server}")
        print(f"  Account: {account_info.login} ({account_info.name})")
        print(f"  Balance: ${account_info.balance:,.2f}")
        print(f"  Leverage: 1:{account_info.leverage}")
    else:
        print("  WARNING: Could not get account info")

    terminal_info = mt5.terminal_info()
    if terminal_info:
        print(f"  Terminal: {terminal_info.name}")
        print(f"  Build: {terminal_info.build}")

    return True


def get_symbol_info(symbol):
    """Get symbol details (point, digits, spread)."""
    info = mt5.symbol_info(symbol)
    if info is None:
        # Try to find the symbol
        symbols = mt5.symbols_get()
        matches = [s for s in symbols if symbol.upper() in s.name.upper()]
        if matches:
            symbol = matches[0].name
            info = mt5.symbol_info(symbol)
            print(f"  Found symbol: {symbol}")

    if info is None:
        print(f"  ERROR: Symbol '{symbol}' not found.")
        print("  Available symbols:")
        all_symbols = mt5.symbols_get()
        for s in all_symbols[:30]:
            print(f"    {s.name}")
        if len(all_symbols) > 30:
            print(f"    ... and {len(all_symbols)-30} more")
        sys.exit(1)

    return symbol, info


def download_bars(symbol, timeframe_str, days):
    """Download historical bars from MT5."""
    tf = TF_MAP.get(timeframe_str.upper(), mt5.TIMEFRAME_M5)

    # Calculate date range
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    print(f"\n[2/4] Downloading {days} days of {timeframe_str} bars for {symbol}...")
    print(f"  From: {start_date.strftime('%Y-%m-%d %H:%M')}")
    print(f"  To:   {end_date.strftime('%Y-%m-%d %H:%M')}")

    # Request bars
    rates = mt5.copy_rates_range(symbol, tf, start_date, end_date)

    if rates is None or len(rates) == 0:
        error = mt5.last_error()
        print(f"  ERROR: Could not download bars: {error}")
        sys.exit(1)

    # Convert to Bar objects
    bars = []
    for r in rates:
        bars.append(Bar(
            timestamp=r['time'],
            open=r['open'],
            high=r['high'],
            low=r['low'],
            close=r['close'],
            volume=int(r['tick_volume']),
        ))

    print(f"  Downloaded {len(bars)} bars")
    if bars:
        first_date = datetime.fromtimestamp(bars[0].timestamp)
        last_date = datetime.fromtimestamp(bars[-1].timestamp)
        print(f"  First: {first_date.strftime('%Y-%m-%d %H:%M')} O={bars[0].open:.5f} H={bars[0].high:.5f} L={bars[0].low:.5f} C={bars[0].close:.5f}")
        print(f"  Last:  {last_date.strftime('%Y-%m-%d %H:%M')} O={bars[-1].open:.5f} H={bars[-1].high:.5f} L={bars[-1].low:.5f} C={bars[-1].close:.5f}")
        price_range = max(b.high for b in bars) - min(b.low for b in bars)
        print(f"  Price range: {min(b.low for b in bars):.5f} - {max(b.high for b in bars):.5f} ({price_range:.5f})")

    return bars


def run(
    symbol: str = "XAUUSD",
    days: int = 30,
    balance: float = 10000.0,
    timeframe: str = "M5",
    spread: int = None,
    cooldown: int = 15,
    output_json: str = None,
):
    """Main backtest entry point."""
    print(f"\n{'='*60}")
    print(f"  DAX V2 AI TRADING SYSTEM - BACKTEST VALIDATOR")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    # Step 1: Connect to MT5
    connect_mt5()

    # Step 2: Get symbol info
    symbol, sym_info = get_symbol_info(symbol)
    point = sym_info.point
    digits = sym_info.digits
    is_gold = "XAU" in symbol.upper() or "GOLD" in symbol.upper()

    print(f"\n  Symbol: {symbol}")
    print(f"  Point: {point} | Digits: {digits}")
    print(f"  Type: {'Gold' if is_gold else 'Forex'}")

    # Auto-detect spread
    if spread is None:
        spread = SPREAD_DEFAULTS.get(symbol, sym_info.spread)
        print(f"  Spread: {spread} pts (auto-detected)")
    else:
        print(f"  Spread: {spread} pts (user-specified)")

    # Step 3: Download bars
    bars = download_bars(symbol, timeframe, days)

    # Step 4: Run backtest
    print(f"\n[3/4] Running backtest simulation...")
    print(f"  Account Balance: ${balance:,.2f}")
    print(f"  Grid Cooldown: {cooldown} bars")

    results = run_backtest(
        bars=bars,
        symbol=symbol,
        timeframe=timeframe,
        account_balance=balance,
        spread_pts=spread,
        grid_cooldown=cooldown,
    )

    # Save JSON output
    if output_json:
        with open(output_json, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"\n[4/4] Results saved to: {output_json}")
    else:
        # Auto-save
        filename = f"backtest_{symbol}_{timeframe}_{days}d_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
        with open(filepath, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"\n[4/4] Results saved to: {filepath}")

    # Shutdown MT5
    mt5.shutdown()
    print(f"\n  MT5 connection closed.")

    return results


def main():
    parser = argparse.ArgumentParser(description="DAX V2 Backtest Validator")
    parser.add_argument("--symbol", default="XAUUSD", help="Trading symbol (default: XAUUSD)")
    parser.add_argument("--days", type=int, default=30, help="Days of history (default: 30)")
    parser.add_argument("--balance", type=float, default=10000.0, help="Starting balance (default: 10000)")
    parser.add_argument("--timeframe", default="M5", choices=["M1", "M5", "M15", "H1", "H4", "D1"],
                        help="Timeframe (default: M5)")
    parser.add_argument("--spread", type=int, default=None, help="Spread in points (auto-detected if omitted)")
    parser.add_argument("--cooldown", type=int, default=15, help="Grid rebuild cooldown in bars (default: 15)")
    parser.add_argument("--output", default=None, help="Output JSON file path")

    args = parser.parse_args()

    run(
        symbol=args.symbol,
        days=args.days,
        balance=args.balance,
        timeframe=args.timeframe,
        spread=args.spread,
        cooldown=args.cooldown,
        output_json=args.output,
    )


if __name__ == "__main__":
    main()
