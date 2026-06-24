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
        valid_keys = {
            "trading_mode", "dry_run", "use_llm", "llm_fail_closed",
            "auto_tune", "auto_tune_profile", "auto_tune_summary",
            "use_risk_sizing", "lots", "max_positions", "max_spread_points", "max_spread_percent", "cooldown_seconds",
            "magic_number", "deviation_points", "one_trade_per_bar",
            "bars_to_send", "request_timeout_ms", "request_retries", "retry_delay_ms",
            "settings_refresh_seconds",
            "symbol", "timeframe", "bars", "poll_seconds",
            "ema_fast", "ema_slow", "ema_trend", "atr_period", "rsi_period",
            "sl_atr_multiplier", "tp_atr_multiplier", "min_stop_points",
            "min_signal_confidence", "llm_min_score", "llm_timeout_seconds",
            "risk_percent", "daily_loss_limit_percent", "max_trades_per_day",
            "fixed_lot", "order_comment",
        }
        return {key: value for key, value in raw.items() if key in valid_keys}

    def effective(self) -> dict[str, Any]:
        overrides = self.get_overrides()
        fixed_lot = overrides.get("fixed_lot", self.settings.fixed_lot)
        return {
            "trading_mode": overrides.get("trading_mode", self.settings.trading_mode),
            "dry_run": overrides.get("dry_run", self.settings.dry_run),
            "symbol": overrides.get("symbol", self.settings.symbol),
            "timeframe": overrides.get("timeframe", self.settings.timeframe),
            "bars": overrides.get("bars", self.settings.bars),
            "poll_seconds": overrides.get("poll_seconds", self.settings.poll_seconds),
            "ema_fast": overrides.get("ema_fast", self.settings.ema_fast),
            "ema_slow": overrides.get("ema_slow", self.settings.ema_slow),
            "ema_trend": overrides.get("ema_trend", self.settings.ema_trend),
            "atr_period": overrides.get("atr_period", self.settings.atr_period),
            "rsi_period": overrides.get("rsi_period", self.settings.rsi_period),
            "sl_atr_multiplier": overrides.get("sl_atr_multiplier", self.settings.sl_atr_multiplier),
            "tp_atr_multiplier": overrides.get("tp_atr_multiplier", self.settings.tp_atr_multiplier),
            "min_stop_points": overrides.get("min_stop_points", self.settings.min_stop_points),
            "min_signal_confidence": overrides.get("min_signal_confidence", self.settings.min_signal_confidence),
            "risk_percent": overrides.get("risk_percent", self.settings.risk_percent),
            "daily_loss_limit_percent": overrides.get("daily_loss_limit_percent", self.settings.daily_loss_limit_percent),
            "max_trades_per_day": overrides.get("max_trades_per_day", self.settings.max_trades_per_day),
            "fixed_lot": fixed_lot,
            "order_comment": overrides.get("order_comment", self.settings.order_comment),
            "use_llm": overrides.get("use_llm", self.settings.use_llm),
            "llm_fail_closed": overrides.get("llm_fail_closed", self.settings.llm_fail_closed),
            "auto_tune": overrides.get("auto_tune", overrides.get("use_llm", self.settings.use_llm)),
            "auto_tune_profile": overrides.get("auto_tune_profile", "manual"),
            "auto_tune_summary": overrides.get("auto_tune_summary", ""),
            "llm_min_score": overrides.get("llm_min_score", self.settings.llm_min_score),
            "llm_timeout_seconds": overrides.get("llm_timeout_seconds", self.settings.llm_timeout_seconds),
            "use_risk_sizing": overrides.get("use_risk_sizing", False),
            "lots": overrides.get("lots", fixed_lot if fixed_lot else self.settings.min_lot),
            "max_positions": overrides.get("max_positions", self.settings.max_positions),
            "max_spread_points": overrides.get("max_spread_points", self.settings.max_spread_points),
            "max_spread_percent": overrides.get("max_spread_percent", 0.5),
            "cooldown_seconds": overrides.get("cooldown_seconds", self.settings.cooldown_seconds),
            "magic_number": overrides.get("magic_number", self.settings.magic_number),
            "deviation_points": overrides.get("deviation_points", self.settings.deviation_points),
            "one_trade_per_bar": overrides.get("one_trade_per_bar", True),
            "bars_to_send": overrides.get("bars_to_send", self.settings.bars),
            "request_timeout_ms": overrides.get("request_timeout_ms", 30000),
            "request_retries": overrides.get("request_retries", 1),
            "retry_delay_ms": overrides.get("retry_delay_ms", 750),
            "settings_refresh_seconds": overrides.get("settings_refresh_seconds", 30),
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

        if "auto_tune" in payload:
            current["auto_tune"] = _coerce_bool(payload["auto_tune"])

        if "use_risk_sizing" in payload:
            current["use_risk_sizing"] = _coerce_bool(payload["use_risk_sizing"])

        if "symbol" in payload:
            current["symbol"] = str(payload["symbol"]).upper()

        if "timeframe" in payload:
            current["timeframe"] = str(payload["timeframe"]).upper()

        if "bars" in payload:
            val = int(payload["bars"])
            if val < 50:
                raise ValueError("bars must be >= 50")
            current["bars"] = val

        if "poll_seconds" in payload:
            val = int(payload["poll_seconds"])
            if val < 5:
                raise ValueError("poll_seconds must be >= 5")
            current["poll_seconds"] = val

        if "ema_fast" in payload:
            current["ema_fast"] = int(payload["ema_fast"])

        if "ema_slow" in payload:
            current["ema_slow"] = int(payload["ema_slow"])

        if "ema_trend" in payload:
            current["ema_trend"] = int(payload["ema_trend"])

        if "atr_period" in payload:
            current["atr_period"] = int(payload["atr_period"])

        if "rsi_period" in payload:
            current["rsi_period"] = int(payload["rsi_period"])

        if "sl_atr_multiplier" in payload:
            current["sl_atr_multiplier"] = float(payload["sl_atr_multiplier"])

        if "tp_atr_multiplier" in payload:
            current["tp_atr_multiplier"] = float(payload["tp_atr_multiplier"])

        if "min_stop_points" in payload:
            current["min_stop_points"] = int(payload["min_stop_points"])

        if "min_signal_confidence" in payload:
            val = float(payload["min_signal_confidence"])
            if val < 0 or val > 1:
                raise ValueError("min_signal_confidence must be between 0 and 1")
            current["min_signal_confidence"] = val

        if "llm_min_score" in payload:
            val = float(payload["llm_min_score"])
            if val < 0 or val > 1:
                raise ValueError("llm_min_score must be between 0 and 1")
            current["llm_min_score"] = val

        if "llm_timeout_seconds" in payload:
            val = int(payload["llm_timeout_seconds"])
            if val < 1:
                raise ValueError("llm_timeout_seconds must be >= 1")
            current["llm_timeout_seconds"] = val

        if "risk_percent" in payload:
            val = float(payload["risk_percent"])
            if val <= 0 or val > 5:
                raise ValueError("risk_percent must be > 0 and <= 5")
            current["risk_percent"] = val

        if "daily_loss_limit_percent" in payload:
            val = float(payload["daily_loss_limit_percent"])
            if val < 0:
                raise ValueError("daily_loss_limit_percent must be >= 0")
            current["daily_loss_limit_percent"] = val

        if "max_trades_per_day" in payload:
            val = int(payload["max_trades_per_day"])
            if val < 1:
                raise ValueError("max_trades_per_day must be >= 1")
            current["max_trades_per_day"] = val

        if "fixed_lot" in payload:
            val = float(payload["fixed_lot"])
            if val <= 0:
                raise ValueError("fixed_lot must be > 0")
            current["fixed_lot"] = val

        if "order_comment" in payload:
            current["order_comment"] = str(payload["order_comment"])[:50]

        if "lots" in payload:
            lots = float(payload["lots"])
            if lots <= 0:
                raise ValueError("lots must be positive")
            current["lots"] = lots

        if "max_positions" in payload:
            val = int(payload["max_positions"])
            if val < 1:
                raise ValueError("max_positions must be >= 1")
            current["max_positions"] = val

        if "max_spread_points" in payload:
            current["max_spread_points"] = int(payload["max_spread_points"])

        if "max_spread_percent" in payload:
            val = float(payload["max_spread_percent"])
            if val < 0:
                raise ValueError("max_spread_percent must be >= 0")
            current["max_spread_percent"] = val

        if "cooldown_seconds" in payload:
            val = int(payload["cooldown_seconds"])
            if val < 0:
                raise ValueError("cooldown_seconds must be >= 0")
            current["cooldown_seconds"] = val

        if "magic_number" in payload:
            current["magic_number"] = int(payload["magic_number"])

        if "deviation_points" in payload:
            current["deviation_points"] = int(payload["deviation_points"])

        if "one_trade_per_bar" in payload:
            current["one_trade_per_bar"] = _coerce_bool(payload["one_trade_per_bar"])

        if "bars_to_send" in payload:
            val = int(payload["bars_to_send"])
            if val < 50:
                raise ValueError("bars_to_send must be >= 50")
            current["bars_to_send"] = val

        if "request_timeout_ms" in payload:
            val = int(payload["request_timeout_ms"])
            if val < 1000:
                raise ValueError("request_timeout_ms must be >= 1000")
            current["request_timeout_ms"] = val

        if "request_retries" in payload:
            val = int(payload["request_retries"])
            if val < 0:
                raise ValueError("request_retries must be >= 0")
            current["request_retries"] = val

        if "retry_delay_ms" in payload:
            val = int(payload["retry_delay_ms"])
            if val < 0:
                raise ValueError("retry_delay_ms must be >= 0")
            current["retry_delay_ms"] = val

        if "settings_refresh_seconds" in payload:
            val = int(payload["settings_refresh_seconds"])
            if val < 5:
                raise ValueError("settings_refresh_seconds must be >= 5")
            current["settings_refresh_seconds"] = val

        llm_enabled = bool(current.get("use_llm", self.settings.use_llm))
        if llm_enabled and "auto_tune" not in current:
            current["auto_tune"] = True
        if not llm_enabled:
            current["auto_tune"] = False
            current["auto_tune_profile"] = "manual"
            current["auto_tune_summary"] = "Manual mode: Strategy and MT5 EA parameters are controlled by the dashboard fields."
        elif _coerce_bool(current.get("auto_tune", True)):
            current.update(_expert_auto_tune(current, self.settings))

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

    def _detect_active_symbol(self) -> str:
        """Detect the actual trading symbol from recent events."""
        events = self.recent(20)
        symbols = [e.get("symbol", "") for e in events if e.get("symbol")]
        if symbols:
            from collections import Counter
            return Counter(symbols).most_common(1)[0][0]
        return self.settings.symbol

    def status(self) -> dict[str, Any]:
        effective = self.runtime_settings.effective() if self.runtime_settings else {
            "trading_mode": self.settings.trading_mode,
            "dry_run": self.settings.dry_run,
            "use_llm": self.settings.use_llm,
        }
        active_symbol = self._detect_active_symbol()
        return {
            "ok": True,
            "server_time": datetime.now(timezone.utc).isoformat(),
            "settings": {
                "trading_mode": effective.get("trading_mode", self.settings.trading_mode),
                "dry_run": effective.get("dry_run", self.settings.dry_run),
                "symbol": effective.get("symbol", self.settings.symbol),
                "active_symbol": active_symbol,
                "timeframe": effective.get("timeframe", self.settings.timeframe),
                "bars": effective.get("bars", self.settings.bars),
                "poll_seconds": effective.get("poll_seconds", self.settings.poll_seconds),
                "use_llm": effective.get("use_llm", self.settings.use_llm),
                "llm_fail_closed": effective.get("llm_fail_closed", self.settings.llm_fail_closed),
                "auto_tune": effective.get("auto_tune", False),
                "auto_tune_profile": effective.get("auto_tune_profile", "manual"),
                "auto_tune_summary": effective.get("auto_tune_summary", ""),
                "llm_min_score": effective.get("llm_min_score", self.settings.llm_min_score),
                "llm_timeout_seconds": effective.get("llm_timeout_seconds", self.settings.llm_timeout_seconds),
                "use_risk_sizing": effective.get("use_risk_sizing", False),
                "max_spread_points": effective.get("max_spread_points", self.settings.max_spread_points),
                "max_positions": effective.get("max_positions", self.settings.max_positions),
                "max_trades_per_day": effective.get("max_trades_per_day", self.settings.max_trades_per_day),
                "cooldown_seconds": effective.get("cooldown_seconds", self.settings.cooldown_seconds),
                "risk_percent": effective.get("risk_percent", self.settings.risk_percent),
                "daily_loss_limit_percent": effective.get("daily_loss_limit_percent", self.settings.daily_loss_limit_percent),
                "min_signal_confidence": effective.get("min_signal_confidence", self.settings.min_signal_confidence),
                "ema_fast": effective.get("ema_fast", self.settings.ema_fast),
                "ema_slow": effective.get("ema_slow", self.settings.ema_slow),
                "ema_trend": effective.get("ema_trend", self.settings.ema_trend),
                "atr_period": effective.get("atr_period", self.settings.atr_period),
                "rsi_period": effective.get("rsi_period", self.settings.rsi_period),
                "sl_atr_multiplier": effective.get("sl_atr_multiplier", self.settings.sl_atr_multiplier),
                "tp_atr_multiplier": effective.get("tp_atr_multiplier", self.settings.tp_atr_multiplier),
                "min_stop_points": effective.get("min_stop_points", self.settings.min_stop_points),
                "fixed_lot": effective.get("fixed_lot", self.settings.fixed_lot),
                "order_comment": effective.get("order_comment", self.settings.order_comment),
                "server_time": datetime.now(timezone.utc).isoformat(),
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


_CRYPTO_PREFIXES = {
    "SOL", "BTC", "ETH", "BNB", "XRP", "ADA", "DOGE", "DOT",
    "LTC", "MATIC", "AVAX", "LINK", "UNI", "ATOM", "FIL",
    "APT", "ARB", "OP", "SUI", "TRX", "TON", "NEAR", "ICP",
    "BCH", "EOS", "ETC", "VET", "ALGO", "MANA", "SAND",
    "AXS", "EGLD", "RUNE", "FTM", "FLOW", "GRT", "IMX", "SNX",
    "XTZ", "THETA", "ZEC", "DASH", "NEO", "QTUM", "OMG", "BAT",
    "ZRX", "ENJ", "CHZ", "CELO", "COMP", "MKR", "YFI", "CRV",
}


def _expert_auto_tune(current: dict[str, Any], settings: Settings) -> dict[str, Any]:
    symbol = str(current.get("symbol", settings.symbol)).upper()
    asset = _detect_asset_class(symbol)
    base = _base_profile(asset)
    risk_percent = _clamp(float(current.get("risk_percent", settings.risk_percent)), 0.01, 5.0)
    daily_loss_limit = _clamp(
        float(current.get("daily_loss_limit_percent", settings.daily_loss_limit_percent)),
        0.0,
        100.0,
    )
    max_trades_per_day = max(1, int(current.get("max_trades_per_day", settings.max_trades_per_day)))
    llm_min_score = _clamp(float(current.get("llm_min_score", settings.llm_min_score)), 0.0, 1.0)
    llm_timeout_seconds = int(_clamp(float(current.get("llm_timeout_seconds", settings.llm_timeout_seconds)), 1, 60))

    risk_band = _risk_band(risk_percent)
    strictness = llm_min_score - float(base["llm_min_score"])
    conservative_adjustment = {"low": 0.04, "balanced": 0.0, "high": -0.02}[risk_band]
    daily_loss_adjustment = _daily_loss_adjustment(risk_percent, daily_loss_limit)
    trade_frequency_adjustment = _trade_frequency_adjustment(max_trades_per_day)
    min_confidence = _clamp(
        float(base["min_signal_confidence"])
        + (strictness * 0.35)
        + conservative_adjustment
        + daily_loss_adjustment["confidence"]
        + trade_frequency_adjustment["confidence"],
        0.5,
        0.9,
    )
    cooldown_multiplier = (
        {"low": 1.35, "balanced": 1.0, "high": 0.75}[risk_band]
        * daily_loss_adjustment["cooldown"]
        * trade_frequency_adjustment["cooldown"]
    )
    cooldown = max(30, int(round(float(base["cooldown_seconds"]) * cooldown_multiplier)))
    lots_cap = _lots_cap(asset, risk_percent)
    request_timeout_ms = min(90000, max(30000, (llm_timeout_seconds * 1000) + 5000))
    max_positions = {"low": 1, "balanced": 1 if asset == "crypto" else 2, "high": 2}[risk_band]
    if daily_loss_adjustment["limit_positions"] or max_trades_per_day <= 3:
        max_positions = 1

    tuned = {
        "auto_tune": True,
        "auto_tune_profile": f"{asset}-{risk_band}",
        "auto_tune_summary": (
            f"LLM autopilot tuned {symbol} as {asset}/{risk_band}: "
            f"EMA {base['ema_fast']}/{base['ema_slow']}/{base['ema_trend']}, "
            f"confidence {min_confidence:.2f}, spread≤{base['max_spread_percent']:.2f}%, "
            f"risk sizing cap {lots_cap:.2f} lots, "
            f"user daily loss {daily_loss_limit:.2f}%, max trades {max_trades_per_day}."
        ),
        "timeframe": base["timeframe"],
        "bars": base["bars"],
        "ema_fast": base["ema_fast"],
        "ema_slow": base["ema_slow"],
        "ema_trend": base["ema_trend"],
        "atr_period": base["atr_period"],
        "rsi_period": base["rsi_period"],
        "sl_atr_multiplier": base["sl_atr_multiplier"],
        "tp_atr_multiplier": base["tp_atr_multiplier"],
        "min_stop_points": base["min_stop_points"],
        "min_signal_confidence": round(min_confidence, 2),
        "use_risk_sizing": True,
        "lots": lots_cap,
        "max_positions": max_positions,
        "max_spread_points": 0,
        "max_spread_percent": base["max_spread_percent"],
        "cooldown_seconds": cooldown,
        "one_trade_per_bar": True,
        "bars_to_send": base["bars"],
        "request_timeout_ms": request_timeout_ms,
        "request_retries": 1,
        "retry_delay_ms": 750,
    }
    return tuned


def _detect_asset_class(symbol: str) -> str:
    upper = symbol.upper()
    if "XAU" in upper or "XAG" in upper or "XPD" in upper or "XPT" in upper:
        return "gold"
    for prefix in _CRYPTO_PREFIXES:
        if upper.startswith(prefix):
            return "crypto"
    if "USD" in upper:
        base = upper.split("USD")[0]
        if base:
            for prefix in _CRYPTO_PREFIXES:
                if prefix in base:
                    return "crypto"
            if len(base) == 3 and base.isalpha():
                return "forex"
    return "other"


def _base_profile(asset: str) -> dict[str, Any]:
    profiles: dict[str, dict[str, Any]] = {
        "crypto": {
            "timeframe": "M5", "bars": 400,
            "ema_fast": 5, "ema_slow": 13, "ema_trend": 34,
            "atr_period": 10, "rsi_period": 10,
            "sl_atr_multiplier": 1.8, "tp_atr_multiplier": 3.0,
            "min_stop_points": 120, "min_signal_confidence": 0.55,
            "llm_min_score": 0.60, "max_spread_percent": 3.0,
            "cooldown_seconds": 300,
        },
        "gold": {
            "timeframe": "M1", "bars": 300,
            "ema_fast": 8, "ema_slow": 21, "ema_trend": 55,
            "atr_period": 14, "rsi_period": 14,
            "sl_atr_multiplier": 1.3, "tp_atr_multiplier": 1.8,
            "min_stop_points": 80, "min_signal_confidence": 0.62,
            "llm_min_score": 0.65, "max_spread_percent": 0.15,
            "cooldown_seconds": 180,
        },
        "forex": {
            "timeframe": "M5", "bars": 300,
            "ema_fast": 8, "ema_slow": 21, "ema_trend": 55,
            "atr_period": 14, "rsi_period": 14,
            "sl_atr_multiplier": 1.3, "tp_atr_multiplier": 2.0,
            "min_stop_points": 50, "min_signal_confidence": 0.62,
            "llm_min_score": 0.65, "max_spread_percent": 0.30,
            "cooldown_seconds": 180,
        },
    }
    return profiles.get(
        asset,
        {
            "timeframe": "M5", "bars": 300,
            "ema_fast": 8, "ema_slow": 21, "ema_trend": 55,
            "atr_period": 14, "rsi_period": 14,
            "sl_atr_multiplier": 1.3, "tp_atr_multiplier": 1.8,
            "min_stop_points": 80, "min_signal_confidence": 0.62,
            "llm_min_score": 0.65, "max_spread_percent": 0.50,
            "cooldown_seconds": 180,
        },
    )


def _risk_band(risk_percent: float) -> str:
    if risk_percent <= 0.2:
        return "low"
    if risk_percent <= 0.6:
        return "balanced"
    return "high"


def _daily_loss_adjustment(risk_percent: float, daily_loss_limit: float) -> dict[str, Any]:
    if daily_loss_limit <= 0:
        return {"confidence": 0.08, "cooldown": 1.75, "limit_positions": True}

    loss_to_trade_risk = daily_loss_limit / max(risk_percent, 0.01)
    if loss_to_trade_risk <= 3:
        return {"confidence": 0.05, "cooldown": 1.4, "limit_positions": True}
    if loss_to_trade_risk <= 6:
        return {"confidence": 0.02, "cooldown": 1.15, "limit_positions": False}
    if daily_loss_limit >= 5 and loss_to_trade_risk >= 12:
        return {"confidence": -0.01, "cooldown": 0.9, "limit_positions": False}
    return {"confidence": 0.0, "cooldown": 1.0, "limit_positions": False}


def _trade_frequency_adjustment(max_trades_per_day: int) -> dict[str, float]:
    if max_trades_per_day <= 2:
        return {"confidence": 0.05, "cooldown": 1.6}
    if max_trades_per_day <= 4:
        return {"confidence": 0.03, "cooldown": 1.3}
    if max_trades_per_day <= 8:
        return {"confidence": 0.0, "cooldown": 1.0}
    if max_trades_per_day <= 15:
        return {"confidence": -0.01, "cooldown": 0.85}
    return {"confidence": -0.02, "cooldown": 0.7}


def _lots_cap(asset: str, risk_percent: float) -> float:
    asset_cap = {"crypto": 0.25, "gold": 0.20, "forex": 0.30}.get(asset, 0.20)
    risk_cap = max(0.01, min(asset_cap, risk_percent * 0.4))
    return round(risk_cap, 2)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
