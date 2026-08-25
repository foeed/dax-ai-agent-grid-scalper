"""
DAX V2 M5 Quick Optimizer - Focused Grid
Narrow grid based on M15 learnings. Fast enough to complete.
"""

import sys, os, io, json, time, itertools
from datetime import datetime, timedelta
from copy import deepcopy

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import MetaTrader5 as mt5
from app.services.backtest_engine import Bar, run_backtest, HFT, generate_signal
import app.services.backtest_engine as engine

# Focused grid based on M15 learnings
HFT_GRID = {
    "sl_ratio":   [0.5, 0.6, 0.7, 0.8, 0.9],
    "tp_ratio":   [1.4, 1.5, 1.6, 1.8, 2.0, 2.2],
    "grid_factor": [0.20, 0.25, 0.30, 0.35],
    "max_orders": [2, 3, 4],
}
SIGNAL_GRID = {
    "buy_zone":  [0.20, 0.25, 0.30, 0.35],
    "sell_zone": [0.65, 0.70, 0.75, 0.80],
    "vol_mult":  [8, 10, 12, 15],
}
SLTP_GRID = {
    "gold_sl_min": [150, 200, 250],
    "gold_sl_max": [400, 500, 600],
    "gold_tp_min": [150, 200, 300],
    "gold_tp_max": [600, 750, 900],
}
TRAIL_GRID = {
    "grid_cooldown":    [10, 15, 20],
    "trail_be_trigger": [0.5, 0.6, 0.7],
    "trail_trigger":    [1.0, 1.2, 1.5],
    "trail_pct":        [0.4, 0.5, 0.6],
}

def score(r):
    wr = r.get("win_rate", 0); pf = r.get("profit_factor", 0)
    dd = r.get("max_drawdown_pct", 100); trades = r.get("total_trades", 0); pnl = r.get("total_pnl_pct", 0)
    if pf < 1.0 or trades < 50: return -999
    return wr * 0.3 + pf * 20 * 0.25 + max(0, 100 - dd) * 0.15 + min(trades/100.0, 20.0) * 0.15 + pnl * 0.15

