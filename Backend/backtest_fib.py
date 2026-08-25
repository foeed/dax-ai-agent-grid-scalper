"""
Fibonation Grid EA - Backtest Validator
Ports the Fibonacci Grid logic from MQ5 to Python for validation.
"""

import sys
import os
import io
import argparse
import json
from datetime import datetime, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

try:
    import MetaTrader5 as mt5
except ImportError:
    print("ERROR: MetaTrader5 package not installed.")
    sys.exit(1)

TF_MAP = {
    "M1":  mt5.TIMEFRAME_M1,
    "M5":  mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "H1":  mt5.TIMEFRAME_H1,
}
SPREAD_DEFAULTS = {"XAUUSD": 20, "XAUUSD.m": 20, "GOLD": 20}

# Fibonacci sequence for grid spacing
FIB = [1, 1, 2, 3, 5, 8, 13, 21, 34, 55]


# === EA PARAMETERS (same as Fibonation_Grid.mq5 inputs) ===
INP_FIB_BUY_LEVEL    = 0.30     # Optimized buy zone (between 23.6% and 38.2% Fib)
INP_FIB_SELL_LEVEL   = 0.65     # Optimized sell zone (near 61.8% Fib)
INP_FIB_PROXIMITY    = 0.05
INP_BASE_DISTANCE    = 30       # points (like optimized M5)
INP_MAX_ORDERS       = 2        # Like optimized M5
INP_COOLDOWN_BARS    = 10
INP_MIN_GRID_PTS     = 20
INP_SL_FACTOR        = 8.0      # SL factor for per-order scaling
INP_TP_FACTOR        = 1.4      # TP = SL * 1.4
INP_SL_MIN           = 200
INP_SL_MAX           = 500
INP_TP_MIN           = 150
INP_TP_MAX           = 750
INP_MAX_SL_PCT       = 2.0
INP_TRAIL_BE         = 0.5
INP_TRAIL_TRIGGER    = 1.0
INP_TRAIL_PCT        = 0.4
INP_LOT_SIZE         = 0.01
INP_MAX_DD_PCT       = 30.0
MAGIC                = 770066


class Bar:
    def __init__(self, ts, o, h, l, c):
        self.timestamp = ts
        self.open = o
        self.high = h
        self.low = l
        self.close = c


class PendingOrder:
    def __init__(self, otype, entry, sl, tp, lot):
        self.order_type = otype
        self.entry = entry
        self.sl = sl
        self.tp = tp
        self.lot = lot
        self.open_bar = 0


class Trade:
    def __init__(self, direction, entry, sl, tp, lot, bar):
        self.direction = direction
        self.entry_price = entry
        self.sl = sl
        self.tp = tp
        self.lot = lot
        self.open_bar = bar
        self.close_bar = -1
        self.close_price = 0
        self.pnl = 0
        self.close_reason = ""


def connect_mt5():
    print("\n[1/4] Connecting to MT5...")
    if not mt5.initialize():
        print(f"  ERROR: {mt5.last_error()}")
        sys.exit(1)
    ai = mt5.account_info()
    if ai:
        print(f"  Connected: {ai.server} | Acc: {ai.login} | Bal: ${ai.balance:,.2f} | Lev: 1:{ai.leverage}")
    return True


def download_bars(symbol, tf_str, days):
    tf = TF_MAP.get(tf_str, mt5.TIMEFRAME_M5)
    end = datetime.now()
    start = end - timedelta(days=days)
    print(f"\n[2/4] Downloading {days}d {tf_str} bars for {symbol}...")
    rates = mt5.copy_rates_range(symbol, tf, start, end)
    if rates is None or len(rates) == 0:
        print(f"  ERROR: {mt5.last_error()}")
        sys.exit(1)
    bars = [Bar(r['time'], r['open'], r['high'], r['low'], r['close']) for r in rates]
    print(f"  Downloaded {len(bars)} bars")
    return bars


def calc_fibonacci(bars, i, point):
    """Get daily high/low for current bar's day."""
    day_ts = bars[i].timestamp
    day_str = datetime.fromtimestamp(day_ts).strftime('%Y-%m-%d')
    fib_high = -1
    fib_low = 1e12
    # Look back up to 1440 bars (1 day of M1)
    for j in range(i, max(-1, i - 2000), -1):
        b_day = datetime.fromtimestamp(bars[j].timestamp).strftime('%Y-%m-%d')
        if b_day != day_str:
            break
        fib_high = max(fib_high, bars[j].high)
        fib_low = min(fib_low, bars[j].low)
    if fib_high < 0 or fib_low > 1e11:
        fib_high = bars[i].high
        fib_low = bars[i].low
    fib_range = fib_high - fib_low
    if fib_range <= 0:
        fib_range = bars[i].close * 0.005
        fib_high = bars[i].close + fib_range / 2
        fib_low = bars[i].close - fib_range / 2
    return fib_high, fib_low, fib_range


