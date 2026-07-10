"""
DAX V2 Backtest Engine - Full Grid Simulation
Replicates the exact signal logic from scalp.py + EA grid management.
Runs bar-by-bar on historical OHLC data.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import math


# === DATA STRUCTURES ===

@dataclass
class Bar:
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: int = 0


@dataclass
class PendingOrder:
    order_type: str   # "BUY_LIMIT" or "SELL_LIMIT"
    entry: float
    sl: float
    tp: float
    lot: float
    bar_index: int     # which bar placed this order


@dataclass
class OpenPosition:
    direction: str     # "BUY" or "SELL"
    entry_price: float
    sl: float
    tp: float
    lot: float
    open_bar: int
    close_bar: int = -1
    close_price: float = 0.0
    pnl: float = 0.0
    close_reason: str = ""  # "SL", "TP", "TRAIL", "BREAKEVEN"
    signal: str = ""


@dataclass
class TradeRecord:
    direction: str
    entry_price: float
    exit_price: float
    entry_bar: int
    exit_bar: int
    lot: float
    pnl: float
    close_reason: str
    signal: str
    sl: float
    tp: float
    timestamp: float = 0.0


@dataclass
class GridState:
    pending_orders: List[PendingOrder] = field(default_factory=list)
    open_positions: List[OpenPosition] = field(default_factory=list)
    balance: float = 10000.0
    equity_curve: List[float] = field(default_factory=list)
    trades: List[TradeRecord] = field(default_factory=list)
    last_grid_bar: int = -999
    consecutive_failures: int = 0


# === HFT MULTIPLIERS (matches scalp.py exactly) ===

HFT = {
    "M1":  {"grid_factor": 0.20, "sl_ratio": 0.5, "tp_ratio": 1.5, "max_orders": 10},
    "M5":  {"grid_factor": 0.30, "sl_ratio": 0.6, "tp_ratio": 1.5, "max_orders": 8},
    "M15": {"grid_factor": 0.35, "sl_ratio": 0.7, "tp_ratio": 1.5, "max_orders": 6},
    "H1":  {"grid_factor": 0.45, "sl_ratio": 0.8, "tp_ratio": 1.5, "max_orders": 5},
}


# === SIGNAL GENERATION (exact copy of scalp.py logic) ===

def generate_signal(
    symbol: str,
    bid: float,
    ask: float,
    spread_pts: float,
    daily_high: float,
    daily_low: float,
    account_balance: float,
    timeframe: str = "M5",
) -> dict:
    """Replicate scalp.py signal generation exactly."""

    mid = (bid + ask) / 2 if ask > 0 else bid
    if mid <= 0:
        mid = 1.0

    is_gold = "XAU" in symbol.upper() or "GOLD" in symbol.upper()
    point = 0.01 if is_gold else 0.00001

    daily_range = daily_high - daily_low
    if daily_range <= 0:
        daily_range = mid * 0.005

    volatility = daily_range / mid if mid > 0 else 0.005
    spread_pct = (spread_pts * point) / mid * 100 if mid > 0 else 0

    pos_in_range = (mid - daily_low) / daily_range if daily_range > 0 else 0.5

    tf = HFT.get(timeframe, HFT["M5"])

    atr_estimate = daily_range * 0.15 * tf["grid_factor"]
    if timeframe == "M1":    atr_estimate = daily_range * 0.03
    elif timeframe == "M5":  atr_estimate = daily_range * 0.06
    elif timeframe == "M15": atr_estimate = daily_range * 0.10
    elif timeframe == "H1":  atr_estimate = daily_range * 0.18

    # Signal generation
    signal = "BUY"
    confidence = 0.6
    risk_score = 0.30

    if spread_pct < 0.15:
        if pos_in_range < 0.35:
            signal = "BUY"
            confidence = max(0.55, 0.85 - abs(pos_in_range - 0.15) * 2)
        elif pos_in_range > 0.65:
            signal = "SELL"
            confidence = max(0.55, 0.85 - abs(pos_in_range - 0.85) * 2)
        else:
            signal = "HOLD"  # No trade in middle range - choppy zone
            confidence = 0.40
            risk_score = 0.25
    else:
        signal = "HOLD"  # Wide spread = no trade
        confidence = 0.30
        risk_score = 0.60

    confidence = max(0.10, min(0.95, confidence))
    risk_score = max(0.10, min(0.95, risk_score))

    # SL/TP
    vol_mult = 1.0 + volatility * 15
    sl_distance_price = atr_estimate * tf["sl_ratio"] * vol_mult
    min_sl = mid * 0.0003
    sl_distance_price = max(min_sl, sl_distance_price)
    tp_distance_price = sl_distance_price * tf["tp_ratio"]

    sl_pts = sl_distance_price / point
    tp_pts = tp_distance_price / point

    if is_gold:
        sl_pts = max(200, min(500, sl_pts))
        tp_pts = max(150, min(750, tp_pts))
        # Ensure R:R ratio holds after clamping (tp must be at least sl * ratio)
        tp_pts = max(tp_pts, int(sl_pts * tf["tp_ratio"]))
        tp_pts = min(750, tp_pts)  # Re-cap
    else:
        sl_pts = max(20, min(200, sl_pts))
        tp_pts = max(15, min(300, tp_pts))
        tp_pts = max(tp_pts, int(sl_pts * tf["tp_ratio"]))
        tp_pts = min(300, tp_pts)

    # Lot size
    target_lot = 0.01
    if is_gold:
        if account_balance <= 100:    target_lot = 0.01
        elif account_balance <= 500:  target_lot = 0.01
        elif account_balance <= 2000: target_lot = 0.02
        else: target_lot = 0.02
    else:
        if account_balance <= 100:    target_lot = 0.01
        elif account_balance <= 500:  target_lot = 0.02
        elif account_balance <= 2000: target_lot = 0.03
        else: target_lot = 0.05

    # Grid
    min_grid_pts = 20 if is_gold else 5
    grid_spacing_price = atr_estimate * tf["grid_factor"]
    grid_spacing_pts = int(grid_spacing_price / point)
    grid_spacing_pts = max(min_grid_pts, min(100, grid_spacing_pts))

    if is_gold:
        risk_per_position = sl_pts * 0.01
        max_orders_cap = 3  # Gold: max 3 orders per side
    else:
        risk_per_position = 0.01 * (sl_pts / 10.0)
        max_orders_cap = min(tf["max_orders"], 10)

    max_risk_per_side = account_balance * 0.02
    max_by_risk = int(max_risk_per_side / risk_per_position) if risk_per_position > 0 else 3
    max_orders = min(max_orders_cap, max_by_risk)
    base_orders = max(2, max_orders)

    buy_orders = base_orders
    sell_orders = base_orders
    if signal == "BUY":
        buy_orders = max_orders
        sell_orders = 0  # No counter-trend orders
    elif signal == "SELL":
        sell_orders = max_orders
        buy_orders = 0  # No counter-trend orders
    else:
        buy_orders = 0
        sell_orders = 0

    return {
        "signal": signal,
        "lot_size": target_lot,
        "sl_pts": sl_pts,
        "tp_pts": tp_pts,
        "grid_spacing_pts": grid_spacing_pts,
        "buy_orders": buy_orders,
        "sell_orders": sell_orders,
        "is_gold": is_gold,
        "point": point,
        "confidence": confidence,
        "risk_level": "LOW" if risk_score < 0.35 else "MEDIUM" if risk_score < 0.60 else "HIGH",
        "volatility": volatility,
        "atr": atr_estimate,
    }


# === GRID BUILDER ===

def build_grid(
    state: GridState,
    plan: dict,
    bid: float,
    ask: float,
    bar_index: int,
    grid_cooldown: int = 15,
):
    """Place pending BuyLimit/SellLimit orders. Matches EA BuildGrid()."""

    # Rate limit
    if (bar_index - state.last_grid_bar) < grid_cooldown:
        return 0

    point = plan["point"]
    lot = plan["lot_size"]
    dist = plan["grid_spacing_pts"]
    sl_pts = plan["sl_pts"]
    tp_pts = plan["tp_pts"]
    is_gold = plan["is_gold"]

    # Validate minimums
    if lot < 0.01 or dist < 5 or sl_pts < 10 or tp_pts < 10:
        return 0

    placed = 0

    # Buy Limits below bid
    for i in range(1, plan["buy_orders"] + 1):
        entry = round(bid - dist * i * point, 6)
        sl = round(entry - sl_pts * point, 6)
        tp = round(entry + tp_pts * point, 6)

        # Don't place too close to price
        if (bid - entry) / point < 5:
            continue

        state.pending_orders.append(PendingOrder(
            order_type="BUY_LIMIT",
            entry=entry,
            sl=sl,
            tp=tp,
            lot=lot,
            bar_index=bar_index,
        ))
        placed += 1

    # Sell Limits above ask
    for i in range(1, plan["sell_orders"] + 1):
        entry = round(ask + dist * i * point, 6)
        sl = round(entry + sl_pts * point, 6)
        tp = round(entry - tp_pts * point, 6)

        if (entry - ask) / point < 5:
            continue

        state.pending_orders.append(PendingOrder(
            order_type="SELL_LIMIT",
            entry=entry,
            sl=sl,
            tp=tp,
            lot=lot,
            bar_index=bar_index,
        ))
        placed += 1

    if placed > 0:
        state.last_grid_bar = bar_index

    return placed


# === SIMULATION ENGINE ===

def simulate_bar(
    state: GridState,
    bar: Bar,
    bar_index: int,
    symbol: str,
    timeframe: str,
    spread_pts: float,
    point: float,
    is_gold: bool,
    grid_cooldown: int = 15,
):
    """Process one bar: fill orders, check SL/TP, trail, maybe rebuild grid."""

    bid = bar.close  # Simplify: use close as bid
    ask = bid + spread_pts * point

    # --- 1. CHECK PENDING ORDERS FOR FILLS ---
    filled_orders = []
    remaining_orders = []
    for order in state.pending_orders:
        filled = False

        if order.order_type == "BUY_LIMIT":
            # BuyLimit fills when price drops to entry
            if bar.low <= order.entry:
                fill_price = order.entry
                filled = True
        elif order.order_type == "SELL_LIMIT":
            # SellLimit fills when price rises to entry
            if bar.high >= order.entry:
                fill_price = order.entry
                filled = True

        if filled:
            # Open a position
            direction = "BUY" if order.order_type == "BUY_LIMIT" else "SELL"
            state.open_positions.append(OpenPosition(
                direction=direction,
                entry_price=fill_price,
                sl=order.sl,
                tp=order.tp,
                lot=order.lot,
                open_bar=bar_index,
            ))
            filled_orders.append(order)
        else:
            remaining_orders.append(order)

    state.pending_orders = remaining_orders

    # --- 2. CHECK OPEN POSITIONS FOR SL/TP/TRAIL ---
    closed_positions = []
    still_open = []
    for pos in state.open_positions:
        closed = False

        if pos.direction == "BUY":
            # Check SL (price dropped)
            if bar.low <= pos.sl:
                if is_gold:
                    pnl = (pos.sl - pos.entry_price) / point * pos.lot
                else:
                    pnl = (pos.sl - pos.entry_price) * 100000 * pos.lot * point
                pos.close_price = pos.sl
                pos.pnl = pnl
                pos.close_bar = bar_index
                pos.close_reason = "SL"
                closed = True

            # Check TP (price rose)
            if not closed and bar.high >= pos.tp:
                if is_gold:
                    pnl = (pos.tp - pos.entry_price) / point * pos.lot
                else:
                    pnl = (pos.tp - pos.entry_price) * 100000 * pos.lot * point
                pos.close_price = pos.tp
                pos.pnl = pnl
                pos.close_bar = bar_index
                pos.close_reason = "TP"
                closed = True

            # Trailing: move SL to breakeven at 70% of SL distance, then trail
            if not closed:
                entry = pos.entry_price
                curr_sl = pos.sl
                sl_dist = abs(entry - curr_sl)
                profit_now = (bid - entry)

                # Only trail if we have meaningful SL distance
                if sl_dist > point * 10:
                    # At 70% of SL distance profit: move SL to breakeven
                    if profit_now >= sl_dist * 0.7 and curr_sl < entry:
                        pos.sl = entry + point * 5  # Breakeven + spread offset

                    # At 120%+ profit: trail at 50% of current profit from entry
                    elif profit_now >= sl_dist * 1.2:
                        trail_sl = entry + profit_now * 0.5
                        if trail_sl > pos.sl + point * 5:
                            pos.sl = trail_sl

        elif pos.direction == "SELL":
            # Check SL (price rose)
            if bar.high >= pos.sl:
                if is_gold:
                    pnl = (pos.entry_price - pos.sl) / point * pos.lot
                else:
                    pnl = (pos.entry_price - pos.sl) * 100000 * pos.lot * point
                pos.close_price = pos.sl
                pos.pnl = pnl
                pos.close_bar = bar_index
                pos.close_reason = "SL"
                closed = True

            # Check TP (price dropped)
            if not closed and bar.low <= pos.tp:
                if is_gold:
                    pnl = (pos.entry_price - pos.tp) / point * pos.lot
                else:
                    pnl = (pos.entry_price - pos.tp) * 100000 * pos.lot * point
                pos.close_price = pos.tp
                pos.pnl = pnl
                pos.close_bar = bar_index
                pos.close_reason = "TP"
                closed = True

            # Trailing for sell: mirror of BUY logic
            if not closed:
                entry = pos.entry_price
                curr_sl = pos.sl
                sl_dist = abs(curr_sl - entry)
                profit_now = (entry - bid)

                if sl_dist > point * 10:
                    # At 70% of SL distance profit: move SL to breakeven
                    if profit_now >= sl_dist * 0.7 and curr_sl > entry:
                        pos.sl = entry - point * 5  # Breakeven - spread offset

                    # At 120%+ profit: trail at 50% of current profit from entry
                    elif profit_now >= sl_dist * 1.2:
                        trail_sl = entry - profit_now * 0.5
                        if trail_sl < pos.sl - point * 5:
                            pos.sl = trail_sl

        if closed:
            state.balance += pos.pnl
            state.trades.append(TradeRecord(
                direction=pos.direction,
                entry_price=pos.entry_price,
                exit_price=pos.close_price,
                entry_bar=pos.open_bar,
                exit_bar=pos.close_bar,
                lot=pos.lot,
                pnl=pos.pnl,
                close_reason=pos.close_reason,
                signal=pos.signal,
                sl=pos.sl,
                tp=pos.tp,
                timestamp=bar.timestamp,
            ))
            closed_positions.append(pos)
        else:
            still_open.append(pos)

    state.open_positions = still_open

    # --- 3. RECORD EQUITY ---
    unrealized = 0.0
    for pos in state.open_positions:
        if pos.direction == "BUY":
            if is_gold:
                unrealized += (bid - pos.entry_price) / point * pos.lot
            else:
                unrealized += (bid - pos.entry_price) * 100000 * pos.lot * point
        else:
            if is_gold:
                unrealized += (pos.entry_price - bid) / point * pos.lot
            else:
                unrealized += (pos.entry_price - bid) * 100000 * pos.lot * point

    state.equity_curve.append(state.balance + unrealized)

    # --- 4. REMOVE STALE PENDING ORDERS (beyond grid range) ---
    # Keep only orders within 10x grid spacing of current price
    mid = (bid + ask) / 2
    max_dist = 500 * point  # ~500 pts = safe cleanup range for gold
    state.pending_orders = [
        o for o in state.pending_orders
        if abs(o.entry - mid) < max_dist
    ]

    return len(filled_orders)


def run_backtest(
    bars: List[Bar],
    symbol: str = "XAUUSD",
    timeframe: str = "M5",
    account_balance: float = 10000.0,
    spread_pts: float = 20.0,
    grid_cooldown: int = 15,
) -> dict:
    """
    Run full backtest on historical bars.
    
    Args:
        bars: List of OHLC bars (chronological order)
        symbol: Trading symbol
        timeframe: M1, M5, M15, H1
        account_balance: Starting balance
        spread_pts: Average spread in points
        grid_cooldown: Min bars between grid rebuilds
    
    Returns:
        Dict with results, trades, equity curve
    """

    is_gold = "XAU" in symbol.upper() or "GOLD" in symbol.upper()
    point = 0.01 if is_gold else 0.00001

    state = GridState(balance=account_balance)
    state.equity_curve.append(account_balance)

    total_grids = 0
    total_fills = 0
    daily_high = 0.0
    daily_low = 999999.0
    day_start_bar = 0
    current_day = ""

    print(f"\n{'='*60}")
    print(f"  DAX V2 BACKTEST ENGINE")
    print(f"  Symbol: {symbol} | TF: {timeframe} | Balance: ${account_balance:,.2f}")
    print(f"  Bars: {len(bars)} | Spread: {spread_pts} pts")
    print(f"{'='*60}\n")

    for i, bar in enumerate(bars):
        # Track daily range (resets each new day)
        bar_day = str(bar.timestamp)[:10] if bar.timestamp > 1000000000 else ""
        if bar_day != current_day:
            if current_day != "":
                pass  # Day rolled over
            current_day = bar_day
            daily_high = bar.high
            daily_low = bar.low
            day_start_bar = i
        else:
            daily_high = max(daily_high, bar.high)
            daily_low = min(daily_low, bar.low)

        bid = bar.close
        ask = bid + spread_pts * point

        # Generate signal (every bar, like EA's FetchPlan)
        plan = generate_signal(
            symbol=symbol,
            bid=bid,
            ask=ask,
            spread_pts=spread_pts,
            daily_high=daily_high,
            daily_low=daily_low,
            account_balance=state.balance,
            timeframe=timeframe,
        )

        # Check if grid needs building (no positions + no pending + not HOLD)
        n_pos = len(state.open_positions)
        n_pend = len(state.pending_orders)

        # Cancel stale pending orders if signal direction changed
        if n_pend > 0 and plan["signal"] != "HOLD":
            expected_type = "BUY_LIMIT" if plan["signal"] == "BUY" else "SELL_LIMIT"
            wrong_type = "SELL_LIMIT" if plan["signal"] == "BUY" else "BUY_LIMIT"
            state.pending_orders = [o for o in state.pending_orders if o.order_type != wrong_type]
            n_pend = len(state.pending_orders)

        if n_pos == 0 and n_pend == 0 and plan["signal"] != "HOLD":
            builds = build_grid(state, plan, bid, ask, i, grid_cooldown)
            if builds > 0:
                total_grids += 1

        # Process bar (fills, SL/TP, trailing)
        fills = simulate_bar(state, bar, i, symbol, timeframe, spread_pts, point, is_gold, grid_cooldown)
        total_fills += fills

        # Progress print
        if i % 500 == 0 and i > 0:
            print(f"  Bar {i}/{len(bars)} | Balance: ${state.balance:,.2f} | "
                  f"Trades: {len(state.trades)} | Open: {len(state.open_positions)} | "
                  f"Pending: {len(state.pending_orders)}")

    # Close any remaining positions at last bar's close
    if bars:
        last_bar = bars[-1]
        for pos in state.open_positions:
            if pos.direction == "BUY":
                if is_gold:
                    pnl = (last_bar.close - pos.entry_price) / point * pos.lot
                else:
                    pnl = (last_bar.close - pos.entry_price) * 100000 * pos.lot * point
            else:
                if is_gold:
                    pnl = (pos.entry_price - last_bar.close) / point * pos.lot
                else:
                    pnl = (pos.entry_price - last_bar.close) * 100000 * pos.lot * point

            state.balance += pnl
            state.trades.append(TradeRecord(
                direction=pos.direction,
                entry_price=pos.entry_price,
                exit_price=last_bar.close,
                entry_bar=pos.open_bar,
                exit_bar=len(bars) - 1,
                lot=pos.lot,
                pnl=pnl,
                close_reason="EOD",
                signal=pos.signal,
                sl=pos.sl,
                tp=pos.tp,
                timestamp=last_bar.timestamp,
            ))

    # === CALCULATE RESULTS ===
    total_trades = len(state.trades)
    winning = [t for t in state.trades if t.pnl > 0]
    losing = [t for t in state.trades if t.pnl <= 0]
    total_pnl = sum(t.pnl for t in state.trades)

    win_rate = len(winning) / total_trades * 100 if total_trades > 0 else 0
    avg_win = sum(t.pnl for t in winning) / len(winning) if winning else 0
    avg_loss = sum(t.pnl for t in losing) / len(losing) if losing else 0

    gross_profit = sum(t.pnl for t in winning)
    gross_loss = abs(sum(t.pnl for t in losing))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Max drawdown from equity curve
    peak = account_balance
    max_dd = 0.0
    max_dd_pct = 0.0
    for eq in state.equity_curve:
        if eq > peak:
            peak = eq
        dd = peak - eq
        dd_pct = dd / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = dd_pct

    # Signal breakdown
    buy_trades = [t for t in state.trades if t.direction == "BUY"]
    sell_trades = [t for t in state.trades if t.direction == "SELL"]
    buy_wins = [t for t in buy_trades if t.pnl > 0]
    sell_wins = [t for t in sell_trades if t.pnl > 0]

    buy_win_rate = len(buy_wins) / len(buy_trades) * 100 if buy_trades else 0
    sell_win_rate = len(sell_wins) / len(sell_trades) * 100 if sell_trades else 0

    # Close reason breakdown
    sl_closes = [t for t in state.trades if t.close_reason == "SL"]
    tp_closes = [t for t in state.trades if t.close_reason == "TP"]
    trail_closes = [t for t in state.trades if t.close_reason in ("TRAIL", "BREAKEVEN")]
    eod_closes = [t for t in state.trades if t.close_reason == "EOD"]

    # Print results
    print(f"\n{'='*60}")
    print(f"  BACKTEST RESULTS")
    print(f"{'='*60}")
    print(f"  Starting Balance:  ${account_balance:>12,.2f}")
    print(f"  Ending Balance:    ${state.balance:>12,.2f}")
    print(f"  Total P&L:         ${total_pnl:>12,.2f}  ({total_pnl/account_balance*100:+.1f}%)")
    print(f"{'─'*60}")
    print(f"  Total Trades:      {total_trades:>12}")
    print(f"  Winning:           {len(winning):>12}  ({win_rate:.1f}%)")
    print(f"  Losing:            {len(losing):>12}  ({100-win_rate:.1f}%)")
    print(f"  Profit Factor:     {profit_factor:>12.2f}")
    print(f"{'─'*60}")
    print(f"  Avg Win:           ${avg_win:>12,.2f}")
    print(f"  Avg Loss:          ${avg_loss:>12,.2f}")
    print(f"  Max Drawdown:      ${max_dd:>12,.2f}  ({max_dd_pct:.1f}%)")
    print(f"{'─'*60}")
    print(f"  Buy Trades:        {len(buy_trades):>8}  Win: {buy_win_rate:.1f}%")
    print(f"  Sell Trades:       {len(sell_trades):>8}  Win: {sell_win_rate:.1f}%")
    print(f"  Grids Built:       {total_grids:>12}")
    print(f"  Order Fills:       {total_fills:>12}")
    print(f"{'─'*60}")
    print(f"  Closed by SL:      {len(sl_closes):>12}")
    print(f"  Closed by TP:      {len(tp_closes):>12}")
    print(f"  Closed by Trail:   {len(trail_closes):>12}")
    print(f"  Closed by EOD:     {len(eod_closes):>12}")
    print(f"{'='*60}\n")

    # Print last 20 trades
    if state.trades:
        print(f"  LAST {min(20, len(state.trades))} TRADES:")
        print(f"  {'#':>4} {'Dir':<5} {'Entry':>10} {'Exit':>10} {'P&L':>10} {'Reason':<10}")
        print(f"  {'─'*55}")
        for t in state.trades[-20:]:
            print(f"  {state.trades.index(t)+1:>4} {t.direction:<5} {t.entry_price:>10.2f} "
                  f"{t.exit_price:>10.2f} ${t.pnl:>+9.2f} {t.close_reason:<10}")

    return {
        "starting_balance": account_balance,
        "ending_balance": round(state.balance, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl / account_balance * 100, 2),
        "total_trades": total_trades,
        "winning_trades": len(winning),
        "losing_trades": len(losing),
        "win_rate": round(win_rate, 2),
        "profit_factor": round(profit_factor, 4),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "grids_built": total_grids,
        "order_fills": total_fills,
        "buy_trades": len(buy_trades),
        "sell_trades": len(sell_trades),
        "buy_win_rate": round(buy_win_rate, 2),
        "sell_win_rate": round(sell_win_rate, 2),
        "sl_closes": len(sl_closes),
        "tp_closes": len(tp_closes),
        "trail_closes": len(trail_closes),
        "eod_closes": len(eod_closes),
        "equity_curve": [round(e, 2) for e in state.equity_curve[::max(1, len(state.equity_curve)//500)]],
        "trades": [
            {
                "direction": t.direction,
                "entry": t.entry_price,
                "exit": t.exit_price,
                "pnl": round(t.pnl, 2),
                "reason": t.close_reason,
                "bar_entry": t.entry_bar,
                "bar_exit": t.exit_bar,
            }
            for t in state.trades
        ],
    }
