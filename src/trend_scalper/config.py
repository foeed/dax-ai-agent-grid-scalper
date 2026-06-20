from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


TradingMode = Literal["paper", "live", "bridge"]


def _str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int(name: str, default: int) -> int:
    value = _str(name)
    return default if value == "" else int(value)


def _float(name: str, default: float) -> float:
    value = _str(name)
    return default if value == "" else float(value)


def _bool(name: str, default: bool) -> bool:
    value = _str(name)
    if value == "":
        return default
    return value.lower() in {"1", "true", "yes", "y", "on"}


def _optional_int(name: str) -> int | None:
    value = _str(name)
    return None if value == "" else int(value)


def _optional_float(name: str) -> float | None:
    value = _str(name)
    return None if value == "" else float(value)


def _load_env_file(path: str | Path) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    trading_mode: TradingMode
    dry_run: bool
    symbol: str
    timeframe: str
    bars: int
    poll_seconds: int
    data_csv_path: Path | None

    ema_fast: int
    ema_slow: int
    ema_trend: int
    atr_period: int
    rsi_period: int
    sl_atr_multiplier: float
    tp_atr_multiplier: float
    min_stop_points: int
    min_signal_confidence: float

    fixed_lot: float | None
    min_lot: float
    max_lot: float
    risk_percent: float
    max_spread_points: float
    max_positions: int
    max_trades_per_day: int
    cooldown_seconds: int
    daily_loss_limit_percent: float
    magic_number: int
    deviation_points: int
    order_comment: str

    use_llm: bool
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    llm_min_score: float
    llm_timeout_seconds: float
    llm_fail_closed: bool

    bridge_url: str
    bridge_token: str
    bridge_host: str
    bridge_port: int

    signal_host: str
    signal_port: int
    signal_token: str

    mt5_login: int | None
    mt5_password: str
    mt5_server: str
    mt5_path: str

    state_path: Path
    event_log_path: Path
    dashboard_settings_path: Path
    dashboard_events_limit: int
    dashboard_auto_token: bool
    log_level: str


def load_settings(env_file: str | Path | None = ".env") -> Settings:
    if env_file:
        _load_env_file(env_file)

    mode = _str("TRADING_MODE", "paper").lower()
    if mode not in {"paper", "live", "bridge"}:
        raise ValueError("TRADING_MODE must be 'paper', 'live', or 'bridge'")

    csv_path = _str("DATA_CSV_PATH")

    return Settings(
        trading_mode=mode,  # type: ignore[arg-type]
        dry_run=_bool("DRY_RUN", True),
        symbol=_str("SYMBOL", "XAUUSD"),
        timeframe=_str("TIMEFRAME", "M1").upper(),
        bars=_int("BARS", 300),
        poll_seconds=_int("POLL_SECONDS", 20),
        data_csv_path=Path(csv_path) if csv_path else None,
        ema_fast=_int("EMA_FAST", 8),
        ema_slow=_int("EMA_SLOW", 21),
        ema_trend=_int("EMA_TREND", 55),
        atr_period=_int("ATR_PERIOD", 14),
        rsi_period=_int("RSI_PERIOD", 14),
        sl_atr_multiplier=_float("SL_ATR_MULTIPLIER", 1.3),
        tp_atr_multiplier=_float("TP_ATR_MULTIPLIER", 1.8),
        min_stop_points=_int("MIN_STOP_POINTS", 80),
        min_signal_confidence=_float("MIN_SIGNAL_CONFIDENCE", 0.62),
        fixed_lot=_optional_float("FIXED_LOT"),
        min_lot=_float("MIN_LOT", 0.01),
        max_lot=_float("MAX_LOT", 0.10),
        risk_percent=_float("RISK_PERCENT", 0.25),
        max_spread_points=_float("MAX_SPREAD_POINTS", 35),
        max_positions=_int("MAX_POSITIONS", 1),
        max_trades_per_day=_int("MAX_TRADES_PER_DAY", 8),
        cooldown_seconds=_int("COOLDOWN_SECONDS", 180),
        daily_loss_limit_percent=_float("DAILY_LOSS_LIMIT_PERCENT", 2.0),
        magic_number=_int("MAGIC_NUMBER", 260618),
        deviation_points=_int("DEVIATION_POINTS", 20),
        order_comment=_str("ORDER_COMMENT", "trend-scalper-ai"),
        use_llm=_bool("USE_LLM", False),
        deepseek_api_key=_str("DEEPSEEK_API_KEY"),
        deepseek_base_url=_str("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        deepseek_model=_str("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        llm_min_score=_float("LLM_MIN_SCORE", 0.65),
        llm_timeout_seconds=_float("LLM_TIMEOUT_SECONDS", 8.0),
        llm_fail_closed=_bool("LLM_FAIL_CLOSED", True),
        bridge_url=_str("BRIDGE_URL", "http://host.docker.internal:8765"),
        bridge_token=_str("BRIDGE_TOKEN"),
        bridge_host=_str("BRIDGE_HOST", "127.0.0.1"),
        bridge_port=_int("BRIDGE_PORT", 8765),
        signal_host=_str("SIGNAL_HOST", "0.0.0.0"),
        signal_port=_int("SIGNAL_PORT", 8766),
        signal_token=_str("SIGNAL_TOKEN"),
        mt5_login=_optional_int("MT5_LOGIN"),
        mt5_password=_str("MT5_PASSWORD"),
        mt5_server=_str("MT5_SERVER"),
        mt5_path=_str("MT5_PATH"),
        state_path=Path(_str("STATE_PATH", "data/bot_state.json")),
        event_log_path=Path(_str("EVENT_LOG_PATH", "data/trade_events.jsonl")),
        dashboard_settings_path=Path(_str("DASHBOARD_SETTINGS_PATH", "data/dashboard_settings.json")),
        dashboard_events_limit=_int("DASHBOARD_EVENTS_LIMIT", 100),
        dashboard_auto_token=_bool("DASHBOARD_AUTO_TOKEN", True),
        log_level=_str("LOG_LEVEL", "INFO").upper(),
    )


def validate_settings(settings: Settings) -> list[str]:
    errors: list[str] = []

    if settings.trading_mode == "live" and not settings.mt5_path:
        errors.append("MT5_PATH is recommended for live mode so the correct terminal is used.")
    if settings.trading_mode == "bridge" and not settings.bridge_url:
        errors.append("BRIDGE_URL is required when TRADING_MODE=bridge.")
    if settings.trading_mode == "live" and settings.mt5_login and not settings.mt5_password:
        errors.append("MT5_PASSWORD is required when MT5_LOGIN is set.")
    if settings.use_llm and not settings.deepseek_api_key:
        errors.append("DEEPSEEK_API_KEY is required when USE_LLM=true.")
    if settings.risk_percent <= 0 or settings.risk_percent > 5:
        errors.append("RISK_PERCENT should be > 0 and <= 5.")
    if settings.max_lot < settings.min_lot:
        errors.append("MAX_LOT must be greater than or equal to MIN_LOT.")
    if settings.bars < max(settings.ema_trend, settings.atr_period, settings.rsi_period) + 20:
        errors.append("BARS is too small for the configured indicators.")
    if settings.min_signal_confidence < 0 or settings.min_signal_confidence > 1:
        errors.append("MIN_SIGNAL_CONFIDENCE must be between 0 and 1.")
    if settings.llm_min_score < 0 or settings.llm_min_score > 1:
        errors.append("LLM_MIN_SCORE must be between 0 and 1.")

    return errors