def calc_signal(mid, fib_low, fib_range, point, spread_pct):
    pos_in_range = (mid - fib_low) / fib_range if fib_range > 0 else 0.5
    pos_in_range = max(0, min(1, pos_in_range))

    buy_zone = INP_FIB_BUY_LEVEL
    sell_zone = INP_FIB_SELL_LEVEL

    if pos_in_range < buy_zone:
        signal = "BUY"
    elif pos_in_range > sell_zone:
        signal = "SELL"
    else:
        signal = "HOLD"

    return signal, 0, 0  # SL/TP computed per-order in grid build


def run_backtest(bars, symbol, balance, spread_pts, point, is_gold):
    state = {
        'balance': balance,
        'trades': [],
        'pending': [],
        'open': [],
        'equity': [balance],
        'grids': 0,
        'fills': 0,
        'last_grid_bar': -999,
        'start_balance': balance,
        'halted': False,
    }

    for i, bar in enumerate(bars):
        if state['halted']:
            continue

        bid = bar.close
        ask = bid + spread_pts * point

        # Daily high/low
        fib_high, fib_low, fib_range = calc_fibonacci(bars, i, point)

        # Signal
        spread_pct = (spread_pts * point) / bid * 100 if bid > 0 else 0
        signal, sl_pts, tp_pts = calc_signal(bid, fib_low, fib_range, point, spread_pct)
        if i < 20:
            print(f"  Bar {i}: pos={((bid-fib_low)/fib_range if fib_range>0 else 0.5):.2f} sig={signal} fr={fib_range:.1f} sl={sl_pts}")

        # Circuit breaker
        eq = state['balance'] + sum(t.pnl for t in state['open'] if t.close_bar >= 0)
        unrealized = 0
        for pos in state['open']:
            if pos.direction == "BUY":
                unrealized += (bid - pos.entry_price) / point * pos.lot
            else:
                unrealized += (pos.entry_price - bid) / point * pos.lot
        eq = state['balance'] + unrealized
        dd_pct = ((balance - eq) / balance) * 100 if balance > 0 else 0
        if dd_pct >= INP_MAX_DD_PCT:
            state['halted'] = True
            print(f"  BREAKER at bar {i}: DD={dd_pct:.1f}%")
            break

        # Manage grid
        live = len(state['open'])
        pend = len(state['pending'])

        # Cancel stale pending orders if signal direction changed (like optimized)
        if signal != "HOLD" and pend > 0:
            expected = "BUY_LIMIT" if signal == "BUY" else "SELL_LIMIT"
            wrong = "SELL_LIMIT" if signal == "BUY" else "BUY_LIMIT"
            state['pending'] = [o for o in state['pending'] if o.order_type != wrong]
            pend = len(state['pending'])

        if signal != "HOLD":
            if live == 0 and pend == 0:
                if (i - state['last_grid_bar']) >= INP_COOLDOWN_BARS:
                    # Build Fibonacci grid with GLOBAL SL/TP (like optimized)
                    placed = 0
                    # Use OPTIMIZED grid logic (same as backtest_engine.py)
                    atr_est = fib_range * 0.06
                    sl_pts = max(INP_SL_MIN, min(INP_SL_MAX, int(round(atr_est * 0.9 * 8.0 / point))))
                    tp_pts = max(INP_TP_MIN, min(INP_TP_MAX, int(round(sl_pts * INP_TP_FACTOR))))
                    grid_pts = max(INP_MIN_GRID_PTS, min(100, int(round(atr_est * 0.3 / point))))
                    # Directional: only build in signal direction
                    if signal == "BUY":
                        # Fibonacci spacing: 1x, 2x (true Fibonacci)
                        for k in range(min(INP_MAX_ORDERS, len(FIB)-1)):
                            fib_dist = grid_pts * FIB[k+1]  # 1x, 2x
                            fib_dist = max(INP_MIN_GRID_PTS, fib_dist)
                            entry = round(bid - fib_dist * point, 2)
                            sl = round(entry - sl_pts * point, 2)
                            tp = round(entry + tp_pts * point, 2)
                            state['pending'].append(PendingOrder("BUY_LIMIT", entry, sl, tp, INP_LOT_SIZE))
                            placed += 1
                    elif signal == "SELL":
                        for k in range(min(INP_MAX_ORDERS, len(FIB)-1)):
                            fib_dist = grid_pts * FIB[k+1]  # 1x, 2x
                            fib_dist = max(INP_MIN_GRID_PTS, fib_dist)
                            entry = round(ask + fib_dist * point, 2)
                            sl = round(entry + sl_pts * point, 2)
                            tp = round(entry - tp_pts * point, 2)
                            state['pending'].append(PendingOrder("SELL_LIMIT", entry, sl, tp, INP_LOT_SIZE))
                            placed += 1
                    if placed > 0:
                        state['grids'] += 1
                        state['last_grid_bar'] = i

        # Fill pending orders
        remaining = []
        for o in state['pending']:
            filled = False
            if o.order_type == "BUY_LIMIT" and bar.low <= o.entry:
                # Open buy
                state['open'].append(Trade("BUY", o.entry, o.sl, o.tp, o.lot, i))
                state['fills'] += 1
                filled = True
            elif o.order_type == "SELL_LIMIT" and bar.high >= o.entry:
                state['open'].append(Trade("SELL", o.entry, o.sl, o.tp, o.lot, i))
                state['fills'] += 1
                filled = True
            if not filled:
                remaining.append(o)
        state['pending'] = remaining

        # Check SL/TP for open positions (with trailing - matches optimized)
        still_open = []
        for pos in state['open']:
            closed = False

            # Trailing: breakeven at 70% of SL, trail at 120% (like optimized)
            sl_dist = abs(pos.entry_price - pos.sl)
            if pos.direction == "BUY":
                profit_now = (bid - pos.entry_price)
                if sl_dist > point * 10:
                    if profit_now >= sl_dist * 0.7 and pos.sl < pos.entry_price:
                        pos.sl = pos.entry_price + point * 5
                    elif profit_now >= sl_dist * 1.2:
                        trail_sl = pos.entry_price + profit_now * 0.5
                        if trail_sl > pos.sl + point * 5:
                            pos.sl = trail_sl
            else:  # SELL
                profit_now = (pos.entry_price - bid)
                if sl_dist > point * 10:
                    if profit_now >= sl_dist * 0.7 and pos.sl > pos.entry_price:
                        pos.sl = pos.entry_price - point * 5
                    elif profit_now >= sl_dist * 1.2:
                        trail_sl = pos.entry_price - profit_now * 0.5
                        if trail_sl < pos.sl - point * 5:
                            pos.sl = trail_sl

            if pos.direction == "BUY":
                if bar.low <= pos.sl:
                    pos.pnl = (pos.sl - pos.entry_price) / point * pos.lot
                    pos.close_reason = "SL"
                    pos.close_bar = i
                    pos.close_price = pos.sl
                    closed = True
                elif bar.high >= pos.tp:
                    pos.pnl = (pos.tp - pos.entry_price) / point * pos.lot
                    pos.close_reason = "TP"
                    pos.close_bar = i
                    pos.close_price = pos.tp
                    closed = True
            else:  # SELL
                if bar.high >= pos.sl:
                    pos.pnl = (pos.entry_price - pos.sl) / point * pos.lot
                    pos.close_reason = "SL"
                    pos.close_bar = i
                    pos.close_price = pos.sl
                    closed = True
                elif bar.low <= pos.tp:
                    pos.pnl = (pos.entry_price - pos.tp) / point * pos.lot
                    pos.close_reason = "TP"
                    pos.close_bar = i
                    pos.close_price = pos.tp
                    closed = True

            if closed:
                state['balance'] += pos.pnl
                state['trades'].append(pos)
            else:
                still_open.append(pos)
        state['open'] = still_open

        # Equity curve
        unreal = 0
        for pos in state['open']:
            if pos.direction == "BUY":
                unreal += (bid - pos.entry_price) / point * pos.lot
            else:
                unreal += (pos.entry_price - bid) / point * pos.lot
        state['equity'].append(state['balance'] + unreal)

        if i % 500 == 0 and i > 0:
            print(f"  Bar {i}/{len(bars)} | Bal: ${state['balance']:,.2f} | Trades: {len(state['trades'])} | Open: {len(state['open'])} | Pend: {len(state['pending'])}")

    # Close remaining at last bar
    if bars and state['open']:
        last_bid = bars[-1].close
        for pos in state['open']:
            if pos.direction == "BUY":
                pnl = (last_bid - pos.entry_price) / point * pos.lot
            else:
                pnl = (pos.entry_price - last_bid) / point * pos.lot
            pos.pnl = pnl
            pos.close_reason = "EOD"
            pos.close_bar = len(bars) - 1
            pos.close_price = last_bid
            state['balance'] += pnl
            state['trades'].append(pos)

    # Results
    total = len(state['trades'])
    winning = [t for t in state['trades'] if t.pnl > 0]
    losing = [t for t in state['trades'] if t.pnl <= 0]
    total_pnl = sum(t.pnl for t in state['trades'])
    wr = len(winning) / total * 100 if total > 0 else 0
    gross_profit = sum(t.pnl for t in winning)
    gross_loss = abs(sum(t.pnl for t in losing))
    pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    peak = balance
    max_dd = 0
    max_dd_pct = 0
    for eq in state['equity']:
        if eq > peak: peak = eq
        dd = peak - eq
        dd_pct = dd / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = dd_pct

    buy_t = [t for t in state['trades'] if t.direction == "BUY"]
    sell_t = [t for t in state['trades'] if t.direction == "SELL"]
    buy_w = [t for t in buy_t if t.pnl > 0]
    sell_w = [t for t in sell_t if t.pnl > 0]
    buy_wr = len(buy_w) / len(buy_t) * 100 if buy_t else 0
    sell_wr = len(sell_w) / len(sell_t) * 100 if sell_t else 0
    sl_c = [t for t in state['trades'] if t.close_reason == "SL"]
    tp_c = [t for t in state['trades'] if t.close_reason == "TP"]
    eod_c = [t for t in state['trades'] if t.close_reason == "EOD"]

    print(f"\n{'='*60}")
    print(f"  FIBONATION GRID BACKTEST - M5")
    print(f"{'='*60}")
    print(f"  Starting: ${balance:,.2f}")
    print(f"  Ending:   ${state['balance']:,.2f}")
    print(f"  P&L:      ${total_pnl:,.2f} ({total_pnl/balance*100:+.1f}%)")
    print(f"  Trades:   {total}")
    print(f"  Win Rate: {wr:.1f}%")
    print(f"  PF:       {pf:.2f}")
    print(f"  Max DD:   ${max_dd:,.2f} ({max_dd_pct:.1f}%)")
    print(f"  Buy:      {len(buy_t)} (WR {buy_wr:.1f}%)")
    print(f"  Sell:     {len(sell_t)} (WR {sell_wr:.1f}%)")
    print(f"  Grids:    {state['grids']}")
    print(f"  Fills:    {state['fills']}")
    print(f"  SL: {len(sl_c)} | TP: {len(tp_c)} | EOD: {len(eod_c)}")
    print(f"{'='*60}")

    return {
        'starting_balance': balance,
        'ending_balance': round(state['balance'], 2),
        'total_pnl': round(total_pnl, 2),
        'total_pnl_pct': round(total_pnl / balance * 100, 2),
        'total_trades': total,
        'win_rate': round(wr, 2),
        'profit_factor': round(pf, 4),
        'max_drawdown_pct': round(max_dd_pct, 2),
        'buy_trades': len(buy_t),
        'sell_trades': len(sell_t),
        'buy_win_rate': round(buy_wr, 2),
        'sell_win_rate': round(sell_wr, 2),
        'grids_built': state['grids'],
        'order_fills': state['fills'],
        'sl_closes': len(sl_c),
        'tp_closes': len(tp_c),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="XAUUSD.m")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--balance", type=float, default=10000.0)
    parser.add_argument("--timeframe", default="M5")
    parser.add_argument("--spread", type=int, default=20)
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  FIBONATION GRID - BACKTEST VALIDATOR")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    connect_mt5()
    symbol, info = args.symbol, mt5.symbol_info(args.symbol)
    if info is None:
        print(f"Symbol {args.symbol} not found")
        sys.exit(1)
    point = info.point
    is_gold = "XAU" in symbol.upper()

    bars = download_bars(symbol, args.timeframe, args.days)
    print(f"\n[3/4] Running Fibonacci grid backtest...")
    results = run_backtest(bars, symbol, args.balance, args.spread, point, is_gold)

    # Save
    fname = f"backtest_fib_{symbol}_{args.timeframe}_{args.days}d_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    fpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), fname)
    with open(fpath, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\n[4/4] Saved: {fpath}")
    mt5.shutdown()


if __name__ == "__main__":
    main()
