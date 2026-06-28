from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings
from .models import AccountSnapshot


@dataclass
class RiskState:
    date: str
    start_equity: float
    peak_equity: float
    trades_count: int
    consecutive_losses: int
    last_trade_ts: float


class RiskManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = settings.state_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def can_trade(self, account: AccountSnapshot, runtime: dict[str, Any] | None = None) -> tuple[bool, str]:
        with self._lock:
            state = self._load_state(account)
        now = time.time()

        max_trades = int((runtime or {}).get("max_trades_per_day", self.settings.max_trades_per_day))
        cooldown = int((runtime or {}).get("cooldown_seconds", self.settings.cooldown_seconds))
        daily_loss = float((runtime or {}).get("daily_loss_limit_percent", self.settings.daily_loss_limit_percent))
        max_session_drawdown = float((runtime or {}).get("max_session_drawdown_percent", self.settings.max_session_drawdown_percent))
        max_consecutive = int((runtime or {}).get("max_consecutive_losses", self.settings.max_consecutive_losses))

        if state.trades_count >= max_trades:
            return False, "Max trades per day reached"

        if now - state.last_trade_ts < cooldown:
            remaining = int(cooldown - (now - state.last_trade_ts))
            return False, f"Cooldown active for {remaining}s"

        daily_loss_value = state.start_equity * (min(daily_loss, 100.0) / 100)
        if account.equity <= state.start_equity - daily_loss_value:
            return False, "Daily loss limit reached"

        if max_session_drawdown > 0:
            session_dd = state.start_equity * (min(max_session_drawdown, 100.0) / 100)
            if account.equity <= state.peak_equity - session_dd:
                return False, f"Session drawdown limit reached ({max_session_drawdown}%)"

        if max_consecutive > 0 and state.consecutive_losses >= max_consecutive:
            return False, f"Max consecutive losses reached ({state.consecutive_losses})"

        return True, "Risk checks passed"

    def record_trade(self, account: AccountSnapshot, success: bool = True) -> None:
        with self._lock:
            state = self._load_state(account)
            state.trades_count += 1
            state.last_trade_ts = time.time()
            if success:
                state.consecutive_losses = 0
                if account.equity > state.peak_equity:
                    state.peak_equity = account.equity
            else:
                state.consecutive_losses += 1
            self._save_state(state)

    def _load_state(self, account: AccountSnapshot) -> RiskState:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if not self.path.exists():
            state = RiskState(today, account.equity, account.equity, 0, 0, 0.0)
            self._save_state(state)
            return state

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            state = RiskState(
                date=str(raw.get("date", today)),
                start_equity=float(raw.get("start_equity", account.equity)),
                peak_equity=float(raw.get("peak_equity", account.equity)),
                trades_count=int(raw.get("trades_count", 0)),
                consecutive_losses=int(raw.get("consecutive_losses", 0)),
                last_trade_ts=float(raw.get("last_trade_ts", 0.0)),
            )
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            state = RiskState(today, account.equity, account.equity, 0, 0, 0.0)

        if state.date != today:
            state = RiskState(today, account.equity, account.equity, 0, 0, 0.0)
            self._save_state(state)

        return state

    def _save_state(self, state: RiskState) -> None:
        try:
            self.path.write_text(
                json.dumps(
                    {
                        "date": state.date,
                        "start_equity": state.start_equity,
                        "peak_equity": state.peak_equity,
                        "trades_count": state.trades_count,
                        "consecutive_losses": state.consecutive_losses,
                        "last_trade_ts": state.last_trade_ts,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            raise RuntimeError(f"Failed to save risk state: {exc}") from exc
