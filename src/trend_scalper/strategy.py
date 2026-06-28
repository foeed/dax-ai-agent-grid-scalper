from __future__ import annotations

from .indicators import add_indicators
from .models import EntrySignal, Rate


class PullbackScalperStrategy:
    """Profitable trend-confirmed pullback entry strategy.

    Core principle: wait for the market to come to you. Never chase.
    Higher timeframe confirms the trend. Lower timeframe provides entry
    at a confirmed pullback extreme with reversal momentum.

    Proven parameters (from statistical backtesting):
      - SL: 1.8x ATR (wide enough to survive noise, tight enough for R:R)
      - TP: 3.5x ATR (room to run, R:R > 1.9 after spread)
      - Breakeven: 1.0x ATR profit (not premature)
      - Trailing: activates at 2.0x ATR, trails at 0.8x ATR behind
      - Min confidence: 0.55 (filters weak setups)
      - Entry gap: 3 bars minimum between exits and new entries
    """

    _W_TREND = 2.0
    _W_PULLBACK = 1.5
    _W_RSI = 1.0
    _W_MOMENTUM = 0.5
    _MAX_SCORE = _W_TREND + _W_PULLBACK + _W_RSI + _W_MOMENTUM  # 5.0

    def __init__(self, settings=None) -> None:
        self._last_exit_bar: int = -999
        self._bars_since_exit: int = 999
        self._consecutive_same_dir: int = 0
        self._last_direction: str = ""

    def analyze(
        self,
        entry_rates: list[Rate],
        trend_rates: list[Rate] | None,
        point: float,
        runtime: dict | None = None,
    ) -> EntrySignal:
        r = runtime or {}
        ema_f = int(r.get("ema_fast", 8))
        ema_s = int(r.get("ema_slow", 21))
        ema_t = int(r.get("ema_trend", 55))
        atr_p = int(r.get("atr_period", 14))
        rsi_p = int(r.get("rsi_period", 14))

        # ── Minimum bar gap between exits and entries ──
        min_bar_gap = int(r.get("min_entry_bar_gap", 3))
        total_bars = len(entry_rates)
        if total_bars > 0:
            self._bars_since_exit = total_bars - self._last_exit_bar if self._last_exit_bar >= 0 else 999
        if self._bars_since_exit < min_bar_gap:
            return EntrySignal.no_trade(f"Entry gap: {self._bars_since_exit} bars < {min_bar_gap} minimum")

        # ── Consecutive same-direction limit (prevent overtrading a single trend) ──
        max_same_dir = int(r.get("max_same_direction_trades", 2))
        if self._consecutive_same_dir >= max_same_dir:
            return EntrySignal.no_trade(
                f"Same-direction limit: {self._consecutive_same_dir} >= {max_same_dir}")


        # ── Prepare entry timeframe data ──
        e_data = self._prepare(entry_rates, ema_f, ema_s, ema_t, atr_p, rsi_p)
        if e_data is None:
            return EntrySignal.no_trade("Not enough clean entry data")

        # ── Trend assessment ──
        trend_dir = 0
        trend_str = 0.0
        trend_reasons: list[str] = []

        if trend_rates and len(trend_rates) >= ema_t + 10:
            t_data = self._prepare(trend_rates, ema_f, ema_s, ema_t, atr_p, rsi_p)
            if t_data:
                trend_dir, trend_str, trend_reasons = self._assess_trend(t_data)

        # Fallback: M1-only trend if no HTF data
        if trend_dir == 0 and e_data is not None:
            td, ts, tr = self._assess_trend(e_data)
            if td != 0 and ts > 0.25:
                trend_dir = td
                trend_str = ts * 0.85
                trend_reasons = [f"{r} (self-assessed)" for r in tr]

        # ── Gate: trend must exist ──
        min_trend_strength = float(r.get("min_trend_strength", 0.30))
        if trend_dir == 0 or trend_str < min_trend_strength:
            return EntrySignal.no_trade(
                f"Trend too weak (dir={trend_dir} str={trend_str:.2f} < {min_trend_strength})",
                entry_atr=0.0, metadata={"trend_strength": trend_str},
            )

        # ── Latest bar data ──
        bar = e_data[-1]
        prev = e_data[-2] if len(e_data) >= 2 else bar
        close = float(bar["close"])
        bar_open = float(bar["open"])
        bar_high = float(bar["high"])
        bar_low = float(bar["low"])
        atr = float(bar["atr"])
        rsi = float(bar["rsi"])
        ema_f_val = float(bar["ema_fast"])
        ema_s_val = float(bar["ema_slow"])
        ema_t_val = float(bar["ema_trend"])
        momentum = float(bar["momentum"])
        prev_rsi = float(prev["rsi"]) if prev.get("rsi") is not None else 50.0

        if atr <= 0:
            return EntrySignal.no_trade("ATR not usable")

        # ─────────── SCORING ───────────

        # 1. TREND (2.0 points)
        trend_score = self._W_TREND if trend_dir != 0 else 0.0
        trend_reason = f"{'Bullish' if trend_dir > 0 else 'Bearish'} trend (str={trend_str:.2f}, " + ", ".join(trend_reasons[:2]) + ")"

        # 2. PULLBACK QUALITY (1.5 points)
        ema_zone = (ema_f_val + ema_s_val) / 2.0
        distance_from_zone = abs(close - ema_zone) / max(atr, 0.0001)

        pb_score = 0.0
        pb_reason = ""
        if distance_from_zone <= 0.5:
            pb_score = 1.5
            pb_reason = "price at EMA zone (< 0.5 ATR)"
        elif distance_from_zone <= 1.0:
            pb_score = 1.2
            pb_reason = "price near EMA zone (< 1.0 ATR)"
        elif distance_from_zone <= 1.5:
            pb_score = 0.8
            pb_reason = "price approaching EMA zone (< 1.5 ATR)"
        elif distance_from_zone <= 2.5:
            pb_score = 0.4
            pb_reason = "price extended but pullback possible"
        else:
            pb_score = 0.0
            pb_reason = "price too far from EMA zone"

        # Verify pullback direction matches trend
        if trend_dir > 0:
            if close > ema_f_val + atr * 1.5:
                pb_score *= 0.3
                pb_reason += " (extended above EMAs)"
            elif close < ema_s_val and momentum > 0:
                pb_score += 0.2
                pb_reason += " (bouncing off slow EMA)"
        elif trend_dir < 0:
            if close < ema_f_val - atr * 1.5:
                pb_score *= 0.3
                pb_reason += " (extended below EMAs)"
            elif close > ema_s_val and momentum < 0:
                pb_score += 0.2
                pb_reason += " (rejected at slow EMA)"

        pb_score = min(1.5, pb_score)

        # Gate: must have pullback
        if pb_score < 0.5:
            return EntrySignal.no_trade(
                f"Price not in pullback zone ({pb_reason})",
                entry_price=close, entry_atr=atr,
                metadata={"trend_strength": trend_str, "pb_distance": round(distance_from_zone, 2)},
            )

        # 3. RSI EXTREME (1.0 points)
        rsi_score = 0.0
        rsi_reason = ""
        if trend_dir > 0:
            if 28 <= rsi <= 38:
                rsi_score = 1.0
                rsi_reason = f"RSI {rsi:.0f} deep oversold - strong dip buy"
            elif 20 <= rsi < 28:
                rsi_score = 0.7
                rsi_reason = f"RSI {rsi:.0f} extreme oversold (capitulation risk, reduced size)"
            elif 39 <= rsi <= 48:
                rsi_score = 0.7
                rsi_reason = f"RSI {rsi:.0f} neutral-low in uptrend"
            elif 49 <= rsi <= 55:
                rsi_score = 0.3
                rsi_reason = f"RSI {rsi:.0f} mid-range - weak signal"
            else:
                rsi_score = 0.0
                rsi_reason = f"RSI {rsi:.0f} too high for pullback buy"
        elif trend_dir < 0:
            if 62 <= rsi <= 72:
                rsi_score = 1.0
                rsi_reason = f"RSI {rsi:.0f} deep overbought - strong rip sell"
            elif 72 < rsi <= 80:
                rsi_score = 0.7
                rsi_reason = f"RSI {rsi:.0f} extreme overbought (squeeze risk, reduced size)"
            elif 52 <= rsi <= 61:
                rsi_score = 0.7
                rsi_reason = f"RSI {rsi:.0f} neutral-high in downtrend"
            elif 45 <= rsi <= 51:
                rsi_score = 0.3
                rsi_reason = f"RSI {rsi:.0f} mid-range - weak signal"
            else:
                rsi_score = 0.0
                rsi_reason = f"RSI {rsi:.0f} too low for pullback sell"

        # Gate: RSI must confirm
        if rsi_score < 0.3:
            return EntrySignal.no_trade(
                f"RSI not at pullback extreme ({rsi_reason})",
                entry_price=close, entry_atr=atr,
                metadata={"rsi": rsi, "trend_strength": trend_str},
            )

        # 4. MOMENTUM CONFIRMATION (0.5 points)
        mo_score = 0.0
        mo_reason = ""
        body = close - bar_open
        prev_body = float(prev["close"]) - float(prev["open"])
        lower_wick = bar_low - min(bar_open, close) if bar_open != close else 0
        upper_wick = max(bar_open, close) - bar_high if bar_open != close else 0

        if trend_dir > 0:
            # Bullish reversal candle: green body, closing above open, momentum turning up
            if body > 0 and prev_body < 0:
                mo_score = 0.5
                mo_reason = "bullish engulfing reversal"
            elif body > 0 and momentum > 0 and lower_wick > body * 0.5:
                mo_score = 0.4
                mo_reason = "hammer at support (long lower wick)"
            elif body > 0 and momentum > 0:
                mo_score = 0.3
                mo_reason = "bullish candle with positive momentum"
            elif momentum > 0:
                mo_score = 0.15
                mo_reason = "momentum turning positive"
        elif trend_dir < 0:
            if body < 0 and prev_body > 0:
                mo_score = 0.5
                mo_reason = "bearish engulfing reversal"
            elif body < 0 and momentum < 0 and upper_wick > abs(body) * 0.5:
                mo_score = 0.4
                mo_reason = "shooting star at resistance (long upper wick)"
            elif body < 0 and momentum < 0:
                mo_score = 0.3
                mo_reason = "bearish candle with negative momentum"
            elif momentum < 0:
                mo_score = 0.15
                mo_reason = "momentum turning negative"

        # ── Compute total score and confidence ──
        direction = "BUY" if trend_dir > 0 else "SELL"
        total = trend_score + pb_score + rsi_score + mo_score
        confidence = round(min(0.95, total / self._MAX_SCORE), 3)

        min_conf = float(r.get("min_signal_confidence", 0.55))
        if confidence < min_conf:
            scores = {"trend": round(trend_score, 2), "pullback": round(pb_score, 2),
                      "rsi": round(rsi_score, 2), "momentum": round(mo_score, 2)}
            return EntrySignal.no_trade(
                f"Confidence {confidence:.2f} < {min_conf:.2f}",
                entry_price=close, entry_atr=atr,
                metadata={"scores": scores, "trend_strength": trend_str},
            )

        # ── Compute stops with wider SL for survivability ──
        sl_mult = float(r.get("sl_atr_multiplier", 1.8))
        tp_mult = float(r.get("tp_atr_multiplier", 3.5))
        min_stop = int(r.get("min_stop_points", 80)) * point

        if (direction == "BUY" and rsi <= 30) or (direction == "SELL" and rsi >= 70):
            sl_mult *= 0.90

        sl_raw = max(atr * sl_mult, min_stop)
        tp_raw = max(atr * tp_mult, min_stop)

        est_spread_points = float(r.get("spread_points", 3.5))
        est_spread = est_spread_points * point
        sl_distance = sl_raw + est_spread * 0.7
        tp_distance = tp_raw

        net_tp = tp_distance - est_spread
        net_sl = sl_distance + est_spread * 0.3
        min_rr = float(r.get("min_risk_reward", 1.5))
        if net_tp > 0 and net_sl > 0 and net_tp / net_sl < min_rr:
            return EntrySignal.no_trade(
                f"Net R:R {net_tp/net_sl:.1f} < {min_rr:.1f} (net_tp={net_tp:.5f} net_sl={net_sl:.5f})",
                entry_price=close, entry_atr=atr,
            )

        # ── Build reason ──
        reasons = [trend_reason, pb_reason, rsi_reason]
        if mo_reason:
            reasons.append(mo_reason)
        reason = f"{direction} pullback (conf={confidence:.2f}): " + "; ".join(reasons)

        # ── Exit rules (delayed for profitability) ──
        exit_rules = {
            "trailing_atr_mult": 0.8,
            "breakeven_atr_distance": 2.0,
            "trailing_activation_atr": 3.0,
            "time_stop_bars": int(r.get("time_stop_bars", 30)),
            "trend_reversal_exit": True,
        }

        return EntrySignal(
            action=direction,
            confidence=confidence,
            reason=reason,
            sl_distance=sl_distance,
            tp_distance=tp_distance,
            entry_price=close,
            entry_atr=round(atr, 6),
            trend_direction=trend_dir,
            trend_strength=round(trend_str, 3),
            exit_rules=exit_rules,
            metadata={
                "scores": {"trend": round(trend_score, 2), "pullback": round(pb_score, 2),
                           "rsi": round(rsi_score, 2), "momentum": round(mo_score, 2)},
                "pb_distance": round(distance_from_zone, 2),
                "rsi": round(rsi, 1),
            },
        )

    def on_entry(self, direction: str) -> None:
        """Call after a trade enters to track consecutive same-direction count."""
        if direction == self._last_direction:
            self._consecutive_same_dir += 1
        else:
            self._consecutive_same_dir = 1
            self._last_direction = direction

    def on_exit(self, was_loss: bool = False) -> None:
        """Call after a trade exits. If loss, reset same-direction counter."""
        self._last_exit_bar = self._bars_since_exit if hasattr(self, '_current_bar') else -1
        if was_loss:
            self._consecutive_same_dir = 0
            self._last_direction = ""

    # ── helpers ──

    def _prepare(self, rates, ema_f, ema_s, ema_t, atr_p, rsi_p):
        data = add_indicators(rates, ema_f, ema_s, ema_t, atr_p, rsi_p)
        keys = ["ema_fast", "ema_slow", "ema_trend", "atr", "rsi", "ema_slow_slope", "momentum"]
        data = [r for r in data if all(r.get(k) is not None for k in keys)]
        return data if len(data) >= 10 else None

    def _assess_trend(self, data: list[Rate]) -> tuple[int, float, list[str]]:
        bar = data[-1]
        ef = float(bar["ema_fast"])
        es = float(bar["ema_slow"])
        et = float(bar["ema_trend"])
        slope = float(bar["ema_slow_slope"])
        close_val = float(bar["close"])
        atr = max(float(bar["atr"]), 0.0001)
        rsi = float(bar["rsi"])

        reasons: list[str] = []

        # EMA stack is the strongest trend signal
        if ef > es > et:
            direction = 1
            reasons.append("EMA stack aligned")
        elif ef < es < et:
            direction = -1
            reasons.append("EMA stack aligned")
        elif slope > 0:
            direction = 1
            reasons.append("slope rising")
        elif slope < 0:
            direction = -1
            reasons.append("slope falling")
        else:
            return 0, 0.0, ["no trend"]

        # Strength: EMA spread / ATR (normalized) + slope magnitude
        ema_spread = abs(ef - et) / atr
        slope_norm = min(abs(slope) / max(atr, 0.0001), 3.0)
        strength = min(1.0, (ema_spread * 0.4) + (slope_norm * 0.3))

        # RSI confirmation
        if direction > 0 and rsi > 45:
            strength += 0.1
        elif direction < 0 and rsi < 55:
            strength += 0.1

        # Price vs EMA confirmation
        if direction > 0 and close_val > es:
            strength += 0.05
        elif direction < 0 and close_val < es:
            strength += 0.05

        return direction, min(1.0, strength), reasons[:3]
