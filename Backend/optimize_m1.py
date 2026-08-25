"""
DAX V2 M1 Optimizer - High Trade Count Focus
Tests M1 timeframe with wider grids, rewards more trades.
Run on Windows: python optimize_m1.py
"""

import sys
import os
import io
import json
import time
import itertools
from datetime import datetime, timedelta
from typing import List, Dict, Any
from copy import deepcopy

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import MetaTrader5 as mt5
except ImportError:
    print("ERROR: MetaTrader5 package not installed.")
    sys.exit(1)

from app.services.backtest_engine import Bar, run_backtest, HFT, generate_signal
import app.services.backtest_engine as engine


# === M1 PARAMETER GRIDS (wider, more combos) ===

STAGE2_HFT = {
    "sl_ratio":   [0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
    "tp_ratio":   [1.0, 1.2, 1.4, 1.5, 1.6, 1.8, 2.0, 2.2, 2.5],
    "grid_factor": [0.15, 0.18, 0.20, 0.22, 0.25, 0.28, 0.30, 0.35, 0.40],
    "max_orders": [2, 3, 4, 5, 6],
}

STAGE3_SIGNAL = {
    "buy_zone":  [0.20, 0.25, 0.30, 0.35, 0.40],
    "sell_zone": [0.60, 0.65, 0.70, 0.75, 0.80],
    "vol_mult":  [8, 10, 12, 15, 18, 20],
}

STAGE4_SLTP = {
    "gold_sl_min": [100, 150, 200, 250, 300],
    "gold_sl_max": [350, 400, 500, 600, 700],
    "gold_tp_min": [100, 150, 200, 300],
    "gold_tp_max": [500, 600, 750, 900, 1000],
}

STAGE5_GRID_TRAIL = {
    "grid_cooldown":   [6, 8, 10, 12, 15, 20, 25, 30],
    "trail_be_trigger": [0.4, 0.5, 0.6, 0.7, 0.8],
    "trail_trigger":   [0.8, 1.0, 1.2, 1.5, 1.8],
    "trail_pct":       [0.3, 0.4, 0.5, 0.6],
}


# === SCORING: reward high trade count + profit ===

def score_result(r: dict) -> float:
    wr = r.get("win_rate", 0)
    pf = r.get("profit_factor", 0)
    dd = r.get("max_drawdown_pct", 100)
    trades = r.get("total_trades", 0)
    pnl_pct = r.get("total_pnl_pct", 0)
    if pf < 1.0 or trades < 50:
        return -999
    # Heavily weight trade count and profit factor
    trade_bonus = min(trades / 100.0, 20.0)  # up to 20 bonus for 2000+ trades
    return wr * 0.3 + pf * 20 * 0.25 + max(0, 100 - dd) * 0.15 + trade_bonus * 0.15 + pnl_pct * 0.15


# === MT5 DATA DOWNLOAD ===

def download_bars(symbol: str, timeframe_str: str, days: int) -> List[Bar]:
    import MetaTrader5 as mt5
    TF_MAP = {
        "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1,
    }
    tf = TF_MAP.get(timeframe_str, mt5.TIMEFRAME_M1)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    rates = mt5.copy_rates_range(symbol, tf, start_date, end_date)
    if rates is None or len(rates) == 0:
        print(f"  ERROR: No data for {symbol} {timeframe_str}")
        return []
    bars = []
    for r in rates:
        bars.append(Bar(
            timestamp=r['time'], open=r['open'], high=r['high'],
            low=r['low'], close=r['close'], volume=int(r['tick_volume']),
        ))
    return bars


# === PARAMETERIZED BACKTEST (copied + patched for M1) ===

def run_param_backtest(bars, symbol, timeframe, balance, spread_pts, params):
    orig_hft = deepcopy(engine.HFT)
    try:
        tf_key = timeframe if timeframe in engine.HFT else "M1"
        engine.HFT[tf_key] = {
            "grid_factor": params.get("grid_factor", 0.20),
            "sl_ratio": params.get("sl_ratio", 0.5),
            "tp_ratio": params.get("tp_ratio", 1.5),
            "max_orders": params.get("max_orders", 4),
        }

        engine._BUY_ZONE = params.get("buy_zone", 0.35)
        engine._SELL_ZONE = params.get("sell_zone", 0.65)
        engine._VOL_MULT = params.get("vol_mult", 15)
        engine._GOLD_SL_MIN = params.get("gold_sl_min", 200)
        engine._GOLD_SL_MAX = params.get("gold_sl_max", 500)
        engine._GOLD_TP_MIN = params.get("gold_tp_min", 150)
        engine._GOLD_TP_MAX = params.get("gold_tp_max", 750)
        engine._TRAIL_BE = params.get("trail_be_trigger", 0.7)
        engine._TRAIL_TRIGGER = params.get("trail_trigger", 1.2)
        engine._TRAIL_PCT = params.get("trail_pct", 0.5)

        cooldown = params.get("grid_cooldown", 15)
        _orig_gen = engine.generate_signal

        def _patched_signal(symbol, bid, ask, spread_pts, daily_high, daily_low, account_balance, timeframe="M1"):
            mid = (bid + ask) / 2 if ask > 0 else bid
            if mid <= 0: mid = 1.0
            is_gold = "XAU" in symbol.upper() or "GOLD" in symbol.upper()
            point = 0.01 if is_gold else 0.00001
            daily_range = daily_high - daily_low
            if daily_range <= 0: daily_range = mid * 0.005
            volatility = daily_range / mid if mid > 0 else 0.005
            spread_pct = (spread_pts * point) / mid * 100 if mid > 0 else 0
            pos_in_range = (mid - daily_low) / daily_range if daily_range > 0 else 0.5
            tf = engine.HFT.get(timeframe, engine.HFT.get("M1", {}))
            atr_mult_map = {"M1": 0.03, "M5": 0.06, "M15": 0.10, "H1": 0.18}
            atr_estimate = daily_range * atr_mult_map.get(timeframe, 0.03)

            signal = "BUY"
            confidence = 0.6
            risk_score = 0.30
            buy_z = engine._BUY_ZONE
            sell_z = engine._SELL_ZONE

            if spread_pct < 0.15:
                if pos_in_range < buy_z:
                    signal = "BUY"
                    confidence = max(0.55, 0.85 - abs(pos_in_range - (buy_z * 0.43)) * 2)
                elif pos_in_range > sell_z:
                    signal = "SELL"
                    confidence = max(0.55, 0.85 - abs(pos_in_range - (1 - (1 - sell_z) * 0.43)) * 2)
                else:
                    signal = "HOLD"
                    confidence = 0.40
                    risk_score = 0.25
            else:
                signal = "HOLD"
                confidence = 0.30
                risk_score = 0.60

            confidence = max(0.10, min(0.95, confidence))
            risk_score = max(0.10, min(0.95, risk_score))

            vol_m = engine._VOL_MULT
            sl_distance_price = atr_estimate * tf["sl_ratio"] * (1.0 + volatility * vol_m)
            min_sl = mid * 0.0003
            sl_distance_price = max(min_sl, sl_distance_price)
            tp_distance_price = sl_distance_price * tf["tp_ratio"]
            sl_pts = sl_distance_price / point
            tp_pts = tp_distance_price / point

            if is_gold:
                sl_pts = max(engine._GOLD_SL_MIN, min(engine._GOLD_SL_MAX, sl_pts))
                tp_pts = max(engine._GOLD_TP_MIN, min(engine._GOLD_TP_MAX, tp_pts))
                tp_pts = max(tp_pts, int(sl_pts * tf["tp_ratio"]))
                tp_pts = min(engine._GOLD_TP_MAX, tp_pts)
            else:
                sl_pts = max(20, min(200, sl_pts))
                tp_pts = max(15, min(300, tp_pts))
                tp_pts = max(tp_pts, int(sl_pts * tf["tp_ratio"]))
                tp_pts = min(300, tp_pts)

            target_lot = 0.02
            min_grid_pts = 20 if is_gold else 5
            grid_spacing_price = atr_estimate * tf["grid_factor"]
            grid_spacing_pts = int(grid_spacing_price / point)
            grid_spacing_pts = max(min_grid_pts, min(100, grid_spacing_pts))

            if is_gold:
                risk_per_position = sl_pts * 0.01
                max_orders_cap = 3
            else:
                risk_per_position = 0.01 * (sl_pts / 10.0)
                max_orders_cap = min(tf.get("max_orders", 8), 10)

            max_risk_per_side = account_balance * 0.02
            max_by_risk = int(max_risk_per_side / risk_per_position) if risk_per_position > 0 else 3
            max_orders = min(max_orders_cap, max_by_risk)
            base_orders = max(2, max_orders)

            if signal == "BUY":
                buy_orders = max_orders
                sell_orders = 0
            elif signal == "SELL":
                sell_orders = max_orders
                buy_orders = 0
            else:
                buy_orders = 0
                sell_orders = 0

            return {
                "signal": signal, "lot_size": target_lot,
                "sl_pts": sl_pts, "tp_pts": tp_pts,
                "grid_spacing_pts": grid_spacing_pts,
                "buy_orders": buy_orders, "sell_orders": sell_orders,
                "is_gold": is_gold, "point": point,
                "confidence": confidence,
                "risk_level": "LOW" if risk_score < 0.35 else "MEDIUM" if risk_score < 0.60 else "HIGH",
                "volatility": volatility, "atr": atr_estimate,
            }

        engine.generate_signal = _patched_signal
        result = run_backtest(
            bars=bars, symbol=symbol, timeframe=timeframe,
            account_balance=balance, spread_pts=spread_pts,
            grid_cooldown=cooldown,
        )
        engine.generate_signal = _orig_gen
        return result
    finally:
        engine.HFT = orig_hft
        for attr, val in [('_BUY_ZONE', 0.35), ('_SELL_ZONE', 0.65), ('_VOL_MULT', 15),
                          ('_GOLD_SL_MIN', 200), ('_GOLD_SL_MAX', 500),
                          ('_GOLD_TP_MIN', 150), ('_GOLD_TP_MAX', 750),
                          ('_TRAIL_BE', 0.7), ('_TRAIL_TRIGGER', 1.2), ('_TRAIL_PCT', 0.5)]:
            setattr(engine, attr, val)


def expand_grid(grid):
    keys = list(grid.keys())
    values = list(grid.values())
    combos = list(itertools.product(*values))
    return [dict(zip(keys, combo)) for combo in combos]


def merge_params(base, overlay):
    result = deepcopy(base)
    result.update(overlay)
    return result


# === MAIN ===

def optimize_m1():
    symbol = "XAUUSD.m"
    timeframe = "M5"
    balance = 10000.0
    spread_pts = 20
    days = 90

    print(f"\n{'='*70}")
    print(f"  DAX V2 M5 OPTIMIZER - HIGH TRADE COUNT (M1 unavailable)")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Symbol: {symbol} | TF: {timeframe} | Balance: ${balance:,.0f} | Days: {days}")
    print(f"{'='*70}")

    if not mt5.initialize():
        print("ERROR: MT5 init failed"); sys.exit(1)
    info = mt5.account_info()
    print(f"  MT5: {info.server} | Account: {info.login}")

    # Download M1 data
    print(f"\n--- Downloading {days} days of M1 data ---")
    print(f"  M1...", end=" ", flush=True)
    bars = download_bars(symbol, timeframe, days)
    print(f"{len(bars)} bars")
    if not bars:
        print("ERROR: No M1 data"); sys.exit(1)

    all_results = []
    start_total = time.time()

    # === STAGE 1: Baseline M1 ===
    print(f"\n{'='*70}")
    print(f"  STAGE 1: M1 Baseline")
    print(f"{'='*70}")
    result = run_param_backtest(bars, symbol, timeframe, balance, spread_pts, {})
    sc = score_result(result)
    print(f"  Baseline: WR={result['win_rate']:.1f}% PF={result['profit_factor']:.2f} "
          f"PnL={result['total_pnl_pct']:+.1f}% DD={result['max_drawdown_pct']:.1f}% "
          f"Trades={result['total_trades']} Score={sc:+.2f}")
    all_results.append({"stage": 1, "timeframe": timeframe, "params": {}, **result, "score": sc})
    best_score = sc
    best_params = {}

    # === STAGE 2: HFT Multipliers (big grid: 8*9*9*5 = 3240 combos) ===
    hft_combos = expand_grid(STAGE2_HFT)
    print(f"\n{'='*70}")
    print(f"  STAGE 2: HFT Multipliers ({len(hft_combos)} combos)")
    print(f"{'='*70}")

    stage2_best_score = best_score
    stage2_best_params = deepcopy(best_params)
    for i, combo in enumerate(hft_combos):
        t0 = time.time()
        result = run_param_backtest(bars, symbol, timeframe, balance, spread_pts, combo)
        elapsed = time.time() - t0
        sc = score_result(result)
        wr = result.get("win_rate", 0)
        pf = result.get("profit_factor", 0)
        trades = result.get("total_trades", 0)
        pnl = result.get("total_pnl_pct", 0)
        if sc > stage2_best_score:
            stage2_best_score = sc
            stage2_best_params = deepcopy(combo)
            flag = " *"
        else:
            flag = ""
        if (i + 1) % 100 == 0 or flag == " *":
            print(f"  [{i+1:4d}/{len(hft_combos)}] sl={combo['sl_ratio']:.1f} tp={combo['tp_ratio']:.1f} "
                  f"gf={combo['grid_factor']:.2f} mo={combo['max_orders']} "
                  f"WR={wr:5.1f}% PF={pf:5.2f} T={trades:4d} PnL={pnl:+6.1f}% S={sc:+7.2f}{flag} ({elapsed:.1f}s)")
        all_results.append({"stage": 2, "timeframe": timeframe, "params": combo, **result, "score": sc})

    print(f"\n  >> Stage 2 best: {stage2_best_params} (score={stage2_best_score:+.2f})")
    base_params = merge_params(stage2_best_params, {})

    # === STAGE 3: Signal Thresholds (5*5*6 = 150 combos) ===
    sig_combos = expand_grid(STAGE3_SIGNAL)
    print(f"\n{'='*70}")
    print(f"  STAGE 3: Signal Thresholds ({len(sig_combos)} combos)")
    print(f"{'='*70}")

    stage3_best_score = stage2_best_score
    stage3_best_params = deepcopy(base_params)
    for i, combo in enumerate(sig_combos):
        params = merge_params(base_params, combo)
        t0 = time.time()
        result = run_param_backtest(bars, symbol, timeframe, balance, spread_pts, params)
        elapsed = time.time() - t0
        sc = score_result(result)
        wr = result.get("win_rate", 0)
        pf = result.get("profit_factor", 0)
        trades = result.get("total_trades", 0)
        pnl = result.get("total_pnl_pct", 0)
        if sc > stage3_best_score:
            stage3_best_score = sc
            stage3_best_params = deepcopy(params)
            flag = " *"
        else:
            flag = ""
        if (i + 1) % 30 == 0 or flag == " *":
            print(f"  [{i+1:3d}/{len(sig_combos)}] buy<{combo['buy_zone']:.2f} sell>{combo['sell_zone']:.2f} "
                  f"vm={combo['vol_mult']:2.0f} WR={wr:5.1f}% PF={pf:5.2f} T={trades:4d} "
                  f"PnL={pnl:+6.1f}% S={sc:+7.2f}{flag} ({elapsed:.1f}s)")
        all_results.append({"stage": 3, "timeframe": timeframe, "params": params, **result, "score": sc})

    print(f"\n  >> Stage 3 best: buy<{stage3_best_params.get('buy_zone')} "
          f"sell>{stage3_best_params.get('sell_zone')} vm={stage3_best_params.get('vol_mult')} "
          f"(score={stage3_best_score:+.2f})")
    base_params = deepcopy(stage3_best_params)

    # === STAGE 4: SL/TP Clamps (5*5*4*5 = 500 combos) ===
    sltp_combos = expand_grid(STAGE4_SLTP)
    print(f"\n{'='*70}")
    print(f"  STAGE 4: SL/TP Clamps ({len(sltp_combos)} combos)")
    print(f"{'='*70}")

    stage4_best_score = stage3_best_score
    stage4_best_params = deepcopy(base_params)
    for i, combo in enumerate(sltp_combos):
        params = merge_params(base_params, combo)
        t0 = time.time()
        result = run_param_backtest(bars, symbol, timeframe, balance, spread_pts, params)
        elapsed = time.time() - t0
        sc = score_result(result)
        wr = result.get("win_rate", 0)
        pf = result.get("profit_factor", 0)
        trades = result.get("total_trades", 0)
        pnl = result.get("total_pnl_pct", 0)
        if sc > stage4_best_score:
            stage4_best_score = sc
            stage4_best_params = deepcopy(params)
            flag = " *"
        else:
            flag = ""
        if (i + 1) % 50 == 0 or flag == " *":
            print(f"  [{i+1:4d}/{len(sltp_combos)}] sl=[{combo['gold_sl_min']},{combo['gold_sl_max']}] "
                  f"tp=[{combo['gold_tp_min']},{combo['gold_tp_max']}] "
                  f"WR={wr:5.1f}% PF={pf:5.2f} T={trades:4d} PnL={pnl:+6.1f}% S={sc:+7.2f}{flag} ({elapsed:.1f}s)")
        all_results.append({"stage": 4, "timeframe": timeframe, "params": params, **result, "score": sc})

    print(f"\n  >> Stage 4 best: sl=[{stage4_best_params.get('gold_sl_min')},{stage4_best_params.get('gold_sl_max')}] "
          f"tp=[{stage4_best_params.get('gold_tp_min')},{stage4_best_params.get('gold_tp_max')}] "
          f"(score={stage4_best_score:+.2f})")
    base_params = deepcopy(stage4_best_params)

    # === STAGE 5: Grid & Trailing (6*5*5*4 = 600 combos) ===
    gt_combos = expand_grid(STAGE5_GRID_TRAIL)
    print(f"\n{'='*70}")
    print(f"  STAGE 5: Grid & Trailing ({len(gt_combos)} combos)")
    print(f"{'='*70}")

    stage5_best_score = stage4_best_score
    stage5_best_params = deepcopy(base_params)
    for i, combo in enumerate(gt_combos):
        params = merge_params(base_params, combo)
        t0 = time.time()
        result = run_param_backtest(bars, symbol, timeframe, balance, spread_pts, params)
        elapsed = time.time() - t0
        sc = score_result(result)
        wr = result.get("win_rate", 0)
        pf = result.get("profit_factor", 0)
        trades = result.get("total_trades", 0)
        pnl = result.get("total_pnl_pct", 0)
        if sc > stage5_best_score:
            stage5_best_score = sc
            stage5_best_params = deepcopy(params)
            flag = " *"
        else:
            flag = ""
        if (i + 1) % 60 == 0 or flag == " *":
            print(f"  [{i+1:4d}/{len(gt_combos)}] cd={combo['grid_cooldown']:2d} "
                  f"be={combo['trail_be_trigger']:.1f} tr={combo['trail_trigger']:.1f} "
                  f"tp={combo['trail_pct']:.1f} WR={wr:5.1f}% PF={pf:5.2f} T={trades:4d} "
                  f"PnL={pnl:+6.1f}% S={sc:+7.2f}{flag} ({elapsed:.1f}s)")
        all_results.append({"stage": 5, "timeframe": timeframe, "params": params, **result, "score": sc})

    print(f"\n  >> Stage 5 best: cd={stage5_best_params.get('grid_cooldown')} "
          f"be={stage5_best_params.get('trail_be_trigger')} "
          f"tr={stage5_best_params.get('trail_trigger')} "
          f"tp={stage5_best_params.get('trail_pct')} "
          f"(score={stage5_best_score:+.2f})")

    # === STAGE 6: Final Validation ===
    final_params = deepcopy(stage5_best_params)
    print(f"\n{'='*70}")
    print(f"  STAGE 6: Final Validation")
    print(f"{'='*70}")

    final_result = run_param_backtest(bars, symbol, timeframe, balance, spread_pts, final_params)
    final_score = score_result(final_result)

    elapsed_total = time.time() - start_total
    print(f"\n  FINAL RESULT ({elapsed_total:.0f}s total):")
    print(f"  {'─'*55}")
    print(f"  Timeframe:     {timeframe}")
    print(f"  Starting:      ${balance:,.2f}")
    print(f"  Ending:        ${final_result.get('ending_balance', 0):,.2f}")
    print(f"  P&L:           ${final_result.get('total_pnl', 0):+,.2f} ({final_result.get('total_pnl_pct', 0):+.1f}%)")
    print(f"  Win Rate:      {final_result.get('win_rate', 0):.1f}%")
    print(f"  Profit Factor: {final_result.get('profit_factor', 0):.2f}")
    print(f"  Max Drawdown:  {final_result.get('max_drawdown_pct', 0):.1f}%")
    print(f"  Trades:        {final_result.get('total_trades', 0)}")
    print(f"  Score:         {final_score:+.2f}")
    print(f"  {'─'*55}")

    # === TOP 20 ===
    all_results.sort(key=lambda x: x.get("score", -999), reverse=True)
    unique_results = []
    seen = set()
    for r in all_results:
        key = json.dumps(r.get("params", {}), sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique_results.append(r)

    print(f"\n  TOP 20 PARAMETER SETS:")
    print(f"  {'#':>3s} {'WR%':>6s} {'PF':>6s} {'PnL%':>7s} {'DD%':>6s} {'Trades':>6s} {'Score':>8s} Parameters")
    print(f"  {'─'*100}")
    for i, r in enumerate(unique_results[:20]):
        wr = r.get("win_rate", 0)
        pf = r.get("profit_factor", 0)
        pnl = r.get("total_pnl_pct", 0)
        dd = r.get("max_drawdown_pct", 0)
        trades = r.get("total_trades", 0)
        sc = r.get("score", 0)
        p = r.get("params", {})
        desc = " ".join(f"{k}={v}" for k, v in p.items()) if p else "defaults"
        print(f"  {i+1:3d} {wr:5.1f}% {pf:5.2f} {pnl:+6.1f}% {dd:5.1f}% {trades:5d} {sc:+7.2f}  {desc}")

    # === BEST PARAMS ===
    bp = final_params
    print(f"\n  {'='*70}")
    print(f"  OPTIMAL M5 PARAMETERS")
    print(f"  {'='*70}")
    print(f"  HFT[\"M1\"] = {{")
    print(f"      \"grid_factor\": {bp.get('grid_factor', 0.20)},")
    print(f"      \"sl_ratio\":   {bp.get('sl_ratio', 0.5)},")
    print(f"      \"tp_ratio\":   {bp.get('tp_ratio', 1.5)},")
    print(f"      \"max_orders\": {bp.get('max_orders', 4)},")
    print(f"  }}")
    print(f"  Signal: buy_zone={bp.get('buy_zone', 0.35)} sell_zone={bp.get('sell_zone', 0.65)} vol_mult={bp.get('vol_mult', 15)}")
    print(f"  SL/TP:  sl=[{bp.get('gold_sl_min', 200)},{bp.get('gold_sl_max', 500)}] tp=[{bp.get('gold_tp_min', 150)},{bp.get('gold_tp_max', 750)}]")
    print(f"  Grid:   cooldown={bp.get('grid_cooldown', 15)}")
    print(f"  Trail:  be_trigger={bp.get('trail_be_trigger', 0.7)} trigger={bp.get('trail_trigger', 1.2)} pct={bp.get('trail_pct', 0.5)}")

    # Save
    output_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                f"optimize_m5_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(output_file, 'w') as f:
        json.dump({
            "symbol": symbol,
            "timeframe": timeframe,
            "days": days,
            "balance": balance,
            "best_params": final_params,
            "final_result": {k: v for k, v in final_result.items() if k != "equity_curve"},
            "final_score": final_score,
            "top_20": [{k: v for k, v in r.items() if k != "equity_curve"} for r in unique_results[:20]],
            "total_tests": len(unique_results),
            "elapsed_seconds": elapsed_total,
        }, f, indent=2, default=str)
    print(f"\n  Results saved to: {output_file}")
    print(f"  Total unique tests: {len(unique_results)} in {elapsed_total:.0f}s")

    mt5.shutdown()


if __name__ == "__main__":
    optimize_m1()
