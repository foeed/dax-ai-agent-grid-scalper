from __future__ import annotations

from .models import Rate


def add_indicators(
    rates: list[Rate],
    ema_fast: int,
    ema_slow: int,
    ema_trend: int,
    atr_period: int,
    rsi_period: int,
) -> list[Rate]:
    _validate_rates(rates)

    close = [float(rate["close"]) for rate in rates]
    high = [float(rate["high"]) for rate in rates]
    low = [float(rate["low"]) for rate in rates]
    open_values = [float(rate["open"]) for rate in rates]

    ema_fast_values = _ema(close, ema_fast)
    ema_slow_values = _ema(close, ema_slow)
    ema_trend_values = _ema(close, ema_trend)
    atr_values = _atr(high, low, close, atr_period)
    rsi_values = _rsi(close, rsi_period)

    data: list[Rate] = []
    for index, rate in enumerate(rates):
        row = dict(rate)
        row["open"] = open_values[index]
        row["high"] = high[index]
        row["low"] = low[index]
        row["close"] = close[index]
        row["ema_fast"] = ema_fast_values[index]
        row["ema_slow"] = ema_slow_values[index]
        row["ema_trend"] = ema_trend_values[index]
        row["atr"] = atr_values[index]
        row["rsi"] = rsi_values[index]
        row["ema_slow_slope"] = (
            ema_slow_values[index] - ema_slow_values[index - 5] if index >= 5 else None
        )
        row["momentum"] = close[index] - close[index - 3] if index >= 3 else None
        data.append(row)

    return data


def _validate_rates(rates: list[Rate]) -> None:
    required = {"open", "high", "low", "close"}
    if not rates:
        raise ValueError("No OHLC rates supplied")
    missing = required.difference(rates[0].keys())
    if missing:
        raise ValueError(f"Missing OHLC columns: {', '.join(sorted(missing))}")


def _sma(values: list[float], period: int) -> float:
    return sum(values[:period]) / period


def _ema(values: list[float], period: int) -> list[float]:
    alpha = 2 / (period + 1)
    output: list[float] = []
    seed = _sma(values[:period], period) if len(values) >= period else values[0]
    for i, value in enumerate(values):
        if i == 0:
            output.append(seed)
            current = seed
            continue
        current = (value * alpha) + (current * (1 - alpha))
        output.append(current)
    return output


def _atr(high: list[float], low: list[float], close: list[float], period: int) -> list[float]:
    true_ranges: list[float] = []
    for index, high_value in enumerate(high):
        previous_close = close[index - 1] if index > 0 else close[index]
        true_ranges.append(
            max(
                high_value - low[index],
                abs(high_value - previous_close),
                abs(low[index] - previous_close),
            )
        )
    return _wilders(true_ranges, period)


def _rsi(close: list[float], period: int) -> list[float | None]:
    gains = [0.0]
    losses = [0.0]
    for index in range(1, len(close)):
        delta = close[index] - close[index - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    average_gains = _wilders(gains, period)
    average_losses = _wilders(losses, period)
    values: list[float | None] = []
    for index, average_gain in enumerate(average_gains):
        average_loss = average_losses[index]
        if index < period:
            values.append(None)
        elif average_loss < 1e-10:
            values.append(100.0)
        else:
            relative_strength = average_gain / average_loss
            values.append(100 - (100 / (1 + relative_strength)))
    return values


def _wilders(values: list[float], period: int) -> list[float]:
    alpha = 1 / period
    output: list[float] = []
    seed = _sma(values[:period], period) if len(values) >= period else values[0]
    for i, value in enumerate(values):
        if i == 0:
            output.append(seed)
            current = seed
            continue
        current = (value * alpha) + (current * (1 - alpha))
        output.append(current)
    return output