def download_bars(sym, tf_str, days):
    TF = {"M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1}
    rates = mt5.copy_rates_range(sym, TF.get(tf_str, mt5.TIMEFRAME_M5), datetime.now()-timedelta(days=days), datetime.now())
    if rates is None: return []
    return [Bar(timestamp=r['time'], open=r['open'], high=r['high'], low=r['low'], close=r['close'], volume=int(r['tick_volume'])) for r in rates]

def run_bt(bars, sym, tf, bal, spread, params):
    orig_hft = deepcopy(engine.HFT)
    try:
        engine.HFT[tf] = {"grid_factor": params.get("grid_factor",0.25), "sl_ratio": params.get("sl_ratio",0.6),
                          "tp_ratio": params.get("tp_ratio",1.5), "max_orders": params.get("max_orders",3)}
        engine._BUY_ZONE = params.get("buy_zone", 0.35); engine._SELL_ZONE = params.get("sell_zone", 0.65)
        engine._VOL_MULT = params.get("vol_mult", 15); engine._GOLD_SL_MIN = params.get("gold_sl_min", 200)
        engine._GOLD_SL_MAX = params.get("gold_sl_max", 500); engine._GOLD_TP_MIN = params.get("gold_tp_min", 150)
        engine._GOLD_TP_MAX = params.get("gold_tp_max", 750); engine._TRAIL_BE = params.get("trail_be_trigger", 0.7)
        engine._TRAIL_TRIGGER = params.get("trail_trigger", 1.2); engine._TRAIL_PCT = params.get("trail_pct", 0.5)
        cd = params.get("grid_cooldown", 15); _orig = engine.generate_signal
        def _ps(symbol, bid, ask, spread_pts, daily_high, daily_low, account_balance, timeframe="M5"):
            mid = (bid+ask)/2 if ask>0 else bid
            if mid<=0: mid=1.0
            ig = "XAU" in symbol.upper() or "GOLD" in symbol.upper(); pt = 0.01 if ig else 0.00001
            dr = daily_high-daily_low
            if dr<=0: dr=mid*0.005
            vol = dr/mid if mid>0 else 0.005; sp_pct = (spread_pts*pt)/mid*100 if mid>0 else 0
            pir = (mid-daily_low)/dr if dr>0 else 0.5
            tf3 = engine.HFT.get(timeframe, engine.HFT.get("M5",{}))
            am = {"M1":0.03,"M5":0.06,"M15":0.10,"H1":0.18}
            atr = dr*am.get(timeframe,0.06)
            sig="BUY"; conf=0.6; rs=0.30; bz=engine._BUY_ZONE; sz=engine._SELL_ZONE
            if sp_pct<0.15:
                if pir<bz: sig="BUY"; conf=max(0.55,0.85-abs(pir-(bz*0.43))*2)
                elif pir>sz: sig="SELL"; conf=max(0.55,0.85-abs(pir-(1-(1-sz)*0.43))*2)
                else: sig="HOLD"; conf=0.40; rs=0.25
            else: sig="HOLD"; conf=0.30; rs=0.60
            conf=max(0.10,min(0.95,conf)); rs=max(0.10,min(0.95,rs))
            vm=engine._VOL_MULT; sdp=atr*tf3["sl_ratio"]*(1.0+vol*vm)
            msl=mid*0.0003; sdp=max(msl,sdp); tdp=sdp*tf3["tp_ratio"]
            sl=sdp/pt; tp=tdp/pt
            if ig:
                sl=max(engine._GOLD_SL_MIN,min(engine._GOLD_SL_MAX,sl))
                tp=max(engine._GOLD_TP_MIN,min(engine._GOLD_TP_MAX,tp))
                tp=max(tp,int(sl*tf3["tp_ratio"])); tp=min(engine._GOLD_TP_MAX,tp)
            else:
                sl=max(20,min(200,sl)); tp=max(15,min(300,tp)); tp=max(tp,int(sl*tf3["tp_ratio"])); tp=min(300,tp)
            tl=0.02; mg=20 if ig else 5; gsp=atr*tf3["grid_factor"]/pt; gp=int(gsp); gp=max(mg,min(100,gp))
            if ig: rpp=sl*0.01; moc=3
            else: rpp=0.01*(sl/10.0); moc=min(tf3.get("max_orders",8),10)
            mrps=account_balance*0.02; mbr=int(mrps/rpp) if rpp>0 else 3; mo=min(moc,mbr); bo=max(2,mo)
            if sig=="BUY": b=mo;s=0
            elif sig=="SELL": s=mo;b=0
            else: b=0;s=0
            return {"signal":sig,"lot_size":tl,"sl_pts":sl,"tp_pts":tp,"grid_spacing_pts":gp,"buy_orders":b,"sell_orders":s,
                    "is_gold":ig,"point":pt,"confidence":conf,"risk_level":"LOW" if rs<0.35 else "MEDIUM" if rs<0.60 else "HIGH",
                    "volatility":vol,"atr":atr}
        engine.generate_signal = _ps
        r = run_backtest(bars=bars, symbol=sym, timeframe=tf, account_balance=bal, spread_pts=spread, grid_cooldown=cd)
        engine.generate_signal = _orig
        return r
    finally:
        engine.HFT = orig_hft
        for a,v in [('_BUY_ZONE',0.35),('_SELL_ZONE',0.65),('_VOL_MULT',15),('_GOLD_SL_MIN',200),
                     ('_GOLD_SL_MAX',500),('_GOLD_TP_MIN',150),('_GOLD_TP_MAX',750),
                     ('_TRAIL_BE',0.7),('_TRAIL_TRIGGER',1.2),('_TRAIL_PCT',0.5)]: setattr(engine,a,v)

def expand(grid):
    return [dict(zip(grid.keys(),c)) for c in itertools.product(*grid.values())]

def merge(base, ov): r=deepcopy(base); r.update(ov); return r

def optimize():
    sym="XAUUSD.m"; tf="M5"; bal=10000.0; sp=20; days=90
    print(f"\n{'='*70}\n  M5 QUICK OPTIMIZER\n  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n{'='*70}")
    if not mt5.initialize(): print("ERROR"); sys.exit(1)
    info=mt5.account_info(); print(f"  MT5: {info.server} | Account: {info.login}")
    print(f"\n  Downloading {days} days M5...")
    bars=download_bars(sym,tf,days); print(f"  {len(bars)} bars")
    if not bars: print("ERROR"); sys.exit(1)

    all_r=[]; t0=time.time()

    # Baseline
    r=run_bt(bars,sym,tf,bal,sp,{}); s=score(r)
    print(f"\n  Baseline: WR={r['win_rate']:.1f}% PF={r['profit_factor']:.2f} PnL={r['total_pnl_pct']:+.1f}% DD={r['max_drawdown_pct']:.1f}% T={r['total_trades']} S={s:+.2f}")
    all_r.append({"stage":0,"params":{},**r,"score":s})
    best_s=s; best_p={}

    # Stage 2: HFT (5*6*4*3 = 360 combos)
    hft=expand(HFT_GRID); print(f"\n  STAGE 2: HFT ({len(hft)} combos)")
    s2s=best_s; s2p=deepcopy(best_p)
    for i,c in enumerate(hft):
        t1=time.time(); r=run_bt(bars,sym,tf,bal,sp,c); el=time.time()-t1; s=score(r)
        if s>s2s: s2s=s; s2p=deepcopy(c); f=" *"
        else: f=""
        if (i+1)%50==0 or f==" *":
            print(f"  [{i+1:3d}/{len(hft)}] sl={c['sl_ratio']:.1f} tp={c['tp_ratio']:.1f} gf={c['grid_factor']:.2f} mo={c['max_orders']} WR={r['win_rate']:5.1f}% PF={r['profit_factor']:5.2f} T={r['total_trades']:4d} PnL={r['total_pnl_pct']:+6.1f}% S={s:+7.2f}{f} ({el:.1f}s)")
        all_r.append({"stage":2,"params":c,**r,"score":s})
    print(f"\n  >> Stage 2 best: {s2p} (score={s2s:+.2f})")
    bp=merge(s2p,{})

    # Stage 3: Signal (4*4*4 = 64 combos)
    sig=expand(SIGNAL_GRID); print(f"\n  STAGE 3: Signal ({len(sig)} combos)")
    s3s=s2s; s3p=deepcopy(bp)
    for i,c in enumerate(sig):
        p=merge(bp,c); t1=time.time(); r=run_bt(bars,sym,tf,bal,sp,p); el=time.time()-t1; s=score(r)
        if s>s3s: s3s=s; s3p=deepcopy(p); f=" *"
        else: f=""
        if (i+1)%16==0 or f==" *":
            print(f"  [{i+1:2d}/{len(sig)}] buy<{c['buy_zone']:.2f} sell>{c['sell_zone']:.2f} vm={c['vol_mult']:2.0f} WR={r['win_rate']:5.1f}% PF={r['profit_factor']:5.2f} T={r['total_trades']:4d} PnL={r['total_pnl_pct']:+6.1f}% S={s:+7.2f}{f} ({el:.1f}s)")
        all_r.append({"stage":3,"params":p,**r,"score":s})
    print(f"\n  >> Stage 3 best: buy<{s3p.get('buy_zone')} sell>{s3p.get('sell_zone')} vm={s3p.get('vol_mult')} (score={s3s:+.2f})")
    bp=deepcopy(s3p)

    # Stage 4: SL/TP (3*3*3*3 = 81 combos)
    sltp=expand(SLTP_GRID); print(f"\n  STAGE 4: SL/TP ({len(sltp)} combos)")
    s4s=s3s; s4p=deepcopy(bp)
    for i,c in enumerate(sltp):
        p=merge(bp,c); t1=time.time(); r=run_bt(bars,sym,tf,bal,sp,p); el=time.time()-t1; s=score(r)
        if s>s4s: s4s=s; s4p=deepcopy(p); f=" *"
        else: f=""
        if (i+1)%20==0 or f==" *":
            print(f"  [{i+1:2d}/{len(sltp)}] sl=[{c['gold_sl_min']},{c['gold_sl_max']}] tp=[{c['gold_tp_min']},{c['gold_tp_max']}] WR={r['win_rate']:5.1f}% PF={r['profit_factor']:5.2f} T={r['total_trades']:4d} PnL={r['total_pnl_pct']:+6.1f}% S={s:+7.2f}{f} ({el:.1f}s)")
        all_r.append({"stage":4,"params":p,**r,"score":s})
    print(f"\n  >> Stage 4 best: sl=[{s4p.get('gold_sl_min')},{s4p.get('gold_sl_max')}] tp=[{s4p.get('gold_tp_min')},{s4p.get('gold_tp_max')}] (score={s4s:+.2f})")
    bp=deepcopy(s4p)

    # Stage 5: Grid+Trail (3*3*3*3 = 81 combos)
    gt=expand(TRAIL_GRID); print(f"\n  STAGE 5: Grid+Trail ({len(gt)} combos)")
    s5s=s4s; s5p=deepcopy(bp)
    for i,c in enumerate(gt):
        p=merge(bp,c); t1=time.time(); r=run_bt(bars,sym,tf,bal,sp,p); el=time.time()-t1; s=score(r)
        if s>s5s: s5s=s; s5p=deepcopy(p); f=" *"
        else: f=""
        if (i+1)%20==0 or f==" *":
            print(f"  [{i+1:2d}/{len(gt)}] cd={c['grid_cooldown']:2d} be={c['trail_be_trigger']:.1f} tr={c['trail_trigger']:.1f} tp={c['trail_pct']:.1f} WR={r['win_rate']:5.1f}% PF={r['profit_factor']:5.2f} T={r['total_trades']:4d} PnL={r['total_pnl_pct']:+6.1f}% S={s:+7.2f}{f} ({el:.1f}s)")
        all_r.append({"stage":5,"params":p,**r,"score":s})
    print(f"\n  >> Stage 5 best: cd={s5p.get('grid_cooldown')} be={s5p.get('trail_be_trigger')} tr={s5p.get('trail_trigger')} tp={s5p.get('trail_pct')} (score={s5s:+.2f})")

    # Final
    fp=deepcopy(s5p); print(f"\n  STAGE 6: Final Validation")
    fr=run_bt(bars,sym,tf,bal,sp,fp); fs=score(fr)
    elapsed=time.time()-t0
    print(f"\n  FINAL RESULT ({elapsed:.0f}s):")
    print(f"  {'─'*55}")
    print(f"  TF: {tf} | Start: ${bal:,.2f} | End: ${fr.get('ending_balance',0):,.2f}")
    print(f"  PnL: ${fr.get('total_pnl',0):+,.2f} ({fr.get('total_pnl_pct',0):+.1f}%)")
    print(f"  WR: {fr.get('win_rate',0):.1f}% | PF: {fr.get('profit_factor',0):.2f}")
    print(f"  DD: {fr.get('max_drawdown_pct',0):.1f}% | Trades: {fr.get('total_trades',0)}")
    print(f"  Score: {fs:+.2f}")
    print(f"  {'─'*55}")

    # Top 20
    all_r.sort(key=lambda x:x.get("score",-999),reverse=True)
    seen=set(); uniq=[]
    for r in all_r:
        k=json.dumps(r.get("params",{}),sort_keys=True)
        if k not in seen: seen.add(k); uniq.append(r)
    print(f"\n  TOP 20:")
    print(f"  {'#':>3s} {'WR%':>6s} {'PF':>6s} {'PnL%':>7s} {'DD%':>6s} {'T':>5s} {'S':>8s} Parameters")
    print(f"  {'─'*95}")
    for i,r in enumerate(uniq[:20]):
        p=r.get("params",{}); d=" ".join(f"{k}={v}" for k,v in p.items()) if p else "defaults"
        print(f"  {i+1:3d} {r.get('win_rate',0):5.1f}% {r.get('profit_factor',0):5.2f} {r.get('total_pnl_pct',0):+6.1f}% {r.get('max_drawdown_pct',0):5.1f}% {r.get('total_trades',0):5d} {r.get('score',0):+7.2f}  {d}")

    bp2=fp
    print(f"\n  OPTIMAL M5 PARAMETERS:")
    print(f'  HFT["M5"] = {{"grid_factor":{bp2.get("grid_factor",0.25)},"sl_ratio":{bp2.get("sl_ratio",0.6)},"tp_ratio":{bp2.get("tp_ratio",1.5)},"max_orders":{bp2.get("max_orders",3)}}}')
    print(f'  Signal: buy_zone={bp2.get("buy_zone",0.35)} sell_zone={bp2.get("sell_zone",0.65)} vol_mult={bp2.get("vol_mult",15)}')
    print(f'  SL/TP: sl=[{bp2.get("gold_sl_min",200)},{bp2.get("gold_sl_max",500)}] tp=[{bp2.get("gold_tp_min",150)},{bp2.get("gold_tp_max",750)}]')
    print(f'  Grid: cooldown={bp2.get("grid_cooldown",15)} Trail: be={bp2.get("trail_be_trigger",0.7)} trigger={bp2.get("trail_trigger",1.2)} pct={bp2.get("trail_pct",0.5)}')

    out=os.path.join(os.path.dirname(os.path.abspath(__file__)),f"optimize_m5_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(out,'w') as f:
        json.dump({"symbol":sym,"timeframe":tf,"days":days,"balance":bal,"best_params":fp,
                    "final_result":{k:v for k,v in fr.items() if k!="equity_curve"},"final_score":fs,
                    "top_20":[{k:v for k,v in r.items() if k!="equity_curve"} for r in uniq[:20]],
                    "total_tests":len(uniq),"elapsed_seconds":elapsed},f,indent=2,default=str)
    print(f"\n  Saved: {out}")
    print(f"  Total: {len(uniq)} unique tests in {elapsed:.0f}s")
    mt5.shutdown()

if __name__=="__main__": optimize()
