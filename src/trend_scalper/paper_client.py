from __future__ import annotations

import csv
import logging
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Settings
from .models import AccountSnapshot, OrderResult, PositionSnapshot, Rate, TradeSignal

logger = logging.getLogger(__name__)


class PaperClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.balance = 10_000.0
        self.positions: list[PositionSnapshot] = []
        self._tick = 0

    def connect(self) -> None:
        logger.info("Paper mode connected; no real broker orders will be sent")

    def shutdown(self) -> None:
        logger.info("Paper mode stopped")

    def get_rates(self) -> list[Rate]:
        if self.settings.data_csv_path:
            return self._load_csv(self.settings.data_csv_path)
        return self._synthetic_rates()

    def get_account_snapshot(self) -> AccountSnapshot:
        return AccountSnapshot(balance=self.balance, equity=self.balance, currency="USD")

    def get_positions(self) -> list[PositionSnapshot]:
        return list(self.positions)

    def spread_points(self) -> float:
        return min(self.settings.max_spread_points, 12.0)

    def point(self) -> float:
        return 0.01 if self.settings.symbol.upper().startswith("XAU") else 0.00001

    def calculate_volume(self, signal: TradeSignal, account: AccountSnapshot) -> float:
        if self.settings.fixed_lot is not None:
            return self._clamp_lot(self.settings.fixed_lot)

        risk_amount = account.equity * (self.settings.risk_percent / 100)
        estimated_loss_per_lot = max(signal.sl_distance / self.point(), 1.0)
        raw_lot = risk_amount / estimated_loss_per_lot
        return self._clamp_lot(raw_lot)

    def place_order(self, signal: TradeSignal, volume: float) -> OrderResult:
        side = signal.action
        self.positions.append(
            PositionSnapshot(
                symbol=self.settings.symbol,
                side=side,
                volume=volume,
                profit=0.0,
                magic=self.settings.magic_number,
            )
        )
        message = f"Paper {side} {volume:.2f} {self.settings.symbol}"
        logger.info(message)
        return OrderResult(True, message, order_id=len(self.positions))

    def _load_csv(self, path: Path) -> list[Rate]:
        with path.open("r", encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)
            rows = list(reader)

        required = {"open", "high", "low", "close"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path} missing columns: {', '.join(sorted(missing))}")

        rates: list[Rate] = []
        for row in rows[-self.settings.bars :]:
            rates.append(
                {
                    **row,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                }
            )
        return rates

    def _synthetic_rates(self) -> list[Rate]:
        self._tick += 1
        count = self.settings.bars
        rng = random.Random(260618 + self._tick)
        base = 2300.0 if self.settings.symbol.upper().startswith("XAU") else 1.08
        now = datetime.now(timezone.utc)
        rates: list[Rate] = []
        previous_close = base

        for index in range(count):
            progress = index / max(count - 1, 1)
            trend = progress * 3.0
            wave = math.sin(progress * 10.0) * 0.9
            noise = rng.gauss(0, 0.08)
            close = base + trend + wave + noise
            open_value = previous_close
            high = max(open_value, close) + rng.uniform(0.05, 0.35)
            low = min(open_value, close) - rng.uniform(0.05, 0.35)
            rates.append(
                {
                    "time": (now - timedelta(minutes=count - index)).isoformat(),
                    "open": open_value,
                    "high": high,
                    "low": low,
                    "close": close,
                    "tick_volume": rng.randint(100, 500),
                }
            )
            previous_close = close

        return rates

    def _clamp_lot(self, lot: float) -> float:
        return round(max(self.settings.min_lot, min(self.settings.max_lot, lot)), 2)
