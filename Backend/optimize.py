"""
DAX V2 Deep Optimizer - XAUUSD.m Parameter Grid Search
Tests 241+ combinations across 5 stages to find best win rate + profitability.
Run on Windows with MT5: python optimize.py
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
    print("ERROR: MetaTrader5 package not installed. Run: pip install MetaTrader5")
    sys.exit(1)

from app.services.backtest_engine import Bar, run_backtest, HFT, generate_signal
import app.services.backtest_engine as engine


# === PARAMETER GRIDS ===

STAGE1_TIMEFRAMES = ["M1", "M5", "M15", "H1"]

STAGE2_HFT = {
    "sl_ratio":   [0.4, 0.5, 0.6, 0.7, 0.8],
    "tp_ratio":   [1.0, 1.2, 1.5, 1.8, 2.0],
    "grid_factor": [0.20, 0.25, 0.30, 0.35],
    "max_orders": [2, 3, 4],
}

STAGE3_SIGNAL = {
    "buy_zone":  [0.25, 0.30, 0.35, 0.40],
    "sell_zone": [0.60, 0.65, 0.70, 0.75],
    "vol_mult":  [10, 15, 20],
}

STAGE4_SLTP = {
    "gold_sl_min": [150, 200, 250, 300],
    "gold_sl_max": [400, 500, 600],
    "gold_tp_min": [150, 200, 300],
    "gold_tp_max": [600, 750, 900],
}

STAGE5_GRID_TRAIL = {
    "grid_cooldown":   [10, 15, 20, 30],
    "trail_be_trigger": [0.5, 0.6, 0.7, 0.8],
    "trail_trigger":   [1.0, 1.2, 1.5],
    "trail_pct":       [0.4, 0.5, 0.6],
}


# === SCORING ===

def score_result(r: dict) -> float:
    wr = r.get("win_rate", 0)
    pf = r.get("profit_factor", 0)
    dd = r.get("max_drawdown_pct", 100)
    trades = r.get("total_trades", 0)
    if pf < 1.0 or trades < 30:
        return -999
    return wr * 0.5 + pf * 20 * 0.3 + max(0, 100 - dd) * 0.2


# === MT5 DATA DOWNLOAD ===

def download_bars(symbol: str, timeframe_str: str, days: int) -> List[Bar]:
    import MetaTrader5 as mt5
    TF_MAP = {
        "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1,
    }
    tf = TF_MAP.get(timeframe_str, mt5.TIMEFRAME_M5)
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


# === PARAMETERIZED BACKTEST ===

def run_param_backtest(
    bars: List[Bar],
    symbol: str,
    timeframe: str,
    balance: float,
    spread_pts: float,
    params: dict,
) -> dict:
    """Run backtest with custom parameters by patching the engine."""

    orig_hft = deepcopy(engine.HFT)

    try:
        tf_key = timeframe if timeframe in engine.HFT else "M5"

        # Patch HFT multipliers
        engine.HFT[tf_key] = {
            "grid_factor": params.get("grid_factor", 0.30),
            "sl_ratio": params.get("sl_ratio", 0.6),
            "tp_ratio": params.get("tp_ratio", 1.5),
            "max_orders": params.get("max_orders", 3),
        }

        # Patch thresholds via module globals
        orig_buy = getattr(engine, '_BUY_ZONE', 0.35)
        orig_sell = getattr(engine, '_SELL_ZONE', 0.65)
        orig_vol = getattr(engine, '_VOL_MULT', 15)
        orig_sl_min = getattr(engine, '_GOLD_SL_MIN', 200)
        orig_sl_max = getattr(engine, '_GOLD_SL_MAX', 500)
        orig_tp_min = getattr(engine, '_GOLD_TP_MIN', 150)
        orig_tp_max = getattr(engine, '_GOLD_TP_MAX', 750)
        orig_cooldown = 15
        orig_trail_be = getattr(engine, '_TRAIL_BE', 0.7)
        orig_trail_trigger = getattr(engine, '_TRAIL_TRIGGER', 1.2)
        orig_trail_pct = getattr(engine, '_TRAIL_PCT', 0.5)

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

        # Patch generate_signal to use our thresholds
        _orig_gen = engine.generate_signal

        def _patched_signal(symbol, bid, ask, spread_pts, daily_high, daily_low, account_balance, timeframe="M5"):
            import math
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

            tf = engine.HFT.get(timeframe, engine.HFT.get("M5", {}))

            atr_mult_map = {"M1": 0.03, "M5": 0.06, "M15": 0.10, "H1": 0.18}
            atr_estimate = daily_range * atr_mult_map.get(timeframe, 0.06)

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

        # Also patch trailing in simulate_bar by patching module-level trail params
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


# === GRID GENERATION ===

def expand_grid(grid: dict) -> List[dict]:
    """Expand a parameter grid into list of param dicts."""
    keys = list(grid.keys())
    values = list(grid.values())
    combos = list(itertools.product(*values))
    return [dict(zip(keys, combo)) for combo in combos]


def merge_params(base: dict, overlay: dict) -> dict:
    """Merge overlay params into base."""
    result = deepcopy(base)
    result.update(overlay)
    return result


# === MAIN OPTIMIZER ===

def optimize():
    symbol = "XAUUSD.m"
    balance = 10000.0
    spread_pts = 20
    days = 90  # 3 months

    print(f"\n{'='*70}")
    print(f"  DAX V2 DEEP OPTIMIZER - XAUUSD.m")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Symbol: {symbol} | Balance: ${balance:,.0f} | Days: {days}")
    print(f"{'='*70}")

    # Connect MT5
    if not mt5.initialize():
        print("ERROR: MT5 init failed"); sys.exit(1)
    info = mt5.account_info()
    print(f"  MT5: {info.server} | Account: {info.login}")
    # Keep MT5 running for data download

    # Download data for all timeframes
    print(f"\n--- Downloading {days} days of data ---")
    data = {}
    for tf in STAGE1_TIMEFRAMES:
        print(f"  {tf}...", end=" ", flush=True)
        bars = download_bars(symbol, tf, days)
        print(f"{len(bars)} bars")
        data[tf] = bars

    all_results = []
    start_total = time.time()

    # === STAGE 1: Best Timeframe ===
    print(f"\n{'='*70}")
    print(f"  STAGE 1: Best Timeframe ({len(STAGE1_TIMEFRAMES)} tests)")
    print(f"{'='*70}")

    tf_scores = {}
    for tf in STAGE1_TIMEFRAMES:
        if not data[tf]:
            continue
        t0 = time.time()
        result = run_param_backtest(data[tf], symbol, tf, balance, spread_pts, {})
        elapsed = time.time() - t0
        sc = score_result(result)
        tf_scores[tf] = sc
        wr = result.get("win_rate", 0)
        pf = result.get("profit_factor", 0)
        pnl = result.get("total_pnl_pct", 0)
        trades = result.get("total_trades", 0)
        dd = result.get("max_drawdown_pct", 0)
        print(f"  {tf:>4s}: WR={wr:5.1f}% PF={pf:5.2f} PnL={pnl:+6.1f}% DD={dd:5.1f}% Trades={trades:4d} Score={sc:+7.2f} ({elapsed:.1f}s)")
        all_results.append({"stage": 1, "timeframe": tf, "params": {}, **result, "score": sc})

    best_tf = max(tf_scores, key=tf_scores.get)
    print(f"\n  >> Best timeframe: {best_tf} (score={tf_scores[best_tf]:+.2f})")

    if not data[best_tf]:
        print("ERROR: No data for best timeframe"); sys.exit(1)

    bars = data[best_tf]

    # === STAGE 2: HFT Multipliers ===
    hft_combos = expand_grid(STAGE2_HFT)
    print(f"\n{'='*70}")
    print(f"  STAGE 2: HFT Multipliers ({len(hft_combos)} tests) on {best_tf}")
    print(f"{'='*70}")

    stage2_best_score = -999
    stage2_best_params = {}
    for i, combo in enumerate(hft_combos):
        t0 = time.time()
        result = run_param_backtest(bars, symbol, best_tf, balance, spread_pts, combo)
        elapsed = time.time() - t0
        sc = score_result(result)
        wr = result.get("win_rate", 0)
        pf = result.get("profit_factor", 0)
        trades = result.get("total_trades", 0)
        if sc > stage2_best_score:
            stage2_best_score = sc
            stage2_best_params = deepcopy(combo)
            flag = " *"
        else:
            flag = ""
        if (i + 1) % 25 == 0 or flag:
            print(f"  [{i+1:3d}/{len(hft_combos)}] sl={combo['sl_ratio']:.1f} tp={combo['tp_ratio']:.1f} gf={combo['grid_factor']:.2f} mo={combo['max_orders']} WR={wr:5.1f}% PF={pf:5.2f} T={trades:3d} S={sc:+7.2f}{flag} ({elapsed:.1f}s)")
        all_results.append({"stage": 2, "timeframe": best_tf, "params": combo, **result, "score": sc})

    print(f"\n  >> Stage 2 best: {stage2_best_params} (score={stage2_best_score:+.2f})")
    base_params = merge_params(stage2_best_params, {})

    # === STAGE 3: Signal Thresholds ===
    sig_combos = expand_grid(STAGE3_SIGNAL)
    print(f"\n{'='*70}")
    print(f"  STAGE 3: Signal Thresholds ({len(sig_combos)} tests)")
    print(f"{'='*70}")

    stage3_best_score = stage2_best_score
    stage3_best_params = deepcopy(base_params)
    for i, combo in enumerate(sig_combos):
        params = merge_params(base_params, combo)
        t0 = time.time()
        result = run_param_backtest(bars, symbol, best_tf, balance, spread_pts, params)
        elapsed = time.time() - t0
        sc = score_result(result)
        wr = result.get("win_rate", 0)
        pf = result.get("profit_factor", 0)
        trades = result.get("total_trades", 0)
        if sc > stage3_best_score:
            stage3_best_score = sc
            stage3_best_params = deepcopy(params)
            flag = " *"
        else:
            flag = ""
        if (i + 1) % 15 == 0 or flag:
            print(f"  [{i+1:3d}/{len(sig_combos)}] buy<{combo['buy_zone']:.2f} sell>{combo['sell_zone']:.2f} vm={combo['vol_mult']:2.0f} WR={wr:5.1f}% PF={pf:5.2f} T={trades:3d} S={sc:+7.2f}{flag} ({elapsed:.1f}s)")
        all_results.append({"stage": 3, "timeframe": best_tf, "params": params, **result, "score": sc})

    print(f"\n  >> Stage 3 best: buy<{stage3_best_params.get('buy_zone')} sell>{stage3_best_params.get('sell_zone')} vm={stage3_best_params.get('vol_mult')} (score={stage3_best_score:+.2f})")
    base_params = deepcopy(stage3_best_params)

    # === STAGE 4: SL/TP Clamps ===
    sltp_combos = expand_grid(STAGE4_SLTP)
    print(f"\n{'='*70}")
    print(f"  STAGE 4: SL/TP Clamps ({len(sltp_combos)} tests)")
    print(f"{'='*70}")

    stage4_best_score = stage3_best_score
    stage4_best_params = deepcopy(base_params)
    for i, combo in enumerate(sltp_combos):
        params = merge_params(base_params, combo)
        t0 = time.time()
        result = run_param_backtest(bars, symbol, best_tf, balance, spread_pts, params)
        elapsed = time.time() - t0
        sc = score_result(result)
        wr = result.get("win_rate", 0)
        pf = result.get("profit_factor", 0)
        trades = result.get("total_trades", 0)
        if sc > stage4_best_score:
            stage4_best_score = sc
            stage4_best_params = deepcopy(params)
            flag = " *"
        else:
            flag = ""
        if (i + 1) % 20 == 0 or flag:
            print(f"  [{i+1:3d}/{len(sltp_combos)}] sl=[{combo['gold_sl_min']},{combo['gold_sl_max']}] tp=[{combo['gold_tp_min']},{combo['gold_tp_max']}] WR={wr:5.1f}% PF={pf:5.2f} T={trades:3d} S={sc:+7.2f}{flag} ({elapsed:.1f}s)")
        all_results.append({"stage": 4, "timeframe": best_tf, "params": params, **result, "score": sc})

    print(f"\n  >> Stage 4 best: sl=[{stage4_best_params.get('gold_sl_min')},{stage4_best_params.get('gold_sl_max')}] tp=[{stage4_best_params.get('gold_tp_min')},{stage4_best_params.get('gold_tp_max')}] (score={stage4_best_score:+.2f})")
    base_params = deepcopy(stage4_best_params)

    # === STAGE 5: Grid & Trailing ===
    gt_combos = expand_grid(STAGE5_GRID_TRAIL)
    print(f"\n{'='*70}")
    print(f"  STAGE 5: Grid & Trailing ({len(gt_combos)} tests)")
    print(f"{'='*70}")

    stage5_best_score = stage4_best_score
    stage5_best_params = deepcopy(base_params)
    for i, combo in enumerate(gt_combos):
        params = merge_params(base_params, combo)
        t0 = time.time()
        result = run_param_backtest(bars, symbol, best_tf, balance, spread_pts, params)
        elapsed = time.time() - t0
        sc = score_result(result)
        wr = result.get("win_rate", 0)
        pf = result.get("profit_factor", 0)
        trades = result.get("total_trades", 0)
        if sc > stage5_best_score:
            stage5_best_score = sc
            stage5_best_params = deepcopy(params)
            flag = " *"
        else:
            flag = ""
        if (i + 1) % 25 == 0 or flag:
            print(f"  [{i+1:3d}/{len(gt_combos)}] cd={combo['grid_cooldown']:2d} be={combo['trail_be_trigger']:.1f} tr={combo['trail_trigger']:.1f} tp={combo['trail_pct']:.1f} WR={wr:5.1f}% PF={pf:5.2f} T={trades:3d} S={sc:+7.2f}{flag} ({elapsed:.1f}s)")
        all_results.append({"stage": 5, "timeframe": best_tf, "params": params, **result, "score": sc})

    print(f"\n  >> Stage 5 best: cd={stage5_best_params.get('grid_cooldown')} be={stage5_best_params.get('trail_be_trigger')} tr={stage5_best_params.get('trail_trigger')} tp={stage5_best_params.get('trail_pct')} (score={stage5_best_score:+.2f})")

    # === STAGE 6: Final Validation ===
    final_params = deepcopy(stage5_best_params)
    print(f"\n{'='*70}")
    print(f"  STAGE 6: Final Validation")
    print(f"{'='*70}")

    final_result = run_param_backtest(bars, symbol, best_tf, balance, spread_pts, final_params)
    final_score = score_result(final_result)

    elapsed_total = time.time() - start_total
    print(f"\n  FINAL RESULT ({elapsed_total:.0f}s total):")
    print(f"  {'─'*50}")
    print(f"  Timeframe:     {best_tf}")
    print(f"  Starting:      ${balance:,.2f}")
    print(f"  Ending:        ${final_result.get('ending_balance', 0):,.2f}")
    print(f"  P&L:           ${final_result.get('total_pnl', 0):+,.2f} ({final_result.get('total_pnl_pct', 0):+.1f}%)")
    print(f"  Win Rate:      {final_result.get('win_rate', 0):.1f}%")
    print(f"  Profit Factor: {final_result.get('profit_factor', 0):.2f}")
    print(f"  Max Drawdown:  {final_result.get('max_drawdown_pct', 0):.1f}%")
    print(f"  Trades:        {final_result.get('total_trades', 0)}")
    print(f"  Score:         {final_score:+.2f}")
    print(f"  {'─'*50}")

    # === TOP 20 RESULTS ===
    all_results.sort(key=lambda x: x.get("score", -999), reverse=True)
    unique_results = []
    seen = set()
    for r in all_results:
        key = json.dumps(r.get("params", {}), sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique_results.append(r)

    print(f"\n  TOP 20 PARAMETER SETS:")
    print(f"  {'#':>3s} {'TF':>4s} {'WR%':>6s} {'PF':>6s} {'PnL%':>7s} {'DD%':>6s} {'Trades':>6s} {'Score':>8s} Parameters")
    print(f"  {'─'*110}")
    for i, r in enumerate(unique_results[:20]):
        wr = r.get("win_rate", 0)
        pf = r.get("profit_factor", 0)
        pnl = r.get("total_pnl_pct", 0)
        dd = r.get("max_drawdown_pct", 0)
        trades = r.get("total_trades", 0)
        sc = r.get("score", 0)
        p = r.get("params", {})
        desc = " ".join(f"{k}={v}" for k, v in p.items()) if p else "defaults"
        print(f"  {i+1:3d} {r.get('timeframe','?'):>4s} {wr:5.1f}% {pf:5.2f} {pnl:+6.1f}% {dd:5.1f}% {trades:5d} {sc:+7.2f}  {desc}")

    # === BEST PARAMS SUMMARY ===
    bp = final_params
    print(f"\n  {'='*70}")
    print(f"  OPTIMAL PARAMETERS FOR scalp.py + backtest_engine.py")
    print(f"  {'='*70}")
    print(f"  HFT[\"{best_tf}\"] = {{")
    print(f"      \"grid_factor\": {bp.get('grid_factor', 0.30)},")
    print(f"      \"sl_ratio\":   {bp.get('sl_ratio', 0.6)},")
    print(f"      \"tp_ratio\":   {bp.get('tp_ratio', 1.5)},")
    print(f"      \"max_orders\": {bp.get('max_orders', 3)},")
    print(f"  }}")
    print(f"  Signal: buy_zone={bp.get('buy_zone', 0.35)} sell_zone={bp.get('sell_zone', 0.65)} vol_mult={bp.get('vol_mult', 15)}")
    print(f"  SL/TP:  sl=[{bp.get('gold_sl_min', 200)},{bp.get('gold_sl_max', 500)}] tp=[{bp.get('gold_tp_min', 150)},{bp.get('gold_tp_max', 750)}]")
    print(f"  Grid:   cooldown={bp.get('grid_cooldown', 15)}")
    print(f"  Trail:  be_trigger={bp.get('trail_be_trigger', 0.7)} trigger={bp.get('trail_trigger', 1.2)} pct={bp.get('trail_pct', 0.5)}")

    # Save results
    output_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                f"optimization_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(output_file, 'w') as f:
        json.dump({
            "symbol": symbol,
            "timeframe": best_tf,
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
    print(f"\n  Total unique tests: {len(unique_results)} in {elapsed_total:.0f}s")

    mt5.shutdown()


if __name__ == "__main__":
    optimize()
