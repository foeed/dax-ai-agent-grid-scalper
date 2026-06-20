from __future__ import annotations

import json
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings


class RuntimeSettingsStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = settings.dashboard_settings_path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def get_overrides(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return {
            key: value
            for key, value in raw.items()
            if key in {"trading_mode", "dry_run", "use_llm", "llm_fail_closed"}
        }

    def effective(self) -> dict[str, Any]:
        overrides = self.get_overrides()
        return {
            "trading_mode": overrides.get("trading_mode", self.settings.trading_mode),
            "dry_run": overrides.get("dry_run", self.settings.dry_run),
            "use_llm": overrides.get("use_llm", self.settings.use_llm),
            "llm_fail_closed": overrides.get("llm_fail_closed", self.settings.llm_fail_closed),
        }

    def update(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.get_overrides()

        if "trading_mode" in payload:
            mode = str(payload["trading_mode"]).lower()
            if mode not in {"paper", "live", "bridge"}:
                raise ValueError("trading_mode must be paper, live, or bridge")
            current["trading_mode"] = mode

        if "dry_run" in payload:
            current["dry_run"] = _coerce_bool(payload["dry_run"])

        if "use_llm" in payload:
            use_llm = _coerce_bool(payload["use_llm"])
            if use_llm and not self.settings.deepseek_api_key:
                raise ValueError("Cannot enable LLM without DEEPSEEK_API_KEY")
            current["use_llm"] = use_llm

        if "llm_fail_closed" in payload:
            current["llm_fail_closed"] = _coerce_bool(payload["llm_fail_closed"])

        current["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.path.write_text(json.dumps(current, indent=2), encoding="utf-8")
        return self.effective()


class EventStore:
    def __init__(self, settings: Settings, runtime_settings: RuntimeSettingsStore | None = None) -> None:
        self.settings = settings
        self.runtime_settings = runtime_settings
        self.path = settings.event_log_path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event_type: str, payload: dict[str, Any]) -> None:
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            **self._safe_payload(payload),
        }
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, separators=(",", ":"), default=str) + "\n")

    def append_signal(self, request: dict[str, Any], response: dict[str, Any]) -> None:
        self.append(
            "signal",
            {
                "symbol": request.get("symbol", self.settings.symbol),
                "timeframe": request.get("timeframe", self.settings.timeframe),
                "action": response.get("action", "HOLD"),
                "confidence": response.get("confidence", 0.0),
                "reason": response.get("reason", ""),
                "spread_points": request.get("spread_points", 0.0),
                "positions_count": request.get("positions_count", 0),
                "sl_points": response.get("sl_points", 0),
                "tp_points": response.get("tp_points", 0),
            },
        )

    def append_trade_result(self, payload: dict[str, Any], recorded: bool) -> None:
        self.append(
            "trade_result",
            {
                "action": payload.get("action", ""),
                "success": bool(payload.get("success", False)),
                "recorded": recorded,
                "retcode": payload.get("retcode"),
                "reason": payload.get("reason", ""),
            },
        )

    def recent(self, limit: int | None = None) -> list[dict[str, Any]]:
        limit = limit or self.settings.dashboard_events_limit
        if not self.path.exists():
            return []

        rows: deque[dict[str, Any]] = deque(maxlen=max(1, limit))
        with self.path.open("r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return list(reversed(rows))

    def summary(self) -> dict[str, Any]:
        events = self.recent(1000)
        actions = Counter(str(event.get("action", "UNKNOWN")) for event in events if event.get("type") == "signal")
        trade_results = [event for event in events if event.get("type") == "trade_result"]
        return {
            "events_count": len(events),
            "actions": dict(actions),
            "trade_results": len(trade_results),
            "trade_success": sum(1 for event in trade_results if event.get("success") is True),
            "trade_failed": sum(1 for event in trade_results if event.get("success") is False),
        }

    def risk_state(self) -> dict[str, Any]:
        if not self.settings.state_path.exists():
            return {}
        try:
            return json.loads(self.settings.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def status(self) -> dict[str, Any]:
        effective = self.runtime_settings.effective() if self.runtime_settings else {
            "trading_mode": self.settings.trading_mode,
            "dry_run": self.settings.dry_run,
            "use_llm": self.settings.use_llm,
        }
        return {
            "ok": True,
            "server_time": datetime.now(timezone.utc).isoformat(),
            "settings": {
                "trading_mode": effective["trading_mode"],
                "dry_run": effective["dry_run"],
                "symbol": self.settings.symbol,
                "timeframe": self.settings.timeframe,
                "use_llm": effective["use_llm"],
                "llm_fail_closed": effective["llm_fail_closed"],
                "max_spread_points": self.settings.max_spread_points,
                "max_positions": self.settings.max_positions,
                "max_trades_per_day": self.settings.max_trades_per_day,
                "cooldown_seconds": self.settings.cooldown_seconds,
                "event_log_path": str(self.settings.event_log_path),
                "state_path": str(self.settings.state_path),
                "dashboard_auto_token": self.settings.dashboard_auto_token,
            },
            "risk_state": self.risk_state(),
            "summary": self.summary(),
        }

    def _safe_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        safe: dict[str, Any] = {}
        for key, value in payload.items():
            if key.lower() in {"token", "password", "api_key", "authorization"}:
                continue
            if isinstance(value, str):
                safe[key] = value[:500]
            elif isinstance(value, (int, float, bool)) or value is None:
                safe[key] = value
            else:
                safe[key] = str(value)[:500]
        return safe


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False
