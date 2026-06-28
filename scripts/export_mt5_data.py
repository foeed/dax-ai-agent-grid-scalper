"""Export MT5 historical data to CSV for Docker backtesting."""
import MetaTrader5 as mt5
from datetime import datetime, timezone
from pathlib import Path
import sys

OUTPUT_DIR = Path("data")

SYMBOLS = [
    ("EURUSD.m", ["M15", "H1"]),    # M15 entry + H1 trend
    ("XAUUSD.m", ["M5", "M15"]),    # M5 entry + M15 trend
]

DAYS = 180  # 6 months


def export_symbol(symbol: str, timeframes: list[str], days: int) -> dict:
    """Export MT5 data to CSV files."""
    results = {}
    for tf_str in timeframes:
        tf_map = {
            "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30,
            "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1,
        }
        tf = tf_map.get(tf_str)
        if tf is None:
            print(f"  Unknown timeframe: {tf_str}")
            continue

        tf_minutes = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
        bars = (days * 24 * 60) // tf_minutes.get(tf_str, 15)

        if not mt5.symbol_select(symbol, True):
            print(f"  Symbol not available: {symbol}")
            continue

        rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
        if rates is None or len(rates) == 0:
            code, msg = mt5.last_error()
            print(f"  Failed [{code}]: {msg}")
            continue

        # Write CSV
        safe_name = symbol.replace(".", "_").replace("/", "_")
        fname = f"{safe_name}_{tf_str}_{days}d.csv"
        fpath = OUTPUT_DIR / fname
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        import csv
        with fpath.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["time", "open", "high", "low", "close", "tick_volume"])
            writer.writeheader()
            for r in rates:
                ts = datetime.fromtimestamp(int(r["time"]), timezone.utc)
                writer.writerow({
                    "time": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "open": f"{float(r['open']):.6f}",
                    "high": f"{float(r['high']):.6f}",
                    "low": f"{float(r['low']):.6f}",
                    "close": f"{float(r['close']):.6f}",
                    "tick_volume": str(int(r["tick_volume"])),
                })

        results[tf_str] = str(fpath)
        print(f"  {tf_str}: {len(rates)} bars -> {fpath}")
    return results


def main():
    print("=" * 60)
    print("  MT5 DATA EXPORTER (for Docker backtesting)")
    print(f"  Period: {DAYS} days")
    print("=" * 60)

    if not mt5.initialize():
        code, msg = mt5.last_error()
        print(f"MT5 init failed [{code}]: {msg}")
        return 1

    all_results = {}
    for symbol, timeframes in SYMBOLS:
        print(f"\n--- {symbol} ---")
        all_results[symbol] = export_symbol(symbol, timeframes, DAYS)

    mt5.shutdown()

    print("\n" + "=" * 60)
    print("  Export complete. Copy CSV files to Docker volume:")
    for sym, tf_files in all_results.items():
        for tf, fpath in tf_files.items():
            print(f"  {fpath}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
