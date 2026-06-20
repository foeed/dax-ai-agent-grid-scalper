from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .models import AccountSnapshot


@dataclass
class RiskState:
    date: str
    start_equity: float
    trades_count: int
    last_trade_ts: float


class RiskManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = settings.state_path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def can_trade(self, account: AccountSnapshot) -> tuple[bool, str]:
        state = self._load_state(account)
        now = time.time()

        if state.trades_count >= self.settings.max_trades_per_day:
            return False, "Max trades per day reached"

        if now - state.last_trade_ts < self.settings.cooldown_seconds:
            remaining = int(self.settings.cooldown_seconds - (now - state.last_trade_ts))
            return False, f"Cooldown active for {remaining}s"

        max_loss = state.start_equity * (self.settings.daily_loss_limit_percent / 100)
        if account.equity <= state.start_equity - max_loss:
            return False, "Daily loss limit reached"

        return True, "Risk checks passed"

    def record_trade(self, account: AccountSnapshot) -> None:
        state = self._load_state(account)
        state.trades_count += 1
        state.last_trade_ts = time.time()
        self._save_state(state)

    def _load_state(self, account: AccountSnapshot) -> RiskState:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if not self.path.exists():
            state = RiskState(today, account.equity, 0, 0.0)
            self._save_state(state)
            return state

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            state = RiskState(
                date=str(raw["date"]),
                start_equity=float(raw["start_equity"]),
                trades_count=int(raw["trades_count"]),
                last_trade_ts=float(raw["last_trade_ts"]),
            )
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            state = RiskState(today, account.equity, 0, 0.0)

        if state.date != today:
            state = RiskState(today, account.equity, 0, 0.0)
            self._save_state(state)

        return state

    def _save_state(self, state: RiskState) -> None:
        self.path.write_text(
            json.dumps(
                {
                    "date": state.date,
                    "start_equity": state.start_equity,
                    "trades_count": state.trades_count,
                    "last_trade_ts": state.last_trade_ts,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
