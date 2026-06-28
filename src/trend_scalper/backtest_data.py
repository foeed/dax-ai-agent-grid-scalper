from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import Rate


class HistoricalDataGenerator:
    """Generate realistic synthetic OHLC data for backtesting.

    Uses a random walk with:
    - Realistic volatility per symbol/timeframe
    - Trend/range/volatility regime shifts
    - Session-based volume and volatility (London/NY active hours)
    - Weekend gaps
    - Mean-reversion tendencies
    """

    # Realistic ATR ranges per symbol (M1 values, scaled for other TFs)
    SYMBOL_CONFIGS: dict[str, dict[str, Any]] = {
        "XAUUSD": {
            "base_price": 2350.0,
            "atr_m1": 0.35,
            "daily_range": 25.0,
            "spread_points": 3.5,
            "point": 0.01,
            "vol_of_vol": 0.3,
            "weekend_gap_pct": 0.001,
        },
        "EURUSD": {
            "base_price": 1.0750,
            "atr_m1": 0.00006,
            "daily_range": 0.0050,
            "spread_points": 1.5,
            "point": 0.00001,
            "vol_of_vol": 0.25,
            "weekend_gap_pct": 0.0005,
        },
    }

    # Timeframe scaling: ATR multiplier vs M1
    TF_SCALE: dict[str, float] = {
        "M1": 1.0,
        "M5": 2.5,
        "M15": 5.0,
        "H1": 12.0,
    }

    def __init__(self, symbol: str = "EURUSD", timeframe: str = "M15",
                 start_date: datetime | None = None, days: int = 21):
        self.symbol = symbol.upper()
        self.timeframe = timeframe.upper()
        self.config = self.SYMBOL_CONFIGS.get(self.symbol, self.SYMBOL_CONFIGS["EURUSD"])
        self.tf_mult = self.TF_SCALE.get(self.timeframe, 1.0)
        self.start_date = start_date or datetime.now(timezone.utc) - timedelta(days=days)
        self.days = days
        self.seed = hash(self.symbol + self.timeframe + str(self.start_date.date()))

    def generate(self) -> list[Rate]:
        """Generate OHLC rates for the configured period."""
        rng = random.Random(self.seed)
        base = float(self.config["base_price"])
        atr_m1 = float(self.config["atr_m1"]) * self.tf_mult
        point = float(self.config["point"])
        spread = float(self.config["spread_points"]) * point

        # Calculate bar interval
        tf_minutes = {"M1": 1, "M5": 5, "M15": 15, "H1": 60}.get(self.timeframe, 15)
        total_bars = self.days * 24 * 60 // tf_minutes

        rates: list[Rate] = []
        price = base
        current_time = self.start_date
        regime = "trend"
        regime_bars_left = rng.randint(200, 600)
        trend_bias = rng.uniform(-0.0003, 0.0003)
        vol_mult = 1.0
        daily_high = price
        daily_low = price
        current_day = current_time.date()

        for i in range(total_bars):
            current_time += timedelta(minutes=tf_minutes)

            # Skip weekends
            if current_time.weekday() >= 5:
                if current_time.weekday() == 5 and current_time.hour < 22:
                    continue
                continue

            # New day: reset daily range, apply weekend gap
            if current_time.date() != current_day:
                current_day = current_time.date()
                daily_high = price
                daily_low = price
                if current_time.weekday() == 0:  # Monday open gap
                    gap = rng.uniform(-self.config["weekend_gap_pct"], self.config["weekend_gap_pct"]) * base
                    price += gap

            # Regime switching
            regime_bars_left -= 1
            if regime_bars_left <= 0:
                regime = rng.choice(["trend", "trend", "range", "range", "volatile"])
                regime_bars_left = rng.randint(150, 500)
                trend_bias = rng.uniform(-0.0004, 0.0004)
                vol_mult = {"trend": rng.uniform(0.8, 1.2), "range": rng.uniform(0.4, 0.8),
                            "volatile": rng.uniform(1.3, 2.0)}.get(regime, 1.0)

            # Session-based volatility
            hour = current_time.hour
            session_mult = 1.0
            if 8 <= hour < 12:  # London open
                session_mult = 1.3
            elif 12 <= hour < 16:  # London/NY overlap
                session_mult = 1.5
            elif 16 <= hour < 20:  # NY afternoon
                session_mult = 1.0
            elif 20 <= hour < 24:  # Asian
                session_mult = 0.6
            else:  # Overnight
                session_mult = 0.4

            # Generate bar
            effective_atr = atr_m1 * vol_mult * session_mult
            noise = rng.gauss(0, effective_atr)

            # Mean reversion in range regime
            if regime == "range":
                mr_force = (base - price) * 0.001
                noise += mr_force
            else:
                noise += trend_bias * atr_m1 * 5

            open_price = price
            close_price = price + noise

            # Enforce daily range realism
            daily_range_target = self.config["daily_range"] * self.tf_mult * 0.3
            close_price = max(daily_low - daily_range_target * 0.5,
                              min(daily_high + daily_range_target * 0.5, close_price))

            bar_high = max(open_price, close_price) + abs(rng.gauss(0, effective_atr * 0.3))
            bar_low = min(open_price, close_price) - abs(rng.gauss(0, effective_atr * 0.3))

            daily_high = max(daily_high, bar_high)
            daily_low = min(daily_low, bar_low)

            # Add spread to ask/bid for realism
            rates.append({
                "time": current_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "open": round(open_price, 6),
                "high": round(bar_high + spread, 6),
                "low": round(bar_low - spread * 0.3, 6),
                "close": round(close_price, 6),
                "tick_volume": int(rng.randint(50, 500) * session_mult),
            })

            price = close_price

        return rates

    def generate_multi_tf(self) -> dict[str, list[Rate]]:
        """Generate M1, M5, and M15 data for the same period."""
        result = {}
        for tf in ["M1", "M5", "M15"]:
            gen = HistoricalDataGenerator(self.symbol, tf, self.start_date, self.days)
            result[tf] = gen.generate()
        return result

    def save_to_csv(self, rates: list[Rate], path: str | Path) -> Path:
        """Save generated rates to CSV."""
        import csv
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["time", "open", "high", "low", "close", "tick_volume"])
            writer.writeheader()
            writer.writerows(rates)
        return p
