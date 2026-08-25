"""
Multi-Symbol Backtest Runner
Tests DAX_M5_Standalone strategy on every tradeable symbol
"""
import sys, os, io, json, time
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import MetaTrader5 as mt5
except ImportError:
    print("ERROR: pip install MetaTrader5"); sys.exit(1)

from app.services.backtest_engine import run_backtest, Bar

TF_MAP = {"M5": mt5.TIMEFRAME_M5, "H1": mt5.TIMEFRAME_H1, "M15": mt5.TIMEFRAME_M15}

def test_symbol(symbol, balance, days, tf_str, spread_override=None):
    print(f"\n{'─'*60}")
    print(f"  TEST: {symbol} | {tf_str} | {days}d | ${balance:,.0f}")
    print(f"{'─'*60}")

    info = mt5.symbol_info(symbol)
    if info is None:
        print(f"  SKIP: symbol not found")
        return None

    spread = spread_override if spread_override is not None else info.spread
    if spread <= 0:
        spread = 30  # fallback
    print(f"  Spread: {spread} pts | Point: {info.point} | Digits: {info.digits}")

    tf = TF_MAP.get(tf_str, mt5.TIMEFRAME_M5)
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, days * 288 + 100)  # M5 bars per day
    if rates is None or len(rates) == 0:
        print(f"  SKIP: no bars available")
        return None

    bars = [Bar(r['time'], r['open'], r['high'], r['low'], r['close']) for r in rates]
    bars = bars[-days * 288:] if len(bars) > days * 288 else bars
    print(f"  Bars: {len(bars)} | Range: {min(b.low for b in bars):.2f} - {max(b.high for b in bars):.2f}")

    try:
        results = run_backtest(bars=bars, symbol=symbol, timeframe=tf_str,
                               account_balance=balance, spread_pts=spread, grid_cooldown=10)
    except Exception as e:
        print(f"  ERROR: {e}")
        return None

    pnl_pct = results['total_pnl_pct']
    wr = results['win_rate']
    pf = results['profit_factor']
    dd = results['max_drawdown_pct']
    trades = results['total_trades']
    tp_close = results['tp_closes']
    sl_close = results['sl_closes']

    rating = "⭐ EXCELLENT" if pnl_pct > 30 and wr > 65 and pf > 2 else \
             "✓ GOOD" if pnl_pct > 10 and wr > 55 and pf > 1.2 else \
             "~ OK" if pnl_pct > -5 else \
             "✗ BAD"

    print(f"  PnL:{pnl_pct:+.1f}% | WR:{wr:.1f}% | PF:{pf:.2f} | DD:{dd:.1f}% | T:{trades} | {rating}")
    return {'symbol': symbol, 'pnl_pct': round(pnl_pct,2), 'wr': round(wr,2), 'pf': round(pf,4),
            'dd': round(dd,2), 'trades': trades, 'tp': tp_close, 'sl': sl_close, 'rating': rating}

def main():
    mt5.initialize()
    ai = mt5.account_info()
    print(f"Connected: {ai.server} | Balance: ${ai.balance:,.2f} | Lev: 1:{ai.leverage}")

    # Test list: metals, crypto, indices
    tests = [
        ("XAUUSD.m",  10_000, 30, "M5", 30),   # Gold - $10k
        ("XAUUSD.m",  10_000, 90, "M5", 30),   # Gold - $10k, 90d
        ("XAGUSD.m",  10_000, 30, "M5", 50),   # Silver
        ("BTCUSD.m",  10_000, 30, "M5", 900),  # Bitcoin - wide spread
        ("ETHUSD.m",  10_000, 30, "M5", 50),   # Ethereum
        ("US30.std",  10_000, 30, "M5", 200),  # Dow Jones
    ]

    results = []
    for sym, bal, days, tf, spread in tests:
        r = test_symbol(sym, bal, days, tf, spread)
        if r: results.append(r)
        time.sleep(0.5)

    # Summary
    print(f"\n{'='*80}")
    print(f"  FINAL RANKING - DAX_M5_STANDALONE STRATEGY")
    print(f"{'='*80}")
    sorted_r = sorted(results, key=lambda x: x['pnl_pct'], reverse=True)
    print(f"  {'Symbol':<15s} {'PnL':>8s} {'WR':>7s} {'PF':>7s} {'DD':>6s} {'Trades':>8s} {'Rating':<20s}")
    print(f"  {'─'*80}")
    for r in sorted_r:
        print(f"  {r['symbol']:<15s} {r['pnl_pct']:+7.1f}% {r['wr']:6.1f}% {r['pf']:7.2f} {r['dd']:5.1f}% {r['trades']:8d} {r['rating']:<20s}")

    # Save
    fname = f"multi_backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(fname, 'w') as f: json.dump(sorted_r, f, indent=2)
    print(f"\n  Saved: {fname}")
    mt5.shutdown()

if __name__ == "__main__":
    main()
