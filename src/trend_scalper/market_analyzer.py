from __future__ import annotations

import logging
from typing import Any

from .indicators import add_indicators
from .models import Rate

logger = logging.getLogger(__name__)

# Default indicator parameters for market structure analysis
_DEFAULT_EMA_FAST = 8
_DEFAULT_EMA_SLOW = 21
_DEFAULT_EMA_TREND = 55
_DEFAULT_ATR_PERIOD = 14
_DEFAULT_RSI_PERIOD = 14


class MarketAnalyzer:
    """Analyzes multi-timeframe market structure for LLM consumption.

    Produces a compact, information-dense JSON summary of the current
    market state across M1, M5, and M15 timeframes, including trend
    direction, volatility, momentum, and key price levels.
    """

    def __init__(
        self,
        ema_fast: int = _DEFAULT_EMA_FAST,
        ema_slow: int = _DEFAULT_EMA_SLOW,
        ema_trend: int = _DEFAULT_EMA_TREND,
        atr_period: int = _DEFAULT_ATR_PERIOD,
        rsi_period: int = _DEFAULT_RSI_PERIOD,
    ) -> None:
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.ema_trend = ema_trend
        self.atr_period = atr_period
        self.rsi_period = rsi_period

    def analyze(self, rates_by_timeframe: dict[str, list[Rate]]) -> dict[str, Any]:
        """Produce a complete multi-timeframe market structure report.

        Args:
            rates_by_timeframe: Mapping of timeframe string (e.g. 'M1', 'M5', 'M15')
                                to lists of OHLC Rate dicts.

        Returns:
            A dict with keys:
              - symbol_hint: str
              - timestamp: str (ISO of latest bar)
              - timeframes: dict of per-timeframe analysis
              - multi_tf_alignment: dict with alignment scores
              - volatility_regime: str (low/medium/high/extreme)
              - summary: str (one-line human-readable summary)
        """
        timeframe_analyses: dict[str, dict[str, Any]] = {}
        latest_timestamp = ""

        for timeframe, rates in rates_by_timeframe.items():
            if len(rates) < max(self.ema_trend, self.atr_period, self.rsi_period) + 5:
                logger.warning("MarketAnalyzer: insufficient bars for %s (%d bars)", timeframe, len(rates))
                continue

            data = add_indicators(
                rates,
                ema_fast=self.ema_fast,
                ema_slow=self.ema_slow,
                ema_trend=self.ema_trend,
                atr_period=self.atr_period,
                rsi_period=self.rsi_period,
            )
            clean = [row for row in data if self._row_ready(row)]
            if len(clean) < 10:
                continue

            timeframe_analyses[timeframe] = self._analyze_timeframe(timeframe, clean)
            if clean:
                latest_ts = str(clean[-1].get("time", ""))
                if latest_ts > latest_timestamp:
                    latest_timestamp = latest_ts

        alignment = self._compute_multi_tf_alignment(timeframe_analyses)
        volatility_regime = self._determine_volatility_regime(timeframe_analyses)
        summary = self._build_summary(timeframe_analyses, alignment, volatility_regime)

        return {
            "symbol_hint": "XAUUSD",
            "timestamp": latest_timestamp,
            "timeframes": timeframe_analyses,
            "multi_tf_alignment": alignment,
            "volatility_regime": volatility_regime,
            "summary": summary,
        }

    def compact_report(self, rates_by_timeframe: dict[str, list[Rate]]) -> dict[str, Any]:
        """Produce the LLM-ready compact payload.

        Returns a token-efficient dict suitable for embedding in the
        DeepSeek prompt alongside the optimization request.
        """
        full = self.analyze(rates_by_timeframe)
        compact: dict[str, Any] = {
            "ts": full["timestamp"],
            "vol_regime": full["volatility_regime"],
            "alignment": full["multi_tf_alignment"],
        }
        for tf, analysis in full["timeframes"].items():
            compact[tf] = {
                "trend": analysis["trend"],
                "trend_strength": analysis["trend_strength"],
                "atr_pct": analysis["atr_pct"],
                "rsi": analysis["rsi"],
                "price_vs_ema": analysis["price_vs_ema"],
                "momentum": analysis["momentum"],
                "candles_5": analysis["candles_5"],
                "s_r_levels": analysis.get("support_resistance", {}),
            }
        compact["summary"] = full["summary"]
        return compact

    def _analyze_timeframe(self, timeframe: str, data: list[Rate]) -> dict[str, Any]:
        """Analyze a single timeframe's data."""
        row = data[-1]
        prev = data[-2]
        close = float(row["close"])
        atr = float(row["atr"])
        ema_f = float(row["ema_fast"])
        ema_s = float(row["ema_slow"])
        ema_t = float(row["ema_trend"])
        rsi = float(row["rsi"])

        # Trend determination
        if ema_f > ema_s > ema_t:
            trend = "bullish"
        elif ema_f < ema_s < ema_t:
            trend = "bearish"
        elif ema_f > ema_s and ema_s < ema_t:
            trend = "bullish_weakening"
        elif ema_f < ema_s and ema_s > ema_t:
            trend = "bearish_weakening"
        else:
            trend = "ranging"

        # Trend strength: distance between fast and trend EMA as % of ATR
        ema_spread = abs(ema_f - ema_t)
        trend_strength = round(min(1.0, ema_spread / max(atr, 1e-9)), 3)

        # ATR as % of price
        atr_pct = round((atr / close) * 100, 4)

        # Price vs EMA position
        price_vs_fast = round((close - ema_f) / max(atr, 1e-9), 2)
        price_vs_slow = round((close - ema_s) / max(atr, 1e-9), 2)
        price_vs_ema = {
            "vs_fast_atr": price_vs_fast,
            "vs_slow_atr": price_vs_slow,
        }

        # Momentum (last 5 bars)
        candles_5 = self._recent_candle_summary(data[-5:])

        # Support and resistance (simple: recent N-bar high/low)
        sr_levels = self._support_resistance(data[-50:], close, atr)

        return {
            "timeframe": timeframe,
            "close": round(close, 2),
            "trend": trend,
            "trend_strength": trend_strength,
            "atr": round(atr, 4),
            "atr_pct": atr_pct,
            "rsi": round(rsi, 1),
            "rsi_zone": self._rsi_zone(rsi),
            "price_vs_ema": price_vs_ema,
            "momentum": round(close - float(data[-5]["close"]) if len(data) >= 5 else 0, 4),
            "candles_5": candles_5,
            "ema_fast": round(ema_f, 2),
            "ema_slow": round(ema_s, 2),
            "ema_trend": round(ema_t, 2),
            "support_resistance": sr_levels,
        }

    def _recent_candle_summary(self, candles: list[Rate]) -> dict[str, Any]:
        """Summarize the last few candles."""
        if not candles:
            return {"count": 0, "bullish": 0, "bearish": 0, "doji": 0, "pattern": "none"}

        bullish = 0
        bearish = 0
        doji = 0
        for candle in candles:
            o = float(candle["open"])
            c = float(candle["close"])
            body = abs(c - o)
            hl_range = float(candle["high"]) - float(candle["low"])
            if hl_range > 0 and body / hl_range < 0.15:
                doji += 1
            elif c > o:
                bullish += 1
            else:
                bearish += 1

        # Pattern detection: 3+ same direction = strong
        if bullish >= 4:
            pattern = "strong_bullish"
        elif bearish >= 4:
            pattern = "strong_bearish"
        elif bullish >= 3:
            pattern = "bullish"
        elif bearish >= 3:
            pattern = "bearish"
        else:
            pattern = "mixed"

        last_c = float(candles[-1]["close"])
        first_o = float(candles[0]["open"])
        net_change = round(last_c - first_o, 4)

        return {
            "count": len(candles),
            "bullish": bullish,
            "bearish": bearish,
            "doji": doji,
            "pattern": pattern,
            "net_change": net_change,
        }

    def _support_resistance(self, bars: list[Rate], current_price: float, atr: float) -> dict[str, Any]:
        """Find nearest support and resistance levels from recent bars."""
        if not bars:
            return {}

        highs = [float(b["high"]) for b in bars]
        lows = [float(b["low"]) for b in bars]

        # Find 2 most recent swing highs and lows (simple: local extrema)
        swing_highs: list[float] = []
        swing_lows: list[float] = []
        for i in range(2, len(bars) - 2):
            if highs[i] > highs[i - 1] and highs[i] > highs[i - 2] and highs[i] > highs[i + 1] and highs[i] > highs[i + 2]:
                swing_highs.append(highs[i])
            if lows[i] < lows[i - 1] and lows[i] < lows[i - 2] and lows[i] < lows[i + 1] and lows[i] < lows[i + 2]:
                swing_lows.append(lows[i])

        # Nearest resistance (above current price)
        resistances = sorted([h for h in swing_highs if h > current_price])[:2]
        # Nearest support (below current price)
        supports = sorted([l for l in swing_lows if l < current_price], reverse=True)[:2]

        return {
            "current_price": round(current_price, 2),
            "nearest_resistance": round(resistances[0], 2) if resistances else None,
            "nearest_support": round(supports[0], 2) if supports else None,
            "resistance_distance_atr": round((resistances[0] - current_price) / max(atr, 1e-9), 2) if resistances else None,
            "support_distance_atr": round((current_price - supports[0]) / max(atr, 1e-9), 2) if supports else None,
        }

    def _rsi_zone(self, rsi: float) -> str:
        if rsi > 70:
            return "overbought"
        if rsi > 60:
            return "bullish"
        if rsi > 40:
            return "neutral"
        if rsi > 30:
            return "bearish"
        return "oversold"

    def _compute_multi_tf_alignment(self, analyses: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Compute how aligned the timeframes are."""
        trends = {tf: a["trend"] for tf, a in analyses.items()}
        trend_values: dict[str, int] = {}
        for tf, trend in trends.items():
            if trend == "bullish":
                trend_values[tf] = 1
            elif trend == "bearish":
                trend_values[tf] = -1
            elif "bullish" in trend:
                trend_values[tf] = 0
            elif "bearish" in trend:
                trend_values[tf] = 0
            else:
                trend_values[tf] = 0

        values = list(trend_values.values())
        if not values:
            return {"score": 0, "label": "unknown", "details": {}}

        # Alignment score: average of absolute agreement
        pairs = []
        timeframes_sorted = sorted(analyses.keys())
        for i in range(len(timeframes_sorted)):
            for j in range(i + 1, len(timeframes_sorted)):
                tf_a = timeframes_sorted[i]
                tf_b = timeframes_sorted[j]
                if tf_a in trend_values and tf_b in trend_values:
                    pairs.append(1 if trend_values[tf_a] == trend_values[tf_b] else 0)

        alignment_score = round(sum(pairs) / len(pairs), 2) if pairs else 0.0

        if alignment_score >= 0.8:
            label = "strongly_aligned"
        elif alignment_score >= 0.5:
            label = "partially_aligned"
        else:
            label = "divergent"

        return {
            "score": alignment_score,
            "label": label,
            "details": trends,
        }

    def _determine_volatility_regime(self, analyses: dict[str, dict[str, Any]]) -> str:
        """Determine the overall volatility regime from all available timeframes."""
        atr_pcts = []
        for tf_analysis in analyses.values():
            atr_pct = tf_analysis.get("atr_pct", 0.0)
            if atr_pct > 0:
                atr_pcts.append(atr_pct)
        if not atr_pcts:
            return "medium"
        avg_atr_pct = sum(atr_pcts) / len(atr_pcts)
        if avg_atr_pct < 0.02:
            return "very_low"
        if avg_atr_pct < 0.04:
            return "low"
        if avg_atr_pct < 0.08:
            return "medium"
        if avg_atr_pct < 0.15:
            return "high"
        return "extreme"

    def _build_summary(
        self,
        analyses: dict[str, dict[str, Any]],
        alignment: dict[str, Any],
        volatility_regime: str,
    ) -> str:
        """Build a one-line human-readable summary."""
        parts: list[str] = []
        for tf in sorted(analyses.keys()):
            a = analyses[tf]
            parts.append(f"{tf}:{a['trend']}({a['rsi']})")
        return (
            f"Vol:{volatility_regime} Align:{alignment['label']} "
            + " ".join(parts)
        )

    def _row_ready(self, row: Rate) -> bool:
        keys = [
            "ema_fast", "ema_slow", "ema_trend",
            "atr", "rsi", "ema_slow_slope", "momentum",
        ]
        return all(row.get(key) is not None for key in keys)