from __future__ import annotations

from .config import Settings
from .indicators import add_indicators
from .models import Rate, TradeSignal


class TrendScalperStrategy:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def analyze(self, rates: list[Rate], point: float) -> TradeSignal:
        if len(rates) < self.settings.bars // 2:
            return TradeSignal("HOLD", 0.0, "Not enough bars")

        data = add_indicators(
            rates,
            ema_fast=self.settings.ema_fast,
            ema_slow=self.settings.ema_slow,
            ema_trend=self.settings.ema_trend,
            atr_period=self.settings.atr_period,
            rsi_period=self.settings.rsi_period,
        )
        data = [row for row in data if self._row_ready(row)]

        if len(data) < 10:
            return TradeSignal("HOLD", 0.0, "Not enough clean indicator data")

        row = data[-2]
        previous = data[-3]
        atr = float(row["atr"])
        if atr <= 0:
            return TradeSignal("HOLD", 0.0, "ATR is not usable")

        buy_score, buy_reasons = self._score_buy(row, previous)
        sell_score, sell_reasons = self._score_sell(row, previous)

        if buy_score > sell_score:
            action = "BUY"
            score = buy_score
            reasons = buy_reasons
        elif sell_score > buy_score:
            action = "SELL"
            score = sell_score
            reasons = sell_reasons
        else:
            return TradeSignal("HOLD", 0.0, "No directional edge")

        confidence = min(0.95, round(score / 5.0, 3))
        if confidence < self.settings.min_signal_confidence:
            return TradeSignal(
                "HOLD",
                confidence,
                f"Signal below threshold: {', '.join(reasons)}",
                metadata=self._metadata(row),
            )

        min_stop_distance = self.settings.min_stop_points * point
        sl_distance = max(atr * self.settings.sl_atr_multiplier, min_stop_distance)
        tp_distance = max(atr * self.settings.tp_atr_multiplier, min_stop_distance)

        return TradeSignal(
            action,
            confidence,
            ", ".join(reasons),
            sl_distance=sl_distance,
            tp_distance=tp_distance,
            metadata=self._metadata(row),
        )

    def _score_buy(self, row: Rate, previous: Rate) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []

        if row["ema_fast"] > row["ema_slow"] > row["ema_trend"]:
            score += 1.4
            reasons.append("EMA stack bullish")
        if row["close"] > row["ema_fast"]:
            score += 0.8
            reasons.append("price above fast EMA")
        if row["ema_slow_slope"] > 0:
            score += 0.9
            reasons.append("trend slope rising")
        if 48 <= row["rsi"] <= 68:
            score += 0.8
            reasons.append("RSI supports momentum")
        if row["momentum"] > 0:
            score += 0.6
            reasons.append("short momentum positive")
        if row["close"] > row["open"] and previous["close"] > previous["open"]:
            score += 0.5
            reasons.append("recent candles bullish")

        return score, reasons

    def _score_sell(self, row: Rate, previous: Rate) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []

        if row["ema_fast"] < row["ema_slow"] < row["ema_trend"]:
            score += 1.4
            reasons.append("EMA stack bearish")
        if row["close"] < row["ema_fast"]:
            score += 0.8
            reasons.append("price below fast EMA")
        if row["ema_slow_slope"] < 0:
            score += 0.9
            reasons.append("trend slope falling")
        if 32 <= row["rsi"] <= 52:
            score += 0.8
            reasons.append("RSI supports downside")
        if row["momentum"] < 0:
            score += 0.6
            reasons.append("short momentum negative")
        if row["close"] < row["open"] and previous["close"] < previous["open"]:
            score += 0.5
            reasons.append("recent candles bearish")

        return score, reasons

    def _metadata(self, row: Rate) -> dict[str, float]:
        keys = ["close", "ema_fast", "ema_slow", "ema_trend", "atr", "rsi", "momentum"]
        return {key: round(float(row[key]), 6) for key in keys}

    def _row_ready(self, row: Rate) -> bool:
        keys = [
            "ema_fast",
            "ema_slow",
            "ema_trend",
            "atr",
            "rsi",
            "ema_slow_slope",
            "momentum",
        ]
        return all(row.get(key) is not None for key in keys)
