from __future__ import annotations

import argparse
import json
import logging
import math
import threading
import time as time_module
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import Settings, load_settings, validate_settings
from .exit_manager import ExitManager
from .llm_regime_brain import LLMRegimeBrain
from .models import AccountSnapshot, EntrySignal, ExitAction, Rate, RegimeConfig, TradeSignal
from .monitoring import EventStore, RuntimeSettingsStore
from .risk import RiskManager
from .strategy import PullbackScalperStrategy

logger = logging.getLogger(__name__)


class SignalEngine:
    def __init__(
        self,
        settings: Settings,
        event_store: EventStore | None = None,
        runtime_settings: RuntimeSettingsStore | None = None,
    ) -> None:
        self.settings = settings
        self.runtime_settings = runtime_settings or RuntimeSettingsStore(settings)
        self.events = event_store or EventStore(settings, self.runtime_settings)
        self.strategy = PullbackScalperStrategy()
        self.risk = RiskManager(settings)
        self.exit_mgr = ExitManager()
        # Async LLM regime brain — runs in background every 5-15 min
        self._regime_brain = LLMRegimeBrain(settings, interval_seconds=getattr(settings, 'llm_regime_interval_seconds', 300))
        self._current_regime = RegimeConfig()  # Mutable, updated by regime brain
        self._last_market_auto_tune_update = 0.0
        self._regime_brain_started = False
        self._regime_update_lock = threading.Lock()
        self._regime_update_running = False
        self._auto_tune_lock = threading.Lock()

    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self._evaluate(payload)
        self.events.append_signal(payload, result)
        return result

    def start_regime_brain(self) -> None:
        """Start the async LLM regime brain if not already running."""
        if not self._regime_brain_started and self.settings.use_llm and self.settings.deepseek_api_key:
            self._regime_brain.start()
            self._regime_brain_started = True
            logger.info("LLM regime brain started in background")

    def stop_regime_brain(self) -> None:
        """Stop the background regime brain."""
        if self._regime_brain_started:
            self._regime_brain.stop()
            self._regime_brain_started = False

    def get_regime_status(self) -> dict[str, Any]:
        """Return current regime info for API."""
        return self._regime_brain.status()

    def update_regime_from_payload(self, payload: dict[str, Any]) -> None:
        """Feed M15/H1 rates to the regime brain for async analysis.

        Called on every signal request when multi-timeframe data is available.
        The brain decides internally whether it's time to re-evaluate.
        """
        if not self._regime_brain_started:
            return

        # Extract macro timeframes from payload
        rates_by_tf = {}
        multi_data = (
            payload.get("multi_timeframe_rates")
            or payload.get("rates_by_timeframe")
            or payload.get("timeframes")
        )
        if isinstance(multi_data, dict):
            for tf in ("M15", "H1", "H4"):
                if tf in multi_data and isinstance(multi_data[tf], list) and multi_data[tf]:
                    rates_by_tf[tf] = self._normalize_rates(multi_data[tf])

        if not rates_by_tf:
            return

        # Check if enough time has passed since last update
        now = time_module.time()
        last_update = self._current_regime.updated_at
        interval = max(120, getattr(self.settings, 'llm_regime_interval_seconds', 300))

        # Use a max staleness check to prevent permanent blocking if thread crashes
        max_staleness = interval * 3
        if last_update and now - last_update < interval:
            if now - last_update < max_staleness:
                return  # Not time yet
            logger.warning("Regime update stale for %.0fs, forcing update", now - last_update)

        # Prevent overlapping async updates
        with self._regime_update_lock:
            if self._regime_update_running:
                return
            self._regime_update_running = True

        def _async_update():
            try:
                new_regime = self._regime_brain.update_regime_now(rates_by_tf)
                self._current_regime = new_regime
                self._persist_regime_to_dashboard(new_regime)
                logger.info(
                    "Regime updated: mode=%s bias=%s conf=%.2f",
                    new_regime.strategy_mode, new_regime.trading_bias, new_regime.llm_confidence,
                )
            except Exception as exc:
                logger.error("Async regime update failed: %s", exc)
            finally:
                self._regime_update_running = False

        threading.Thread(target=_async_update, daemon=True, name="regime-update").start()

    def _persist_regime_to_dashboard(self, regime: RegimeConfig) -> None:
        """Persist the current regime to dashboard settings for visibility."""
        try:
            self.runtime_settings.apply_auto_tune({
                "auto_tune": True,
                "auto_tune_profile": f"llm-{regime.strategy_mode}-{regime.trading_bias}",
                "auto_tune_summary": (
                    f"LLM Regime: {regime.strategy_mode} / {regime.trading_bias} bias "
                    f"(conf={regime.llm_confidence:.2f}). {regime.llm_reasoning[:200]}"
                ),
                "symbol": self.settings.symbol,
                "timeframe": "M1",
                "bars": 300,
                "ema_fast": regime.ema_fast,
                "ema_slow": regime.ema_slow,
                "ema_trend": regime.ema_trend,
                "atr_period": regime.atr_period,
                "rsi_period": regime.rsi_period,
                "sl_atr_multiplier": regime.sl_atr_multiplier,
                "tp_atr_multiplier": regime.tp_atr_multiplier,
                "min_stop_points": regime.min_stop_points,
                "min_signal_confidence": regime.min_signal_confidence,
                "cooldown_seconds": regime.cooldown_seconds,
                "use_risk_sizing": True,
                "lots": 0.04,
                "max_spread_percent": 0.15,
                "max_spread_points": 0,
                "settings_refresh_seconds": 60,
            })
        except Exception as exc:
            logger.warning("Failed to persist regime to dashboard: %s", exc)

    def _evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Fast-path signal evaluation with MTF trend-confirmed pullback entry."""
        rates_m1 = self._rates(payload)
        point = float(payload.get("point") or self._default_point())
        account = self._account(payload.get("account", {}))
        spread_points = float(payload.get("spread_points", 0.0))
        positions_count = int(payload.get("positions_count", 0))

        if self._regime_brain_started:
            self.update_regime_from_payload(payload)

        region_runtime = self._current_regime.effective_runtime()
        dashboard_overrides = self.runtime_settings.effective()
        runtime: dict[str, Any] = {
            **region_runtime,
            **{k: v for k, v in dashboard_overrides.items() if v is not None},
        }

        magic_number = int(runtime.get("magic_number", self.settings.magic_number))
        allowed, risk_reason = self.risk.can_trade(account, runtime)
        if not allowed:
            return self._hold(risk_reason, runtime=runtime)

        max_spread = float(runtime.get("max_spread_points", self.settings.max_spread_points))
        if max_spread > 0 and spread_points > max_spread:
            return self._hold(f"Spread blocked: {spread_points:.1f} > {max_spread:.1f}", runtime=runtime)

        max_pos = int(runtime.get("max_positions", 1))
        if positions_count >= max_pos:
            return self._hold(f"Position cap: {positions_count} >= {max_pos}", runtime=runtime)

        # ── Extract M5 rates for trend confirmation ──
        rates_m5 = self._extract_tf_rates(payload, "M5") or self._extract_tf_rates(payload, "H1")

        # ── Gold autopilot: merge MTF profile into runtime ──
        if bool(runtime.get("auto_tune", False)):
            profile = self._gold_autopilot_profile(payload, runtime, rates_m1, rates_m5, point)
            if profile:
                runtime = profile["runtime"]
                signal = profile["signal"]
            else:
                signal = self.strategy.analyze(rates_m1, rates_m5, point, runtime)
        else:
            signal = self.strategy.analyze(rates_m1, rates_m5, point, runtime)

        if not signal.is_trade:
            return self._hold(signal.reason, signal.confidence, runtime=runtime)

        # ── Trading bias filter ──
        bias = self._current_regime.trading_bias
        if bias == "bullish" and signal.action == "SELL":
            return self._hold(f"Regime bias ({bias}) blocked SELL", runtime=runtime)
        if bias == "bearish" and signal.action == "BUY":
            return self._hold(f"Regime bias ({bias}) blocked BUY", runtime=runtime)

        # ── R:R gate ──
        est_spread = spread_points * point
        net_tp = signal.tp_distance - est_spread
        net_sl = signal.sl_distance + est_spread
        min_rr = float(runtime.get("min_risk_reward", 1.5))
        if net_tp > 0 and net_sl > 0 and net_tp / net_sl < min_rr:
            return self._hold(
                f"Net R:R {net_tp/net_sl:.1f} < {min_rr:.1f}",
                runtime=runtime,
            )

        sl_points = max(1, int(math.ceil(signal.sl_distance / point)))
        tp_points = max(1, int(math.ceil(signal.tp_distance / point)))
        return {
            "action": signal.action,
            "confidence": signal.confidence,
            "reason": signal.reason,
            "sl_distance": signal.sl_distance,
            "tp_distance": signal.tp_distance,
            "sl_points": sl_points,
            "tp_points": tp_points,
            "magic": magic_number,
            "metadata": signal.metadata,
            "exit_rules": signal.exit_rules,
            "entry_price": signal.entry_price,
            "entry_atr": signal.entry_atr,
        }

    def record_trade_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        success = bool(payload.get("success", False))
        if success:
            self.risk.record_trade(self._account(payload.get("account", {})), success=True)
        self.events.append_trade_result(payload, success)
        return {"ok": True, "recorded": success}

    def evaluate_exit(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Check exit conditions for an active trade. Called by EA after each bar close."""
        trade_id = int(payload.get("trade_id", 0))
        if trade_id <= 0:
            return {"action": "HOLD", "reason": "invalid trade_id"}

        rates_m1 = self._rates(payload)
        if not rates_m1:
            return {"action": "HOLD", "reason": "no rates"}

        rates_m5 = self._extract_tf_rates(payload, "M5")
        trend_dir = 0
        if rates_m5:
            from .indicators import add_indicators
            data = add_indicators(rates_m5, 8, 21, 55, 14, 14)
            ready = [r for r in data if all(r.get(k) is not None for k in ("ema_fast", "ema_slow", "ema_trend"))]
            if ready:
                last = ready[-1]
                ef, es, et = float(last["ema_fast"]), float(last["ema_slow"]), float(last["ema_trend"])
                if ef > es > et:
                    trend_dir = 1
                elif ef < es < et:
                    trend_dir = -1

        result = self.exit_mgr.evaluate(trade_id, rates_m1, rates_m5, trend_dir)
        state = self.exit_mgr.get_state(trade_id) or {}
        point = float(payload.get("point") or self._default_point())

        response: dict[str, Any] = {"action": result.action, "reason": result.reason}
        if state.get("breakeven_triggered") and not state.get("trailing_active"):
            response["new_sl_points"] = 0
        if state.get("trailing_active"):
            response["trailing_active"] = True
        return response

    def _rates(self, payload: dict[str, Any]) -> list[Rate]:
        rates = payload.get("rates")
        if not isinstance(rates, list):
            raise ValueError("Payload must include a rates list")

        return self._normalize_rates(rates)

    def _rates_by_timeframe(self, payload: dict[str, Any], fallback_rates: list[Rate]) -> dict[str, list[Rate]]:
        raw = (
            payload.get("multi_timeframe_rates")
            or payload.get("rates_by_timeframe")
            or payload.get("timeframes")
        )
        normalized: dict[str, list[Rate]] = {}
        if isinstance(raw, dict):
            for timeframe, rates in raw.items():
                if isinstance(timeframe, str) and isinstance(rates, list) and rates:
                    normalized[timeframe.upper()] = self._normalize_rates(rates)

        payload_timeframe = str(payload.get("timeframe", self.settings.timeframe)).upper()
        normalized.setdefault(payload_timeframe, fallback_rates)
        return normalized

    def _normalize_rates(self, rates: list[Any]) -> list[Rate]:
        normalized: list[Rate] = []
        for row in rates:
            if not isinstance(row, dict):
                raise ValueError("Each rate must be an object")
            normalized.append(
                {
                    **row,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                }
            )
        return normalized

    def _account(self, raw: dict[str, Any]) -> AccountSnapshot:
        return AccountSnapshot(
            balance=float(raw.get("balance", 10_000.0)),
            equity=float(raw.get("equity", raw.get("balance", 10_000.0))),
            currency=str(raw.get("currency", "USD")),
        )

    def _default_point(self) -> float:
        return 0.01 if self.settings.symbol.upper().startswith("XAU") else 0.00001

    def _persist_market_profile(self, runtime: dict[str, Any]) -> None:
        now = time_module.time()
        refresh_seconds = max(60, int(runtime.get("settings_refresh_seconds", 60)))
        with self._auto_tune_lock:
            if self._last_market_auto_tune_update and now - self._last_market_auto_tune_update < refresh_seconds:
                return
            self._last_market_auto_tune_update = now

        keys = {
            "symbol",
            "timeframe",
            "bars",
            "ema_fast",
            "ema_slow",
            "ema_trend",
            "atr_period",
            "rsi_period",
            "sl_atr_multiplier",
            "tp_atr_multiplier",
            "min_stop_points",
            "min_signal_confidence",
            "use_risk_sizing",
            "lots",
            "max_positions",
            "max_spread_points",
            "max_spread_percent",
            "cooldown_seconds",
            "one_trade_per_bar",
            "bars_to_send",
            "request_timeout_ms",
            "request_retries",
            "retry_delay_ms",
            "settings_refresh_seconds",
            "auto_tune",
            "auto_tune_profile",
            "auto_tune_summary",
        }
        self.runtime_settings.apply_auto_tune({key: runtime[key] for key in keys if key in runtime})
        self._last_market_auto_tune_update = now

    def _hold(
        self,
        reason: str,
        confidence: float = 0.0,
        runtime: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        effective = runtime or {}
        return {
            "action": "HOLD",
            "confidence": confidence,
            "reason": reason,
            "sl_distance": 0.0,
            "tp_distance": 0.0,
            "sl_points": 0,
            "tp_points": 0,
            "magic": int(effective.get("magic_number", self.settings.magic_number)),
            "metadata": {},
        }

    def _extract_tf_rates(self, payload: dict[str, Any], timeframe: str) -> list[Rate] | None:
        multi_data = (
            payload.get("multi_timeframe_rates")
            or payload.get("rates_by_timeframe")
            or payload.get("timeframes")
        )
        if isinstance(multi_data, dict):
            tf_data = multi_data.get(timeframe)
            if isinstance(tf_data, list) and tf_data:
                return self._normalize_rates(tf_data)
        return None

    def _gold_autopilot_profile(
        self,
        payload: dict[str, Any],
        runtime: dict[str, Any],
        rates_m1: list[Rate],
        rates_m5: list[Rate] | None,
        point: float,
    ) -> dict[str, Any] | None:
        """Gold autopilot: use M5 trend + M1 entry with optimized params."""
        symbol = str(payload.get("symbol", runtime.get("symbol", self.settings.symbol))).upper()
        if _detect_asset_class(symbol) != "gold":
            return None

        profile = _gold_market_profiles().get("M1", {})
        merged_runtime = {**runtime, **profile}

        signal = self.strategy.analyze(rates_m1, rates_m5, point, merged_runtime)
        if not signal.is_trade:
            return None

        merged_runtime["auto_tune"] = True
        merged_runtime["auto_tune_profile"] = "gold-autopilot"
        merged_runtime["auto_tune_summary"] = (
            f"Gold autopilot: {signal.action} conf={signal.confidence:.2f} "
            f"SL={signal.sl_distance:.5f} TP={signal.tp_distance:.5f}"
        )
        self._persist_market_profile(merged_runtime)
        return {"runtime": merged_runtime, "signal": signal}


class SignalContext:
    def __init__(
        self,
        engine: SignalEngine,
        password: str,
        event_store: EventStore,
        runtime_settings: RuntimeSettingsStore,
    ) -> None:
        self.engine = engine
        self.password = password
        self.events = event_store
        self.runtime_settings = runtime_settings


context: SignalContext | None = None


class SignalHandler(BaseHTTPRequestHandler):
    server_version = "TrendScalperSignalService/0.1"
    _MAX_BODY_BYTES = 1_048_576  # 1 MB limit
    _rate_limit: dict[str, list[float]] = {}
    _MAX_REQUESTS_PER_WINDOW = 60
    _RATE_WINDOW_SECONDS = 10.0

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send({"ok": True})
            return
        if parsed.path in {"/", "/dashboard"}:
            self._send_html(dashboard_html())
            return
        if parsed.path == "/api/status":
            if not self._authorized():
                self._send({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                return
            self._send(context.events.status() if context else {"ok": False})
            return
        if parsed.path == "/api/events":
            if not self._authorized():
                self._send({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                return
            query = parse_qs(parsed.query)
            limit = int(query.get("limit", ["100"])[0] or 100)
            self._send({"events": context.events.recent(limit) if context else []})
            return
        if parsed.path == "/api/settings":
            if not self._authorized():
                self._send({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                return
            self._send(
                {
                    "settings": context.runtime_settings.effective() if context else {},
                    "overrides": context.runtime_settings.get_overrides() if context else {},
                }
            )
            return
        if parsed.path == "/api/runtime-settings":
            if not self._authorized():
                self._send({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                return
            self._send(context.runtime_settings.effective() if context else {})
            return
        if parsed.path == "/api/symbol-info":
            query = parse_qs(parsed.query)
            symbol = query.get("symbol", ["XAUUSD"])[0].upper()
            self._send({
                "symbol": symbol,
                "type": _detect_asset_class(symbol),
                "presets": _asset_presets(symbol),
            })
            return
        if parsed.path == "/api/regime":
            if not self._authorized():
                self._send({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                return
            if context:
                self._send(context.engine.get_regime_status())
            else:
                self._send({"error": "context not available"}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        self._send({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        try:
            if context is None:
                self._send({"error": "service not initialized"}, HTTPStatus.SERVICE_UNAVAILABLE)
                return
            if not self._authorized():
                self._send({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                return
            if not self._check_rate_limit():
                self._send({"error": "rate limit exceeded"}, HTTPStatus.TOO_MANY_REQUESTS)
                return

            payload = self._read_json()
            if self.path == "/signal":
                self._send(context.engine.evaluate(payload))
                return
            if self.path == "/exit":
                self._send(context.engine.evaluate_exit(payload))
                return
            if self.path == "/trade-result":
                self._send(context.engine.record_trade_result(payload))
                return
            if self.path == "/api/settings":
                self._send({"settings": context.runtime_settings.update(payload)})
                return
            self._send({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            logger.exception("Signal request failed: %s", exc)
            self._send({"error": "internal error"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _authorized(self) -> bool:
        if not context or not context.password:
            return False
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and auth[7:] == context.password:
            return True
        header_pw = self.headers.get("X-Signal-Password", "")
        return header_pw == context.password

    def _check_rate_limit(self) -> bool:
        now = time_module.time()
        client_key = self.client_address[0]
        window = self._RATE_WINDOW_SECONDS
        if client_key not in self._rate_limit:
            self._rate_limit[client_key] = []
        timestamps = self._rate_limit[client_key]
        timestamps[:] = [t for t in timestamps if now - t < window]
        if len(timestamps) >= self._MAX_REQUESTS_PER_WINDOW:
            return False
        timestamps.append(now)
        if len(self._rate_limit) > 1000:
            self._rate_limit.clear()
        return True

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        if length > self._MAX_BODY_BYTES:
            raise ValueError(f"Request body exceeds {self._MAX_BODY_BYTES} bytes")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _send(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, default=str, separators=(",", ":")).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = html.encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# --- Symbol detection (mirrors EA logic) ---
_CRYPTO_PREFIXES = {
    "SOL", "BTC", "ETH", "BNB", "XRP", "ADA", "DOGE", "DOT",
    "LTC", "MATIC", "AVAX", "LINK", "UNI", "ATOM", "FIL",
    "APT", "ARB", "OP", "SUI", "TRX", "TON", "NEAR", "ICP",
    "BCH", "EOS", "ETC", "VET", "ALGO", "MANA", "SAND",
    "AXS", "EGLD", "RUNE", "FTM", "FLOW", "GRT", "IMX", "SNX",
    "XTZ", "THETA", "ZEC", "DASH", "NEO", "QTUM", "OMG", "BAT",
    "ZRX", "ENJ", "CHZ", "CELO", "COMP", "MKR", "YFI", "CRV",
}


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


def _asset_presets(symbol: str) -> dict[str, Any]:
    asset = _detect_asset_class(symbol)
    if asset == "gold":
        return {
            "type": "gold", "icon": "🥇",
            "max_spread_pct": 0.15,
            "ema_fast": 8, "ema_slow": 21, "ema_trend": 55,
            "atr_period": 14, "rsi_period": 14,
            "sl_atr": 1.3, "tp_atr": 1.8,
            "min_confidence": 0.62, "llm_min_score": 0.65,
            "risk_percent": 0.25, "cooldown": 180,
            "description": "Gold — moderate volatility, trend-following EMA cross + ATR stops",
        }
    if asset == "crypto":
        return {
            "type": "crypto", "icon": "💎",
            "max_spread_pct": 3.0,
            "ema_fast": 5, "ema_slow": 13, "ema_trend": 34,
            "atr_period": 10, "rsi_period": 10,
            "sl_atr": 1.8, "tp_atr": 3.0,
            "min_confidence": 0.55, "llm_min_score": 0.60,
            "risk_percent": 0.15, "cooldown": 300,
            "description": "Crypto — high volatility, fast EMAs, wider stops, lower risk per trade",
        }
    if asset == "forex":
        return {
            "type": "forex", "icon": "💱",
            "max_spread_pct": 0.30,
            "ema_fast": 8, "ema_slow": 21, "ema_trend": 55,
            "atr_period": 14, "rsi_period": 14,
            "sl_atr": 1.3, "tp_atr": 2.0,
            "min_confidence": 0.62, "llm_min_score": 0.65,
            "risk_percent": 0.25, "cooldown": 180,
            "description": "Forex — stable, tight spreads, EMA crossover + RSI confirmation",
        }
    return {
        "type": "other", "icon": "📊",
        "max_spread_pct": 0.50,
        "ema_fast": 8, "ema_slow": 21, "ema_trend": 55,
        "atr_period": 14, "rsi_period": 14,
        "sl_atr": 1.3, "tp_atr": 1.8,
        "min_confidence": 0.62, "llm_min_score": 0.65,
        "risk_percent": 0.25, "cooldown": 180,
        "description": "Unknown — default trend scalper settings",
    }

 
def _gold_market_profiles() -> dict[str, dict[str, Any]]:
    """Default gold market profiles for M1/M5/M15 scalping.
    
    These are starting defaults that can be overridden by LLM optimization.
    M1 profile is optimized for aggressive scalping with fast take-profit.
    """
    return {
        "M1": {
            "timeframe": "M1",
            "bars": 300,
            "ema_fast": 8,
            "ema_slow": 21,
            "ema_trend": 55,
            "atr_period": 14,
            "rsi_period": 14,
            "sl_atr_multiplier": 1.3,
            "tp_atr_multiplier": 1.8,
            "min_stop_points": 80,
            "min_signal_confidence": 0.64,
            "use_risk_sizing": True,
            "lots": 0.12,
            "max_positions": 2,
            "max_spread_percent": 0.15,
            "cooldown_seconds": 113,
            "one_trade_per_bar": True,
            "request_timeout_ms": 30000,
            "request_retries": 1,
            "retry_delay_ms": 750,
        },
        "M5": {
            "timeframe": "M5",
            "bars": 300,
            "ema_fast": 9,
            "ema_slow": 21,
            "ema_trend": 55,
            "atr_period": 14,
            "rsi_period": 14,
            "sl_atr_multiplier": 1.35,
            "tp_atr_multiplier": 2.0,
            "min_stop_points": 100,
            "min_signal_confidence": 0.64,
            "use_risk_sizing": True,
            "lots": 0.04,
            "max_positions": 1,
            "max_spread_percent": 0.15,
            "cooldown_seconds": 210,
            "one_trade_per_bar": True,
            "request_timeout_ms": 30000,
            "request_retries": 1,
            "retry_delay_ms": 750,
        },
        "M15": {
            "timeframe": "M15",
            "bars": 240,
            "ema_fast": 10,
            "ema_slow": 30,
            "ema_trend": 80,
            "atr_period": 14,
            "rsi_period": 14,
            "sl_atr_multiplier": 1.5,
            "tp_atr_multiplier": 2.2,
            "min_stop_points": 120,
            "min_signal_confidence": 0.62,
            "use_risk_sizing": True,
            "lots": 0.04,
            "max_positions": 1,
            "max_spread_percent": 0.15,
            "cooldown_seconds": 300,
            "one_trade_per_bar": True,
            "request_timeout_ms": 30000,
            "request_retries": 1,
            "retry_delay_ms": 750,
        },
    }


def _gold_market_selection_score(
    timeframe: str,
    signal: TradeSignal,
    actions: dict[str, str],
) -> float:
    score = float(signal.confidence)
    if not signal.is_trade:
        return score * 0.35

    score += {"M1": 0.02, "M5": 0.05, "M15": 0.03}.get(timeframe, 0.0)
    action = signal.action
    for context_timeframe, aligned_bonus, opposite_penalty in (
        ("M15", 0.10, 0.18),
        ("M5", 0.07, 0.12),
        ("M1", 0.03, 0.06),
    ):
        context_action = actions.get(context_timeframe)
        if not context_action or context_timeframe == timeframe:
            continue
        if context_action == action:
            score += aligned_bonus
        else:
            score -= opposite_penalty
    return score


def _gold_lots_cap(risk_percent: float) -> float:
    return round(max(0.01, min(0.20, risk_percent * 0.4)), 2)


def _gold_market_summary(
    selected_timeframe: str,
    analyses: list[dict[str, Any]],
    selected_signal: TradeSignal,
) -> str:
    parts = []
    for item in analyses:
        signal = item["signal"]
        parts.append(f"{item['timeframe']} {signal.action} {float(signal.confidence):.2f}")
    selected_runtime = next(
        item["runtime"] for item in analyses if item["timeframe"] == selected_timeframe
    )
    return (
        f"Gold MTF autopilot selected {selected_timeframe}: "
        f"EMA {selected_runtime['ema_fast']}/{selected_runtime['ema_slow']}/{selected_runtime['ema_trend']}, "
        f"confidence {selected_runtime['min_signal_confidence']:.2f}, "
        f"SL x{selected_runtime['sl_atr_multiplier']:.2f}, TP x{selected_runtime['tp_atr_multiplier']:.2f}. "
        f"Scan M1/M5/M15 every 60s: {', '.join(parts)}. "
        f"Decision {selected_signal.action} {selected_signal.confidence:.2f}."
    )


def _gold_market_hold_reason(analyses: list[dict[str, Any]]) -> str:
    parts = []
    for item in analyses:
        signal = item["signal"]
        parts.append(f"{item['timeframe']} {signal.action} {float(signal.confidence):.2f}")
    return f"Gold MTF HOLD: no M1/M5/M15 trend scalp passed filters ({', '.join(parts)})"


def dashboard_html() -> str:
    return DASHBOARD_HTML_TEMPLATE


DASHBOARD_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trend Scalper AI · Dashboard</title>
<style>
:root{color-scheme:dark;font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;--bg:#090b10;--surface:#11151e;--border:#1e2433;--muted:#6b7280;--accent:#6366f1;--green:#10b981;--red:#ef4444;--amber:#f59e0b;--text:#e5e7eb;--card-bg:#161b26;--hover:#1f2937}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);min-height:100vh}
header{position:sticky;top:0;z-index:10;background:var(--bg);border-bottom:1px solid var(--border);padding:14px 28px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;backdrop-filter:blur(12px)}
h1{font-size:20px;font-weight:700;letter-spacing:-0.02em;display:flex;align-items:center;gap:10px}
h1 span{background:linear-gradient(135deg,var(--accent),#a855f7);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.dot{width:8px;height:8px;border-radius:99px;display:inline-block;animation:pulse 2s infinite}
.dot-online{background:var(--green);box-shadow:0 0 8px var(--green)}
.dot-offline{background:var(--red)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.5}}
main{padding:20px 28px;display:grid;gap:18px;max-width:1440px;margin:0 auto}
.row{display:flex;flex-wrap:wrap;gap:10px;align-items:flex-end}
.row-center{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
.row-spread{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between}
.grid3{display:grid;grid-template-columns:repeat(auto-fill,minmax(155px,1fr));gap:10px}
.panel{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:18px}
.panel h2{font-size:15px;font-weight:600;margin-bottom:12px;display:flex;align-items:center;gap:8px}
.card{background:var(--card-bg);border:1px solid var(--border);border-radius:12px;padding:14px;transition:all .15s}
.card:hover{border-color:var(--accent)}
.card .label{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.card .value{font-size:22px;font-weight:700;margin-top:3px;overflow:hidden;text-overflow:ellipsis}
.card .sub{font-size:11px;color:var(--muted);margin-top:2px}
input,select{background:var(--card-bg);color:var(--text);border:1px solid var(--border);border-radius:10px;padding:9px 11px;font-size:13px;outline:none;transition:border .15s}
input:focus,select:focus{border-color:var(--accent)}
input[type=number]{width:80px}
button{border:0;border-radius:10px;padding:9px 16px;font-size:13px;font-weight:600;cursor:pointer;transition:all .15s;white-space:nowrap}
button:hover{filter:brightness(1.15)}
.btn-accent{background:var(--accent);color:#fff}
.btn-green{background:var(--green);color:#fff}
.btn-red{background:var(--red);color:#fff}
.btn-outline{background:transparent;border:1px solid var(--border);color:var(--text)}
.field{display:grid;gap:3px}
.field .label{font-size:10px;text-transform:uppercase;color:var(--muted);letter-spacing:.04em}
.badge{font-size:10px;padding:2px 9px;border-radius:99px;font-weight:600}
.badge-live{background:var(--green)22;color:var(--green);border:1px solid var(--green)44}
.badge-dry{background:var(--red)22;color:var(--red);border:1px solid var(--red)44}
.badge-llm{background:#a855f722;color:#a855f7;border:1px solid #a855f744}
.divider{height:1px;background:var(--border);margin:14px 0}
table{width:100%;border-collapse:collapse;font-size:12px}
td,th{padding:8px 10px;text-align:left;border-bottom:1px solid var(--border)}
th{font-size:10px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);background:var(--surface);position:sticky;top:0}
th:first-child,td:first-child{border-radius:8px 0 0 8px}
th:last-child,td:last-child{border-radius:0 8px 8px 0}
.action-BUY{color:var(--green);font-weight:700}
.action-SELL{color:var(--red);font-weight:700}
.action-HOLD{color:var(--amber);font-weight:700}
.muted{color:var(--muted);font-size:12px}
.error{color:var(--red)}
.pill{font-size:11px;padding:3px 8px;border-radius:6px;cursor:pointer;transition:all .15s;border:1px solid transparent}
.pill:hover{border-color:var(--accent)}
.pill.active{background:var(--accent)22;color:var(--accent);border-color:var(--accent)44}
.tabs{display:flex;gap:6px;margin-bottom:14px}
.hero{background:radial-gradient(circle at top left,#6366f133,transparent 32%),linear-gradient(135deg,#111827,#0f172a);border-color:#334155}
.hero h2{font-size:17px}
.autopilot-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin-top:12px}
.metric{background:#02061766;border:1px solid #334155;border-radius:12px;padding:12px}
.metric .k{font-size:10px;text-transform:uppercase;color:var(--muted);letter-spacing:.06em}
.metric .v{font-size:18px;font-weight:700;margin-top:4px}
.soft-note{font-size:12px;color:#cbd5e1;line-height:1.5}
.advanced-hidden{display:none!important}
.mode-chip{font-size:11px;border-radius:99px;padding:4px 10px;border:1px solid #818cf866;color:#c4b5fd;background:#6366f122}
.hidden{display:none!important}
.toast{position:fixed;bottom:20px;right:20px;background:var(--green);color:#fff;padding:10px 18px;border-radius:10px;font-size:13px;font-weight:600;z-index:99;animation:fadeIn .3s,fadeOut .3s 1.7s forwards}
@keyframes fadeIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
@keyframes fadeOut{from{opacity:1;transform:translateY(0)}to{opacity:0;transform:translateY(10px)}}
</style>
</head>
<body>
<header>
<h1>⚡ <span>Trend Scalper AI</span></h1>
<div class="row-center" style="gap:10px">
  <input id="tokenInput" type="password" placeholder="Signal password" style="width:150px">
  <button onclick="savePassword()" class="btn-outline">Auth</button>
  <div id="topBar" class="row-center" style="gap:14px;font-size:12px;color:var(--muted)">Loading...</div>
</div>
</header>
<main>

<!-- Quick Stats Row -->
<section class="grid3" id="statCards"></section>

<!-- Asset Selector + Presets -->
<section class="panel" id="assetPanel">
  <h2>🎯 Smart Asset Selector</h2>
  <div class="row" style="gap:10px;flex-wrap:wrap">
    <label class="field">
      <span class="label">Symbol</span>
      <input id="symInput" type="text" value="SOLUSD" style="width:110px" onchange="onSymbolChange()" oninput="clearTimeout(symTimeout);symTimeout=setTimeout(onSymbolChange,400)">
    </label>
    <button onclick="onSymbolChange()" class="btn-outline">🔍 Detect</button>
    <span id="symResult" class="muted"></span>
    <span style="margin-left:auto" id="presetInfo"></span>
    <button onclick="applyPresets()" class="btn-accent">⚡ Apply Presets</button>
  </div>
  <div class="divider"></div>
  <div class="tabs" id="assetTabs">
    <div class="pill active" onclick="selectAsset('crypto')">💎 Crypto</div>
    <div class="pill" onclick="selectAsset('forex')">💱 Forex</div>
    <div class="pill" onclick="selectAsset('gold')">🥇 Gold</div>
    <div class="pill" onclick="selectAsset('other')">📊 Other</div>
  </div>
  <div id="presetDetails" class="row" style="gap:12px;flex-wrap:wrap"></div>
</section>

<!-- Live Control Panel -->
<section class="panel" id="corePanel">
  <div class="row-spread">
    <h2>🎛 Live Control <span id="eaBadge" class="badge badge-live">LIVE</span></h2>
    <div class="row" style="gap:8px">
      <label class="field"><span class="label">Trading</span><select id="cfgMode"><option value="live">live</option><option value="paper">paper</option><option value="bridge">bridge</option></select></label>
      <label class="field"><span class="label">Dry Run</span><select id="cfgDry"><option value="true">true</option><option value="false">false</option></select></label>
      <label class="field"><span class="label">LLM</span><select id="cfgLlm"><option value="false">off</option><option value="true">on</option></select></label>
      <label class="field"><span class="label">Fail Closed</span><select id="cfgFail"><option value="true">true</option><option value="false">false</option></select></label>
      <button onclick="saveCoreSettings()" class="btn-accent">Save</button>
    </div>
  </div>
</section>

<!-- AI Autopilot -->
<section class="panel hero hidden" id="aiAutopilotPanel">
  <div class="row-spread">
    <h2>🧠 LLM Expert Autopilot <span id="autoProfile" class="mode-chip">manual</span></h2>
    <span class="soft-note">You control Risk % · Daily Loss % · Max Trades/Day · Max Positions · LLM Min Score · LLM Timeout.</span>
  </div>
  <div class="autopilot-grid" id="autoTuneCards"></div>
  <div class="divider"></div>
  <div id="autoTuneSummary" class="soft-note">Enable LLM to activate automatic expert tuning.</div>
</section>

<!-- Strategy Parameters -->
<section class="panel" id="strategyPanel">
  <h2>📊 Strategy</h2>
  <div class="row" style="flex-wrap:wrap;gap:10px">
    <label class="field"><span class="label">Timeframe</span><select id="cfgTf"><option>M1</option><option>M5</option><option>M15</option><option>H1</option><option>H4</option><option>D1</option></select></label>
    <label class="field"><span class="label">Bars</span><input id="cfgBars" type="number" min="50" max="1000" value="300"></label>
    <label class="field"><span class="label">EMA Fast</span><input id="cfgEmaF" type="number" min="2" max="200" value="8"></label>
    <label class="field"><span class="label">EMA Slow</span><input id="cfgEmaS" type="number" min="3" max="200" value="21"></label>
    <label class="field"><span class="label">EMA Trend</span><input id="cfgEmaT" type="number" min="5" max="500" value="55"></label>
    <label class="field"><span class="label">ATR</span><input id="cfgAtr" type="number" min="2" max="100" value="14"></label>
    <label class="field"><span class="label">RSI</span><input id="cfgRsi" type="number" min="2" max="100" value="14"></label>
    <label class="field"><span class="label">SL Mult</span><input id="cfgSlAtr" type="number" step="0.1" min="0.5" max="5" value="1.3"></label>
    <label class="field"><span class="label">TP Mult</span><input id="cfgTpAtr" type="number" step="0.1" min="0.5" max="10" value="1.8"></label>
    <label class="field"><span class="label">Min Stop</span><input id="cfgMinStop" type="number" min="10" max="1000" value="80"></label>
    <label class="field"><span class="label">Confidence</span><input id="cfgMinConf" type="number" step="0.01" min="0" max="1" value="0.62"></label>
    <button onclick="saveStrategy()" class="btn-green">Save</button>
  </div>
</section>

<!-- EA Execution -->
<section class="panel" id="eaPanel">
  <h2>🤖 MT5 EA</h2>
  <div class="row" style="flex-wrap:wrap;gap:10px">
    <label class="field"><span class="label">Max Spread %</span><input id="cfgSpreadPct" type="number" step="0.1" min="0" max="100" value="0" style="width:80px"></label>
    <label class="field"><span class="label">Spread Pts</span><input id="cfgSpreadPts" type="number" min="0" value="0" style="width:80px"></label>
    <label class="field"><span class="label">Sizing</span><select id="cfgRiskSizing"><option value="false">fixed lots</option><option value="true">risk %</option></select></label>
    <label class="field"><span class="label">Lots / Max Lots</span><input id="cfgLots" type="number" step="0.01" min="0.01" max="100" value="0.01" style="width:80px"></label>
    <label class="field"><span class="label">Max Positions</span><input id="cfgMaxPos" type="number" min="1" max="100" value="1" style="width:80px"></label>
    <label class="field"><span class="label">Cooldown</span><input id="cfgCooldown" type="number" min="0" value="180" style="width:80px"></label>
    <label class="field"><span class="label">Magic</span><input id="cfgMagic" type="number" value="260618" style="width:90px"></label>
    <label class="field"><span class="label">Deviation</span><input id="cfgDev" type="number" min="0" value="20" style="width:80px"></label>
    <label class="field"><span class="label">1 Trade/Bar</span><select id="cfgOneBar"><option value="true">on</option><option value="false">off</option></select></label>
    <button onclick="saveEaSettings()" class="btn-green">Save EA</button>
  </div>
</section>

<!-- Risk + LLM -->
<section class="panel" id="riskPanel">
  <h2>🛡 Risk & 🤖 LLM</h2>
  <div class="row" style="flex-wrap:wrap;gap:10px">
    <label class="field"><span class="label">Risk %</span><input id="cfgRiskPct" type="number" step="0.01" min="0.01" max="5" value="0.25" style="width:80px"></label>
    <label class="field"><span class="label">Daily Loss %</span><input id="cfgDailyLoss" type="number" step="0.1" min="0" max="100" value="2.0" style="width:90px"></label>
    <label class="field"><span class="label">Max Trades/Day</span><input id="cfgMaxTrades" type="number" min="1" max="1000" value="8" style="width:90px"></label>
    <label class="field"><span class="label">LLM Min Score</span><input id="cfgLlmScore" type="number" step="0.01" min="0" max="1" value="0.65" style="width:90px"></label>
    <label class="field"><span class="label">LLM Timeout</span><input id="cfgLlmTimeout" type="number" min="1" max="60" value="8" style="width:80px"></label>
    <label class="field"><span class="label">Max Positions</span><input id="cfgMaxPosLlm" type="number" min="1" value="2" style="width:85px" title="Set to any number — no upper limit"></label>
    <button onclick="saveStrategy()" class="btn-accent">Save</button>
  </div>
</section>

<!-- Events Table -->
<section class="panel">
  <h2>📋 Live Events</h2>
  <div style="max-height:400px;overflow-y:auto">
    <table><thead><tr><th>Time</th><th>Type</th><th>Symbol</th><th>Action</th><th>Conf</th><th>Reason</th></tr></thead>
    <tbody id="eventsTable"><tr><td colspan="6" class="muted">Loading...</td></tr></tbody></table>
  </div>
</section>

<div id="toast" class="hidden"></div>
</main>
<script>
const tokenInput=document.getElementById('tokenInput')||null;
let symTimeout=null;
let currentPreset=null;
function authHeaders(){const pw=localStorage.getItem('signalPassword')||'';return pw?{'X-Signal-Password':pw}:{};}
const h=()=>authHeaders();
async function requestJson(p,opts={},retried=false){
  const r=await fetch(p,{...opts,headers:{...(opts.headers||{}),...h()}});
  if(r.status===401&&!retried&&localStorage.getItem('signalPassword')){
    localStorage.removeItem('signalPassword');
    if(tokenInput)tokenInput.value='';
    return requestJson(p,opts,true);
  }
  if(!r.ok)throw new Error(r.status+' '+(await r.text()));
  return r.json();
}
async function G(p){return requestJson(p)}
async function P(p,d){return requestJson(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)})}
function B(v){return v?'true':'false'}
function toast(msg){const t=document.getElementById('toast');t.textContent=msg;t.className='toast';setTimeout(()=>t.className='hidden',2200)}
function showError(action,e){console.error(action,e);toast('❌ '+action+': '+String(e.message||e).slice(0,140))}
function savePassword(){
  const value=(tokenInput&&tokenInput.value.trim())||'';
  if(value)localStorage.setItem('signalPassword',value);else localStorage.removeItem('signalPassword');
  toast(value?'Password saved':'Password cleared');
  refresh();
}
const coreFieldIds=['cfgMode','cfgDry','cfgLlm','cfgFail'];
const strategyFieldIds=['symInput','cfgTf','cfgBars','cfgEmaF','cfgEmaS','cfgEmaT','cfgAtr','cfgRsi','cfgSlAtr','cfgTpAtr','cfgMinStop','cfgMinConf','cfgLlmScore','cfgLlmTimeout','cfgRiskPct','cfgDailyLoss','cfgMaxTrades'];
const eaFieldIds=['cfgDry','cfgRiskSizing','cfgSpreadPct','cfgSpreadPts','cfgLots','cfgMaxPos','cfgCooldown','cfgMagic','cfgDev','cfgOneBar'];
const riskLlmFieldIds=['cfgRiskPct','cfgDailyLoss','cfgMaxTrades','cfgLlmScore','cfgLlmTimeout','cfgMaxPosLlm'];
let autoSaveTimer=null;
const dirtyFields=new Set();
function markDirty(ids){ids.forEach(id=>dirtyFields.add(id))}
function markSaved(ids){ids.forEach(id=>dirtyFields.delete(id))}
function setValue(id,value){
  const el=document.getElementById(id);
  if(!el||document.activeElement===el||dirtyFields.has(id))return;
  el.value=value==null?'':String(value);
}
document.addEventListener('input',e=>{if(e.target&&e.target.id)dirtyFields.add(e.target.id)});
document.addEventListener('change',e=>{if(e.target&&e.target.id)dirtyFields.add(e.target.id)});
document.addEventListener('input',e=>{if(e.target&&riskLlmFieldIds.includes(e.target.id))scheduleAutoTuneSave()});
document.addEventListener('change',e=>{if(e.target&&riskLlmFieldIds.includes(e.target.id))scheduleAutoTuneSave()});

let gPresets={};
let gDetectedType='other';

async function onSymbolChange(){
  const sym=document.getElementById('symInput').value.trim().toUpperCase()||'SOLUSD';
  const result=document.getElementById('symResult');
  try{
    const info=await G('/api/symbol-info?symbol='+encodeURIComponent(sym));
    gPresets=info.presets||{};
    gDetectedType=info.type||'other';
    result.innerHTML=`<span style="color:var(--accent)">${info.type.toUpperCase()}</span> ${gPresets.icon||''}`;
    selectAsset(gDetectedType);
    renderPresets();
  }catch(e){result.innerHTML=`<span class="error">${e.message}</span>`}
}

function selectAsset(type){
  gDetectedType=type;
  const tabs=document.querySelectorAll('#assetTabs .pill');
  tabs.forEach(t=>t.classList.remove('active'));
  if(type==='crypto')tabs[0].classList.add('active');
  else if(type==='forex')tabs[1].classList.add('active');
  else if(type==='gold')tabs[2].classList.add('active');
  else tabs[3].classList.add('active');
  // fetch presets for this type
  fetchPresetsForType(type);
}

async function fetchPresetsForType(type){
  const sym=document.getElementById('symInput').value.trim().toUpperCase();
  const lookupSym={'crypto':'SOLUSD','forex':'EURUSD','gold':'XAUUSD','other':'XAUUSD'};
  try{
    const info=await G('/api/symbol-info?symbol='+encodeURIComponent(lookupSym[type]||sym));
    gPresets=info.presets||{};
    gDetectedType=info.type||type;
    renderPresets();
  }catch(e){}
}

function renderPresets(){
  const el=document.getElementById('presetDetails');
  const p=gPresets;
  el.innerHTML=`
    <span class="label">${p.icon||''} ${(p.type||'?').toUpperCase()} — ${p.description||''}</span>
    <span class="label">Spread≤${p.max_spread_pct||0}% EMA(${p.ema_fast}/${p.ema_slow}/${p.ema_trend}) ATR${p.atr_period} RSI${p.rsi_period} SLx${p.sl_atr} TPx${p.tp_atr} Conf≥${p.min_confidence} Risk${p.risk_percent}%</span>`;
}

function applyPresets(){
  const p=gPresets;
  document.getElementById('cfgSpreadPct').value=p.max_spread_pct||0;
  document.getElementById('cfgEmaF').value=p.ema_fast||8;
  document.getElementById('cfgEmaS').value=p.ema_slow||21;
  document.getElementById('cfgEmaT').value=p.ema_trend||55;
  document.getElementById('cfgAtr').value=p.atr_period||14;
  document.getElementById('cfgRsi').value=p.rsi_period||14;
  document.getElementById('cfgSlAtr').value=p.sl_atr||1.3;
  document.getElementById('cfgTpAtr').value=p.tp_atr||1.8;
  document.getElementById('cfgMinConf').value=p.min_confidence||0.62;
  document.getElementById('cfgLlmScore').value=p.llm_min_score||0.65;
  document.getElementById('cfgRiskPct').value=p.risk_percent||0.25;
  document.getElementById('cfgCooldown').value=p.cooldown||180;
  markDirty(['cfgSpreadPct','cfgEmaF','cfgEmaS','cfgEmaT','cfgAtr','cfgRsi','cfgSlAtr','cfgTpAtr','cfgMinConf','cfgLlmScore','cfgRiskPct','cfgCooldown']);
  toast('✅ Presets applied! Click Save buttons to persist.');
}

function llmEnabled(){return document.getElementById('cfgLlm').value==='true'}

function riskLlmPayload(){
  return {
    auto_tune:llmEnabled(),
    use_llm:llmEnabled(),
    risk_percent:parseFloat(document.getElementById('cfgRiskPct').value),
    daily_loss_limit_percent:parseFloat(document.getElementById('cfgDailyLoss').value),
    max_trades_per_day:parseInt(document.getElementById('cfgMaxTrades').value),
    llm_min_score:parseFloat(document.getElementById('cfgLlmScore').value),
    llm_timeout_seconds:parseInt(document.getElementById('cfgLlmTimeout').value),
    max_positions:parseInt(document.getElementById('cfgMaxPosLlm').value)
  };
}

function scheduleAutoTuneSave(){
  if(!llmEnabled())return;
  clearTimeout(autoSaveTimer);
  autoSaveTimer=setTimeout(()=>saveRiskLlmSettings(true),900);
}

async function saveRiskLlmSettings(silent=false){
  try{
    const result=await P('/api/settings',riskLlmPayload());
    markSaved(riskLlmFieldIds);
    await refresh();
    if(!silent)toast('LLM autopilot saved')
    return result;
  }catch(e){showError('LLM autopilot save failed',e)}
}

async function saveCoreSettings(){
  try{
    const useLlm=document.getElementById('cfgLlm').value==='true';
    await P('/api/settings',{
      trading_mode:document.getElementById('cfgMode').value,
      dry_run:document.getElementById('cfgDry').value==='true',
      use_llm:useLlm,
      auto_tune:useLlm,
      llm_fail_closed:document.getElementById('cfgFail').value==='true'});
    markSaved(coreFieldIds);
    await refresh();toast('Core settings saved')
  }catch(e){showError('Core save failed',e)}
}

async function saveStrategy(){
  if(llmEnabled())return saveRiskLlmSettings(false);
  try{
    const p={
      symbol:document.getElementById('symInput').value.trim().toUpperCase(),
      timeframe:document.getElementById('cfgTf').value,
      bars:parseInt(document.getElementById('cfgBars').value),
      ema_fast:parseInt(document.getElementById('cfgEmaF').value),
      ema_slow:parseInt(document.getElementById('cfgEmaS').value),
      ema_trend:parseInt(document.getElementById('cfgEmaT').value),
      atr_period:parseInt(document.getElementById('cfgAtr').value),
      rsi_period:parseInt(document.getElementById('cfgRsi').value),
      sl_atr_multiplier:parseFloat(document.getElementById('cfgSlAtr').value),
      tp_atr_multiplier:parseFloat(document.getElementById('cfgTpAtr').value),
      min_stop_points:parseInt(document.getElementById('cfgMinStop').value),
      min_signal_confidence:parseFloat(document.getElementById('cfgMinConf').value),
      llm_min_score:parseFloat(document.getElementById('cfgLlmScore').value),
      risk_percent:parseFloat(document.getElementById('cfgRiskPct').value),
      daily_loss_limit_percent:parseFloat(document.getElementById('cfgDailyLoss').value),
      max_trades_per_day:parseInt(document.getElementById('cfgMaxTrades').value),
      llm_timeout_seconds:parseInt(document.getElementById('cfgLlmTimeout').value)};
    await P('/api/settings',p);markSaved(strategyFieldIds);await refresh();toast('Strategy saved')
  }catch(e){showError('Strategy save failed',e)}
}

async function saveEaSettings(){
  try{
    await P('/api/settings',{
      dry_run:document.getElementById('cfgDry').value==='true',
      use_risk_sizing:document.getElementById('cfgRiskSizing').value==='true',
      lots:parseFloat(document.getElementById('cfgLots').value),
      max_positions:parseInt(document.getElementById('cfgMaxPos').value),
      max_spread_points:parseInt(document.getElementById('cfgSpreadPts').value),
      max_spread_percent:parseFloat(document.getElementById('cfgSpreadPct').value),
      cooldown_seconds:parseInt(document.getElementById('cfgCooldown').value),
      magic_number:parseInt(document.getElementById('cfgMagic').value),
      deviation_points:parseInt(document.getElementById('cfgDev').value),
      one_trade_per_bar:document.getElementById('cfgOneBar').value==='true'});
    markSaved(eaFieldIds);
    await refresh();toast('EA settings saved')
  }catch(e){showError('EA save failed',e)}
}

function updateDashboardMode(s,runtime){
  const autopilot=!!s.use_llm;
  document.getElementById('aiAutopilotPanel').classList.toggle('hidden',!autopilot);
  document.getElementById('assetPanel').classList.toggle('advanced-hidden',autopilot);
  document.getElementById('corePanel').classList.toggle('advanced-hidden',autopilot);
  document.getElementById('strategyPanel').classList.toggle('advanced-hidden',autopilot);
  document.getElementById('eaPanel').classList.toggle('advanced-hidden',autopilot);
  document.getElementById('autoProfile').textContent=s.auto_tune_profile||'manual';
  document.getElementById('autoTuneSummary').textContent=s.auto_tune_summary||(
    autopilot?'LLM autopilot is tuning Strategy and MT5 EA parameters from your risk controls.':'Enable LLM to activate automatic expert tuning.'
  );
  document.getElementById('autoTuneCards').innerHTML=[
    ['Strategy',`${s.timeframe||'?'} · EMA ${s.ema_fast||'?'} / ${s.ema_slow||'?'} / ${s.ema_trend||'?'}`],
    ['Signal Gate',`Confidence ≥ ${Number(s.min_signal_confidence||0).toFixed(2)} · LLM ≥ ${Number(s.llm_min_score||0).toFixed(2)}`],
    ['Stops',`SL x${s.sl_atr_multiplier||'?'} · TP x${s.tp_atr_multiplier||'?'} · min ${s.min_stop_points||'?'} pts`],
    ['Execution',`${runtime.use_risk_sizing?'risk % sizing':'fixed lots'} · cap ${runtime.lots||'?'} lot · max pos ${runtime.max_positions||'?'}`],
    ['Protection',`spread ≤ ${runtime.max_spread_percent??'?'}% · cooldown ${runtime.cooldown_seconds||'?'}s`],
    ['Daily Risk',`risk ${s.risk_percent||'?'}% · loss ${s.daily_loss_limit_percent||'?'}% · trades ${s.max_trades_per_day||'?'}`]
  ].map(([k,v])=>`<div class="metric"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');
}

async function refresh(){
  try{
    if(tokenInput)tokenInput.value=localStorage.getItem('signalPassword')||'';
    const status=await G('/api/status');
    const runtime=await G('/api/runtime-settings');
    const eventsR=await G('/api/events?limit=80');
    const s=status.settings||{},sum=status.summary||{},risk=status.risk_state||{},events=eventsR.events||[];

    // Populate forms
    setValue('symInput',s.symbol||'SOLUSD');
    setValue('cfgMode',s.trading_mode||'live');
    setValue('cfgDry',B(s.dry_run));
    setValue('cfgLlm',B(s.use_llm));
    setValue('cfgFail',B(s.llm_fail_closed));
    setValue('cfgTf',s.timeframe||'M1');
    setValue('cfgBars',s.bars||300);
    setValue('cfgEmaF',s.ema_fast||8);
    setValue('cfgEmaS',s.ema_slow||21);
    setValue('cfgEmaT',s.ema_trend||55);
    setValue('cfgAtr',s.atr_period||14);
    setValue('cfgRsi',s.rsi_period||14);
    setValue('cfgSlAtr',s.sl_atr_multiplier||1.3);
    setValue('cfgTpAtr',s.tp_atr_multiplier||1.8);
    setValue('cfgMinStop',s.min_stop_points||80);
    setValue('cfgMinConf',s.min_signal_confidence||0.62);
    setValue('cfgLlmScore',s.llm_min_score||0.65);
    setValue('cfgLlmTimeout',s.llm_timeout_seconds||8);
    setValue('cfgRiskPct',s.risk_percent||0.25);
    setValue('cfgDailyLoss',s.daily_loss_limit_percent||2.0);
    setValue('cfgMaxTrades',s.max_trades_per_day||8);
    setValue('cfgSpreadPct',runtime.max_spread_percent!=null?runtime.max_spread_percent:0);
    setValue('cfgSpreadPts',runtime.max_spread_points||0);
    setValue('cfgRiskSizing',B(runtime.use_risk_sizing));
    setValue('cfgLots',runtime.lots||0.01);
    setValue('cfgMaxPos',runtime.max_positions||1);
    setValue('cfgMaxPosLlm',runtime.max_positions||2);
    setValue('cfgCooldown',runtime.cooldown_seconds||180);
    setValue('cfgMagic',runtime.magic_number||260618);
    setValue('cfgDev',runtime.deviation_points||20);
    setValue('cfgOneBar',B(runtime.one_trade_per_bar));
    updateDashboardMode(s,runtime);

    // Top bar
    document.getElementById('topBar').innerHTML=`
      <span class="dot dot-${status.ok?'online':'offline'}"></span>
      <span>${status.ok?'ONLINE':'DOWN'}</span>
      <span style="opacity:0.5">|</span>
      <span>${s.trading_mode||'?'}</span>
      <span style="opacity:0.5">|</span>
      <span>${s.symbol||'?'}·${s.timeframe||'?'}</span>
      <span style="opacity:0.5">|</span>
      <span>LLM ${s.use_llm?'<span style="color:var(--accent)">on</span>':'off'}</span>
      <span style="opacity:0.5">|</span>
      <span>🕐 ${(s.server_time||'').slice(11,19)}</span>`;

    // EA badge
    const badge=document.getElementById('eaBadge');
    if(runtime.dry_run===false){badge.className='badge badge-live';badge.textContent='LIVE 🔴'}
    else{badge.className='badge badge-dry';badge.textContent='DRY RUN'}

    // Stat cards
    document.getElementById('statCards').innerHTML=[
      ['⚡ Signals',sum.events_count||0,''],
      ['🟢 BUY',(sum.actions||{}).BUY||0,'action-BUY'],
      ['🔴 SELL',(sum.actions||{}).SELL||0,'action-SELL'],
      ['🟡 HOLD',(sum.actions||{}).HOLD||0,'action-HOLD'],
      ['📈 Trades',risk.trades_count??0,''],
      ['✅ Wins',sum.trade_success||0,''],
      ['❌ Losses',sum.trade_failed||0,''],
      ['💰 Bal','$'+(risk.balance!=null?Number(risk.balance).toFixed(2):'-'),''],
      ['📊 PnL',risk.daily_pnl_pct!=null?Number(risk.daily_pnl_pct).toFixed(2)+'%':'-',''],
      ['🤖 LLM',s.use_llm?'Active':'Off',s.use_llm?'':'muted']
    ].map(([l,v,c])=>`<div class="card"><div class="label">${l}</div><div class="value ${c}">${v}</div></div>`).join('');

    // Events
    document.getElementById('eventsTable').innerHTML=events.length?events.slice(0,60).map(r=>`<tr>
      <td>${(r.ts||'').slice(11,19)}</td><td>${r.type||''}</td><td>${r.symbol||''}</td>
      <td><span class="action-${r.action||''}">${r.action||''}</span></td>
      <td>${r.confidence!=null?r.confidence.toFixed(3):''}</td>
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${r.reason||''}">${r.reason||''}</td>
    </tr>`).join(''):'<tr><td colspan="6" class="muted">No events yet</td></tr>';
  }catch(e){
    console.error('refresh failed',e);
    document.getElementById('topBar').innerHTML=`<span class="dot dot-offline"></span><span class="error">API error: ${String(e.message||e).slice(0,120)}</span>`;
  }
}

onSymbolChange();
refresh();
setInterval(refresh,4000);
</script>
</body>
</html>"""


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Trend Scalper HTTP signal service")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args(argv)

    settings = load_settings(args.env_file)
    configure_logging(settings.log_level)
    errors = validate_settings(settings)
    if errors:
        for error in errors:
            logger.warning("Config: %s", error)
        if settings.use_llm:
            return 2

    runtime_settings = RuntimeSettingsStore(settings)
    event_store = EventStore(settings, runtime_settings)

    # Start the async LLM regime brain if configured
    engine = SignalEngine(settings, event_store, runtime_settings)
    if settings.use_llm:
        engine.start_regime_brain()

    global context
    context = SignalContext(
        engine,
        settings.signal_password,
        event_store,
        runtime_settings,
    )

    server = ThreadingHTTPServer((settings.signal_host, settings.signal_port), SignalHandler)
    logger.info("Signal service listening on http://%s:%s", settings.signal_host, settings.signal_port)
    logger.info("LLM regime brain: %s", "running (async, every 5-15min)" if settings.use_llm else "disabled")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping signal service")
    finally:
        engine.stop_regime_brain()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
