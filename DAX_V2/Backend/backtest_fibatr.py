"""
FibATR Trend EA - Backtest Validator
Fibonacci Grid + Adaptive ATR + Trend Filter + Global DD Protection
Mirrors DAX_FibATR_Trend.mq5 logic for validation.
"""

import sys
import os
import io
import argparse
import json
from datetime import datetime, timedelta
import math

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

try:
    import MetaTrader5 as mt5
except ImportError:
    print("ERROR: MetaTrader5 package not installed.")
    sys.exit(1)

TF_MAP = {"M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1}
SPREAD_DEFAULTS = {"XAUUSD": 20, "XAUUSD.m": 20, "GOLD": 20}

# === Fibonacci ratios for grid spacing (same as EA) ===
FIB_RATIOS = [1.0, 1.618, 2.618]  # Fibonacci extension (geometric grid spacing)

# === EA Parameters (same as DAX_FibATR_Trend inputs) ===
INP_TREND_EMA       = 50
INP_TREND_LOOKBACK  = 100
INP_TREND_SLOPE_MIN = 3
INP_ADX_PERIOD      = 14
INP_ADX_THRESHOLD   = 22.0
INP_ATR_PERIOD      = 14
INP_ATR_LOOKBACK    = 20
INP_ATR_SL_FACTOR   = 1.5
INP_ATR_TP_FACTOR   = 2.5
INP_SL_MIN          = 200
INP_SL_MAX          = 600
INP_TP_MIN          = 300
INP_TP_MAX          = 900
INP_MAX_ORDERS      = 2
INP_COOLDOWN_BARS   = 10
INP_MIN_GRID_PTS    = 20
INP_MAX_GRID_PTS    = 500
INP_BUY_ZONE        = 0.30
INP_SELL_ZONE       = 0.65
INP_MAX_SPREAD_PCT  = 0.15
INP_TRAIL_BE        = 0.7
INP_TRAIL_TRIGGER   = 1.2
INP_TRAIL_PCT       = 0.5
INP_LOT_SIZE        = 0.01
INP_MAX_GLOBAL_DD   = 15.0
MAGIC               = 770077
# Grid base: daily_range * 0.018 (same as optimized M5 atr*0.3)
GRID_BASE_MULT = 0.018

TREND_UP = 1
TREND_DOWN = -1
TREND_FLAT = 0


class Bar:
    def __init__(self, ts, o, h, l, c):
        self.timestamp = ts; self.open = o; self.high = h; self.low = l; self.close = c


class PendingOrder:
    def __init__(self, otype, entry, sl, tp, lot, bar):
        self.order_type = otype; self.entry = entry; self.sl = sl; self.tp = tp; self.lot = lot; self.bar_index = bar


class Trade:
    def __init__(self, direction, entry, sl, tp, lot, bar):
        self.direction = direction; self.entry_price = entry; self.sl = sl; self.tp = tp; self.lot = lot
        self.open_bar = bar; self.close_bar = -1; self.close_price = 0; self.pnl = 0; self.close_reason = ""


def connect_mt5():
    print("\n[1/5] Connecting to MT5...")
    if not mt5.initialize():
        print(f"  ERROR: {mt5.last_error()}"); sys.exit(1)
    ai = mt5.account_info()
    if ai:
        print(f"  Connected: {ai.server} | Acc: {ai.login} | Bal: ${ai.balance:,.2f} | Lev: 1:{ai.leverage}")
    return True


def download_bars(symbol, tf_str, days):
    tf = TF_MAP.get(tf_str, mt5.TIMEFRAME_M5)
    end = datetime.now(); start = end - timedelta(days=days)
    print(f"\n[2/5] Downloading {days}d {tf_str} bars for {symbol}...")
    rates = mt5.copy_rates_range(symbol, tf, start, end)
    if rates is None or len(rates) == 0:
        print(f"  ERROR: {mt5.last_error()}"); sys.exit(1)
    bars = [Bar(r['time'], r['open'], r['high'], r['low'], r['close']) for r in rates]
    print(f"  Downloaded {len(bars)} {tf_str} bars")
    return bars


# === INDICATOR COMPUTATIONS ===

def compute_ema(data, period):
    """Compute EMA from list of values (most recent first)."""
    if len(data) < period:
        return data[0] if data else 0
    alpha = 2.0 / (period + 1)
    ema = data[period - 1]  # oldest
    for i in range(period - 2, -1, -1):
        ema = data[i] * alpha + ema * (1 - alpha)
    return ema


def compute_adx(d1_bars, period=14):
    """Compute ADX(period) from D1 bars (most recent first)."""
    if len(d1_bars) < period + 10:
        return 25.0  # default

    n = min(len(d1_bars), period * 3)
    highs = [b.high for b in d1_bars[:n]]
    lows = [b.low for b in d1_bars[:n]]
    closes = [b.close for b in d1_bars[:n]]

    tr_sum = 0; pdm_sum = 0; mdm_sum = 0
    prev_h = highs[n-1]; prev_l = lows[n-1]; prev_c = closes[n-1]

    for j in range(n - 2, n - 2 - period, -1):
        if j < 0: break
        tr = max(highs[j] - lows[j], abs(highs[j] - prev_c), abs(lows[j] - prev_c))
        tr_sum += tr
        up = highs[j] - prev_h; dn = prev_l - lows[j]
        pdm = up if (up > dn and up > 0) else 0
        mdm = dn if (dn > up and dn > 0) else 0
        pdm_sum += pdm; mdm_sum += mdm
        prev_h, prev_l, prev_c = highs[j], lows[j], prev_c

    atr14 = tr_sum / period
    if atr14 <= 0: return 20.0

    dip = 100.0 * (pdm_sum / period) / atr14
    dim = 100.0 * (mdm_sum / period) / atr14
    di_sum = dip + dim
    if di_sum <= 0: return 20.0

    dx = 100.0 * abs(dip - dim) / di_sum
    return dx


def compute_atr(m5_bars, period=14, lookback=20):
    """Compute ATR(period) from M5 bars (most recent first). Returns ATR in points."""
    n = min(len(m5_bars), lookback + 2)
    if n < period + 2:
        return 200.0

    highs = [b.high for b in m5_bars[:n]]
    lows = [b.low for b in m5_bars[:n]]
    closes = [b.close for b in m5_bars[:n]]

    tr_sum = 0
    prev_c = closes[period]
    for j in range(min(period, n - 1)):
        tr = max(highs[j] - lows[j], abs(highs[j] - prev_c), abs(lows[j] - prev_c))
        tr_sum += tr
        prev_c = closes[j]

    atr_price = tr_sum / period
    atr_pts = atr_price / 0.01  # gold point
    atr_pts = max(50, min(800, atr_pts))
    return atr_pts


def get_trend(d1_bars):
    """Compute D1 EMA(50) + ADX(14) and return trend state."""
    n = min(len(d1_bars), INP_TREND_LOOKBACK)
    if n < INP_TREND_EMA + 10:
        return TREND_FLAT, 0, 20.0

    closes = [b.close for b in d1_bars[:n]]
    ema50 = compute_ema(closes, INP_TREND_EMA)

    # EMA slope over ~3 bars
    ema3_data = closes[:min(n, INP_TREND_EMA + 4)]
    ema3 = compute_ema(ema3_data, INP_TREND_EMA)
    slope = (ema50 - ema3) / 3.0

    adx = compute_adx(d1_bars[:n], INP_ADX_PERIOD)

    trending = adx > INP_ADX_THRESHOLD
    above = closes[0] > ema50
    slope_pts = slope / 0.01  # gold point

    if trending and above and slope_pts > INP_TREND_SLOPE_MIN:
        trend = TREND_UP
    elif trending and not above and slope_pts < -INP_TREND_SLOPE_MIN:
        trend = TREND_DOWN
    else:
        trend = TREND_FLAT

    return trend, ema50, adx


def calc_daily_range(m5_bars, i):
    """Get daily high/low for current bar's day (looks back within same day)."""
    day_ts = m5_bars[i].timestamp
    day_str = datetime.fromtimestamp(day_ts).strftime('%Y-%m-%d')
    dhigh = -1; dlow = 1e12
    for j in range(i, -1, -1):
        if datetime.fromtimestamp(m5_bars[j].timestamp).strftime('%Y-%m-%d') != day_str:
            break
        dhigh = max(dhigh, m5_bars[j].high)
        dlow = min(dlow, m5_bars[j].low)
    if dhigh < 0 or dlow > 1e11:
        dhigh = m5_bars[i].high; dlow = m5_bars[i].low
    return dhigh, dlow, dhigh - dlow


# === BACKTEST ENGINE ===

def run_backtest(m5_bars, d1_bars, symbol, balance, spread_pts, point):
    state = {
        'balance': balance, 'trades': [], 'pending': [], 'open': [],
        'equity': [balance], 'peak_equity': balance,
        'grids': 0, 'fills': 0, 'last_grid_bar': -999,
        'halted': False, 'halt_reason': '',
    }

    trend = TREND_FLAT; ema50 = 0; adx = 0; last_trend_calc = -999
    atr_pts = 200.0; last_atr_calc = -999

    for i, bar in enumerate(m5_bars):
        if state['halted']:
            continue

        bid = bar.close
        ask = bid + spread_pts * point

        # Recalculate trend every 5 bars (like UpdateSec)
        if i - last_trend_calc >= 5 * 12:  # ~5 seconds = ~12 M5 bars... roughly
            # Find D1 bars up to this M5 bar's date
            bar_date = datetime.fromtimestamp(bar.timestamp)
            d1_day = bar_date.strftime('%Y-%m-%d')
            # Use pre-computed D1 bars up to this day
            d1_idx = -1
            for di, dbar in enumerate(d1_bars):
                dbar_date = datetime.fromtimestamp(dbar.timestamp).strftime('%Y-%m-%d')
                if dbar_date <= d1_day:
                    d1_idx = di
                    break
            if d1_idx >= 0:
                trend, ema50, adx = get_trend(d1_bars[d1_idx:])
            else:
                trend = TREND_FLAT
            last_trend_calc = i

        # Recalculate ATR
        if i - last_atr_calc >= 10:
            if i >= INP_ATR_LOOKBACK:
                atr_pts = compute_atr(m5_bars[i:])
            else:
                atr_pts = 200.0
            last_atr_calc = i

        # Daily range
        dhigh, dlow, daily_range = calc_daily_range(m5_bars, i)
        if daily_range <= 0:
            daily_range = bid * 0.005
            dhigh = bid + daily_range / 2
            dlow = bid - daily_range / 2

        # Signal
        mid = (bid + ask) / 2
        pos_in_range = max(0, min(1, (mid - dlow) / daily_range if daily_range > 0 else 0.5))
        spread_pct = (spread_pts * point) / mid * 100 if mid > 0 else 0

        base_signal = "HOLD"
        if spread_pct < INP_MAX_SPREAD_PCT:
            if pos_in_range < INP_BUY_ZONE:
                base_signal = "BUY"
            elif pos_in_range > INP_SELL_ZONE:
                base_signal = "SELL"

        # Trend filter (like EA - suppresses counter-trend trades)
        signal = base_signal
        if trend == TREND_UP and base_signal == "SELL":
            signal = "HOLD"
        elif trend == TREND_DOWN and base_signal == "BUY":
            signal = "HOLD"

        # SL/TP (same as optimized M5: daily_range * 0.432 / point)
        sl_pts = max(INP_SL_MIN, min(INP_SL_MAX, int(round(daily_range * 0.432 / point))))
        tp_pts = max(INP_TP_MIN, min(INP_TP_MAX, int(round(sl_pts * 1.4))))
        # R:R floor after clamping
        min_tp = int(round(sl_pts * 1.4))
        tp_pts = max(min_tp, min(INP_TP_MAX, tp_pts))

        # Grid base from daily range (same as optimized atr*0.3)
        grid_base_pts = max(INP_MIN_GRID_PTS, min(INP_MAX_GRID_PTS, int(round(daily_range * GRID_BASE_MULT / point))))

        # Global DD check
        unreal = 0
        for pos in state['open']:
            if pos.direction == "BUY":
                unreal += (bid - pos.entry_price) / point * pos.lot
            else:
                unreal += (pos.entry_price - bid) / point * pos.lot
        eq = state['balance'] + unreal
        if eq > state['peak_equity']:
            state['peak_equity'] = eq
        dd_pct = ((state['peak_equity'] - eq) / state['peak_equity']) * 100 if state['peak_equity'] > 0 else 0
        if dd_pct >= INP_MAX_GLOBAL_DD:
            state['halted'] = True
            state['halt_reason'] = f"DD={dd_pct:.1f}% (limit={INP_MAX_GLOBAL_DD}%)"
            print(f"  !!! BREAKER at bar {i}: {state['halt_reason']}")
            break

        # Manage grid (identical to backtest_engine.py)
        live = len(state['open'])
        pend = len(state['pending'])

        # Cancel stale pending if signal direction changed
        if pend > 0 and signal != "HOLD":
            expected = "BUY_LIMIT" if signal == "BUY" else "SELL_LIMIT"
            wrong = "SELL_LIMIT" if signal == "BUY" else "BUY_LIMIT"
            state['pending'] = [o for o in state['pending'] if o.order_type != wrong]
            pend = len(state['pending'])

        if live == 0 and pend == 0 and signal != "HOLD":
            if (i - state['last_grid_bar']) >= INP_COOLDOWN_BARS:
                placed = 0
                if signal == "BUY":
                    for k in range(1, INP_MAX_ORDERS + 1):
                        dist = grid_base_pts * k
                        dist = max(INP_MIN_GRID_PTS, dist)
                        entry = round(bid - dist * point, 2)
                        sl = round(entry - sl_pts * point, 2)
                        tp = round(entry + tp_pts * point, 2)
                        state['pending'].append(PendingOrder("BUY_LIMIT", entry, sl, tp, INP_LOT_SIZE, i))
                        placed += 1
                elif signal == "SELL":
                    for k in range(1, INP_MAX_ORDERS + 1):
                        dist = grid_base_pts * k
                        dist = max(INP_MIN_GRID_PTS, dist)
                        entry = round(ask + dist * point, 2)
                        sl = round(entry + sl_pts * point, 2)
                        tp = round(entry - tp_pts * point, 2)
                        state['pending'].append(PendingOrder("SELL_LIMIT", entry, sl, tp, INP_LOT_SIZE, i))
                        placed += 1
                if placed > 0:
                    state['grids'] += 1
                    state['last_grid_bar'] = i

        # Fill pending orders
        remaining = []
        for o in state['pending']:
            filled = False
            if o.order_type == "BUY_LIMIT" and bar.low <= o.entry:
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

        # Check SL/TP + Trailing
        still_open = []
        for pos in state['open']:
            closed = False

            # Trailing
            sl_dist = abs(pos.entry_price - pos.sl)
            if pos.direction == "BUY":
                profit_now = (bid - pos.entry_price)
                if sl_dist > point * 10:
                    if profit_now >= sl_dist * INP_TRAIL_BE and pos.sl < pos.entry_price:
                        pos.sl = pos.entry_price + point * 5
                    elif profit_now >= sl_dist * INP_TRAIL_TRIGGER:
                        trail_sl = pos.entry_price + profit_now * INP_TRAIL_PCT
                        if trail_sl > pos.sl + point * 5:
                            pos.sl = trail_sl
            else:
                profit_now = (pos.entry_price - bid)
                if sl_dist > point * 10:
                    if profit_now >= sl_dist * INP_TRAIL_BE and pos.sl > pos.entry_price:
                        pos.sl = pos.entry_price - point * 5
                    elif profit_now >= sl_dist * INP_TRAIL_TRIGGER:
                        trail_sl = pos.entry_price - profit_now * INP_TRAIL_PCT
                        if trail_sl < pos.sl - point * 5:
                            pos.sl = trail_sl

            # SL/TP
            if pos.direction == "BUY":
                if bar.low <= pos.sl:
                    pos.pnl = (pos.sl - pos.entry_price) / point * pos.lot
                    pos.close_reason = "SL"
                    pos.close_bar = i; pos.close_price = pos.sl; closed = True
                elif bar.high >= pos.tp:
                    pos.pnl = (pos.tp - pos.entry_price) / point * pos.lot
                    pos.close_reason = "TP"
                    pos.close_bar = i; pos.close_price = pos.tp; closed = True
            else:
                if bar.high >= pos.sl:
                    pos.pnl = (pos.entry_price - pos.sl) / point * pos.lot
                    pos.close_reason = "SL"
                    pos.close_bar = i; pos.close_price = pos.sl; closed = True
                elif bar.low <= pos.tp:
                    pos.pnl = (pos.entry_price - pos.tp) / point * pos.lot
                    pos.close_reason = "TP"
                    pos.close_bar = i; pos.close_price = pos.tp; closed = True

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

        # Stale order cleanup (beyond 500 pts from current price)
        midpt = (bid + ask) / 2
        max_dist = 500 * point
        state['pending'] = [o for o in state['pending'] if abs(o.entry - midpt) < max_dist]

        if i % 500 == 0 and i > 0:
            print(f"  Bar {i}/{len(m5_bars)} | Bal: ${state['balance']:,.2f} | "
                  f"Trades: {len(state['trades'])} | DD: {dd_pct:.1f}%")

    # Close remaining at last bar
    if m5_bars and state['open']:
        last_bid = m5_bars[-1].close
        for pos in state['open']:
            if pos.direction == "BUY":
                pnl = (last_bid - pos.entry_price) / point * pos.lot
            else:
                pnl = (pos.entry_price - last_bid) / point * pos.lot
            pos.pnl = pnl; pos.close_reason = "EOD"
            pos.close_bar = len(m5_bars) - 1; pos.close_price = last_bid
            state['balance'] += pnl
            state['trades'].append(pos)

    # Stats
    total = len(state['trades'])
    winning = [t for t in state['trades'] if t.pnl > 0]
    losing = [t for t in state['trades'] if t.pnl <= 0]
    total_pnl = sum(t.pnl for t in state['trades'])
    wr = len(winning) / total * 100 if total > 0 else 0
    gross_profit = sum(t.pnl for t in winning)
    gross_loss = abs(sum(t.pnl for t in losing))
    pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    peak = balance; max_dd = 0; max_dd_pct = 0
    for eq in state['equity']:
        if eq > peak: peak = eq
        dd = peak - eq; dd_pct_v = dd / peak * 100 if peak > 0 else 0
        if dd > max_dd: max_dd = dd; max_dd_pct = dd_pct_v

    buy_t = [t for t in state['trades'] if t.direction == "BUY"]
    sell_t = [t for t in state['trades'] if t.direction == "SELL"]
    buy_w = [t for t in buy_t if t.pnl > 0]; sell_w = [t for t in sell_t if t.pnl > 0]
    buy_wr = len(buy_w) / len(buy_t) * 100 if buy_t else 0
    sell_wr = len(sell_w) / len(sell_t) * 100 if sell_t else 0
    sl_c = [t for t in state['trades'] if t.close_reason == "SL"]
    tp_c = [t for t in state['trades'] if t.close_reason == "TP"]

    print(f"\n{'='*60}")
    print(f"  FibATR TREND BACKTEST - {symbol}")
    print(f"{'='*60}")
    print(f"  Starting:  ${balance:>12,.2f}")
    print(f"  Ending:    ${state['balance']:>12,.2f}")
    print(f"  P&L:       ${total_pnl:>12,.2f} ({total_pnl/balance*100:+.1f}%)")
    print(f"  Peak Eq:   ${state['peak_equity']:>12,.2f}")
    print(f"  Trades:    {total:>12}")
    print(f"  Win Rate:  {wr:>11.1f}%")
    print(f"  PF:        {pf:>12.2f}")
    print(f"  Max DD:    {max_dd_pct:>11.1f}% ({'BREACHED' if max_dd_pct >= INP_MAX_GLOBAL_DD else 'ok'})")
    print(f"  Buy:       {len(buy_t):>8}  WR={buy_wr:.1f}%")
    print(f"  Sell:      {len(sell_t):>8}  WR={sell_wr:.1f}%")
    print(f"  Grids:     {state['grids']:>12}")
    print(f"  SL: {len(sl_c)} | TP: {len(tp_c)}")
    if state['halt_reason']:
        print(f"  HALTED:    {state['halt_reason']}")
    print(f"{'='*60}\n")

    return {
        'starting_balance': balance,
        'ending_balance': round(state['balance'], 2),
        'peak_equity': round(state['peak_equity'], 2),
        'total_pnl': round(total_pnl, 2),
        'total_pnl_pct': round(total_pnl / balance * 100, 2),
        'total_trades': total,
        'win_rate': round(wr, 2),
        'profit_factor': round(pf, 4),
        'max_drawdown_pct': round(max_dd_pct, 2),
        'buy_trades': len(buy_t), 'sell_trades': len(sell_t),
        'buy_win_rate': round(buy_wr, 2), 'sell_win_rate': round(sell_wr, 2),
        'grids_built': state['grids'], 'order_fills': state['fills'],
        'sl_closes': len(sl_c), 'tp_closes': len(tp_c),
        'halt_reason': state['halt_reason'],
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
    print(f"  FibATR TREND - BACKTEST VALIDATOR")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    connect_mt5()

    info = mt5.symbol_info(args.symbol)
    if info is None:
        print(f"  Symbol {args.symbol} not found"); sys.exit(1)
    point = info.point

    # Download M5 + D1 bars
    m5_bars = download_bars(args.symbol, args.timeframe, args.days)
    d1_bars = download_bars(args.symbol, "D1", max(args.days, 200))

    print(f"\n[3/5] Running FibATR Trend backtest ({args.days}d, ${args.balance:,.0f})...")
    results = run_backtest(m5_bars, d1_bars, args.symbol, args.balance, args.spread, point)

    fname = f"backtest_fibatr_{args.symbol}_{args.timeframe}_{args.days}d_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    fpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), fname)
    with open(fpath, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"[4/5] Saved: {fpath}")
    mt5.shutdown()
    print(f"[5/5] MT5 closed.")


if __name__ == "__main__":
    main()
