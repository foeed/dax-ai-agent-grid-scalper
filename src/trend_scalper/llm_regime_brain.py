from __future__ import annotations

import json
import logging
import threading
import time as time_module
import urllib.error
import urllib.request
from typing import Any

from .config import Settings
from .market_analyzer import MarketAnalyzer
from .models import Rate, RegimeConfig

logger = logging.getLogger(__name__)


class LLMRegimeBrain:
    """Asynchronous background thread that calls DeepSeek once every 5-15 minutes
    to determine the current market regime.

    This is completely decoupled from the fast-path signal evaluation loop.
    It updates an in-memory RegimeConfig that the strategy engine reads instantly.
    """

    def __init__(self, settings: Settings, interval_seconds: int = 300) -> None:
        self.settings = settings
        self.interval = max(120, interval_seconds)  # minimum 2 minutes
        self.analyzer = MarketAnalyzer()
        self._current_regime = RegimeConfig()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._last_update_time = 0.0
        self._call_count = 0
        self._error_count = 0
        self._circuit_open = False
        self._circuit_open_since = 0.0
        self._MAX_CONSECUTIVE_ERRORS = 5
        self._CIRCUIT_RESET_SECONDS = 600

    @property
    def current_regime(self) -> RegimeConfig:
        """Thread-safe read of the current regime."""
        with self._lock:
            return RegimeConfig(
                trading_bias=self._current_regime.trading_bias,
                strategy_mode=self._current_regime.strategy_mode,
                max_risk_percent=self._current_regime.max_risk_percent,
                max_positions=self._current_regime.max_positions,
                min_signal_confidence=self._current_regime.min_signal_confidence,
                sl_atr_multiplier=self._current_regime.sl_atr_multiplier,
                tp_atr_multiplier=self._current_regime.tp_atr_multiplier,
                cooldown_seconds=self._current_regime.cooldown_seconds,
                ema_fast=self._current_regime.ema_fast,
                ema_slow=self._current_regime.ema_slow,
                ema_trend=self._current_regime.ema_trend,
                atr_period=self._current_regime.atr_period,
                rsi_period=self._current_regime.rsi_period,
                min_stop_points=self._current_regime.min_stop_points,
                updated_at=self._current_regime.updated_at,
                llm_reasoning=self._current_regime.llm_reasoning,
                llm_confidence=self._current_regime.llm_confidence,
                source=self._current_regime.source,
            )

    def status(self) -> dict[str, Any]:
        """Return status for dashboard display."""
        with self._lock:
            reg = self._current_regime
        return {
            "running": self._thread is not None and self._thread.is_alive(),
            "interval_seconds": self.interval,
            "calls": self._call_count,
            "errors": self._error_count,
            "last_update_ago_seconds": round(time_module.time() - self._last_update_time, 1) if self._last_update_time else None,
            "current_regime": reg.as_dict(),
        }

    def start(self) -> None:
        """Start the background regime analysis thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("LLMRegimeBrain already running")
            return

        if not self.settings.deepseek_api_key:
            logger.warning("LLMRegimeBrain: no DeepSeek API key, regime will stay at defaults")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="llm-regime-brain")
        self._thread.start()
        logger.info("LLMRegimeBrain started: interval=%ds", self.interval)

    def stop(self) -> None:
        """Signal the background thread to stop."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("LLMRegimeBrain stopped")

    def update_regime_now(self, rates_by_timeframe: dict[str, list[Rate]]) -> RegimeConfig:
        """Force an immediate regime update (used for initialization or manual trigger).

        Args:
            rates_by_timeframe: M15 and H1 OHLC data

        Returns:
            The newly computed RegimeConfig
        """
        new_regime = self._analyze_and_classify(rates_by_timeframe)
        with self._lock:
            self._current_regime = new_regime
            self._last_update_time = time_module.time()
            self._call_count += 1
        return new_regime

    def _run_loop(self) -> None:
        """Main background loop: sleep, analyze (if possible), update.
        
        The brain can't fetch rates on its own — the signal service injects them
        via update_regime_now(). This loop simply provides periodic heartbeat logging
        and health monitoring. The actual analysis is event-driven.
        """
        logger.info("LLMRegimeBrain loop started (interval=%ds)", self.interval)
        time_module.sleep(30)

        while not self._stop_event.is_set():
            try:
                age = time_module.time() - self._last_update_time if self._last_update_time else None
                circ = "OPEN" if self._circuit_open else "closed"
                logger.debug(
                    "LLMRegimeBrain heartbeat: calls=%d errors=%d last_update=%ss circuit=%s",
                    self._call_count, self._error_count,
                    f"{age:.0f}" if age else "never", circ,
                )
                if self._circuit_open and time_module.time() - self._circuit_open_since > self._CIRCUIT_RESET_SECONDS:
                    self._circuit_open = False
                    self._error_count = 0
                    logger.info("LLMRegimeBrain circuit breaker reset")
            except Exception as exc:
                logger.error("LLMRegimeBrain loop error: %s", exc)
                self._error_count += 1

            self._stop_event.wait(self.interval)

        logger.info("LLMRegimeBrain loop exited")

    def _analyze_and_classify(self, rates_by_timeframe: dict[str, list[Rate]]) -> RegimeConfig:
        """Core logic: analyze M15/H1 market structure, call DeepSeek once, produce RegimeConfig."""
        if self._circuit_open:
            age = time_module.time() - self._circuit_open_since
            if age > self._CIRCUIT_RESET_SECONDS:
                self._circuit_open = False
                self._error_count = 0
            else:
                logger.debug("LLMRegimeBrain circuit open (%.0fs remaining)", self._CIRCUIT_RESET_SECONDS - age)
                with self._lock:
                    return RegimeConfig(
                        trading_bias=self._current_regime.trading_bias,
                        strategy_mode=self._current_regime.strategy_mode,
                        max_risk_percent=self._current_regime.max_risk_percent,
                        max_positions=self._current_regime.max_positions,
                        min_signal_confidence=self._current_regime.min_signal_confidence,
                        sl_atr_multiplier=self._current_regime.sl_atr_multiplier,
                        tp_atr_multiplier=self._current_regime.tp_atr_multiplier,
                        cooldown_seconds=self._current_regime.cooldown_seconds,
                        ema_fast=self._current_regime.ema_fast,
                        ema_slow=self._current_regime.ema_slow,
                        ema_trend=self._current_regime.ema_trend,
                        atr_period=self._current_regime.atr_period,
                        rsi_period=self._current_regime.rsi_period,
                        min_stop_points=self._current_regime.min_stop_points,
                        updated_at=time_module.time(),
                        llm_reasoning=self._current_regime.llm_reasoning,
                        llm_confidence=self._current_regime.llm_confidence,
                        source=self._current_regime.source,
                    )

        macro_rates = {}
        for tf in ("M15", "H1", "H4"):
            if tf in rates_by_timeframe and rates_by_timeframe[tf]:
                macro_rates[tf] = rates_by_timeframe[tf]

        if not macro_rates:
            logger.warning("LLMRegimeBrain: no M15/H1 rates available, keeping default regime")
            return RegimeConfig()

        try:
            market_report = self.analyzer.compact_report(macro_rates)
        except Exception as exc:
            logger.warning("MarketAnalyzer failed for macro timeframes: %s", exc)
            return RegimeConfig()

        prompt_payload = {
            "market_structure": market_report,
            "instruction": (
                "Classify the current gold market regime based on M15/H1 data. "
                "Output a regime classification and trading posture adjustment."
            ),
        }

        max_retries = 2
        for attempt in range(1, max_retries + 1):
            try:
                response = self._chat_completion(
                    {
                        "model": self.settings.deepseek_model,
                        "messages": [
                            {"role": "system", "content": self._system_prompt()},
                            {"role": "user", "content": json.dumps(prompt_payload, separators=(",", ":"))},
                        ],
                        "response_format": {"type": "json_object"},
                        "stream": False,
                    }
                )
                break
            except Exception as exc:
                if attempt < max_retries and _is_retryable(str(exc)):
                    delay = 2.0 * attempt
                    logger.warning("DeepSeek regime call attempt %d failed: %s. Retrying in %.1fs...", attempt, exc, delay)
                    time_module.sleep(delay)
                    continue
                with self._lock:
                    self._error_count += 1
                    if self._error_count >= self._MAX_CONSECUTIVE_ERRORS:
                        self._circuit_open = True
                        self._circuit_open_since = time_module.time()
                        logger.warning("LLMRegimeBrain circuit breaker OPEN (consecutive errors: %d)", self._error_count)
                logger.warning("DeepSeek regime call failed: %s, keeping last regime", exc)
                with self._lock:
                    return RegimeConfig(
                        trading_bias=self._current_regime.trading_bias,
                        strategy_mode=self._current_regime.strategy_mode,
                        max_risk_percent=self._current_regime.max_risk_percent,
                        max_positions=self._current_regime.max_positions,
                        min_signal_confidence=self._current_regime.min_signal_confidence,
                        sl_atr_multiplier=self._current_regime.sl_atr_multiplier,
                        tp_atr_multiplier=self._current_regime.tp_atr_multiplier,
                        cooldown_seconds=self._current_regime.cooldown_seconds,
                        ema_fast=self._current_regime.ema_fast,
                        ema_slow=self._current_regime.ema_slow,
                        ema_trend=self._current_regime.ema_trend,
                        atr_period=self._current_regime.atr_period,
                        rsi_period=self._current_regime.rsi_period,
                        min_stop_points=self._current_regime.min_stop_points,
                        updated_at=time_module.time(),
                        llm_reasoning=self._current_regime.llm_reasoning,
                        llm_confidence=self._current_regime.llm_confidence,
                        source=self._current_regime.source,
                    )

        content = response["choices"][0]["message"].get("content") or "{}"
        try:
            raw = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("DeepSeek regime response not valid JSON: %s", content[:200])
            with self._lock:
                return RegimeConfig(
                    trading_bias=self._current_regime.trading_bias,
                    strategy_mode=self._current_regime.strategy_mode,
                    max_risk_percent=self._current_regime.max_risk_percent,
                    max_positions=self._current_regime.max_positions,
                    min_signal_confidence=self._current_regime.min_signal_confidence,
                    sl_atr_multiplier=self._current_regime.sl_atr_multiplier,
                    tp_atr_multiplier=self._current_regime.tp_atr_multiplier,
                    cooldown_seconds=self._current_regime.cooldown_seconds,
                    ema_fast=self._current_regime.ema_fast,
                    ema_slow=self._current_regime.ema_slow,
                    ema_trend=self._current_regime.ema_trend,
                    atr_period=self._current_regime.atr_period,
                    rsi_period=self._current_regime.rsi_period,
                    min_stop_points=self._current_regime.min_stop_points,
                    updated_at=time_module.time(),
                    llm_reasoning=self._current_regime.llm_reasoning,
                    llm_confidence=self._current_regime.llm_confidence,
                    source=self._current_regime.source,
                )

        # Extract regime classification
        regime_type = str(raw.get("regime_type", "neutral")).lower()
        trading_bias = str(raw.get("trading_bias", "neutral")).lower()
        reasoning = str(raw.get("reasoning", "No reasoning provided"))[:500]
        confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.5))))
        volatility_level = str(raw.get("volatility_level", "medium")).lower()

        # Map regime_type to strategy mode and parameters
        strategy_mode, params = self._regime_to_params(regime_type, trading_bias, volatility_level)

        # Experimental: if LLM provides explicit parameter overrides, use them
        llm_params = raw.get("parameters", {})
        if isinstance(llm_params, dict):
            for key in params:
                if key in llm_params:
                    params[key] = self._clamp_param(key, llm_params[key])

        logger.info(
            "LLM regime classified: type=%s bias=%s mode=%s conf=%.2f vol=%s",
            regime_type, trading_bias, strategy_mode, confidence, volatility_level,
        )

        return RegimeConfig(
            trading_bias=trading_bias,
            strategy_mode=strategy_mode,
            max_risk_percent=params["max_risk_percent"],
            max_positions=params["max_positions"],
            min_signal_confidence=params["min_signal_confidence"],
            sl_atr_multiplier=params["sl_atr_multiplier"],
            tp_atr_multiplier=params["tp_atr_multiplier"],
            cooldown_seconds=params["cooldown_seconds"],
            ema_fast=params["ema_fast"],
            ema_slow=params["ema_slow"],
            ema_trend=params["ema_trend"],
            atr_period=14,
            rsi_period=14,
            min_stop_points=params["min_stop_points"],
            updated_at=time_module.time(),
            llm_reasoning=reasoning,
            llm_confidence=confidence,
            source="llm_regime",
        )

    def _system_prompt(self) -> str:
        """System prompt for regime classification (one DeepSeek call, not two)."""
        return """You are a macro market regime classifier for Gold (XAUUSD) scalping.

TASK: Analyze the provided M15/H1 market structure and classify the current regime.

CLASSIFY INTO ONE OF THESE REGIME TYPES:
- "strong_trend": Clear directional move, EMAs aligned across timeframes, RSI trending
- "volatile_break": High ATR, recent breakout, wide ranges, momentum
- "choppy_range": Price oscillating in a range, mixed candle patterns, low conviction
- "quiet_accumulation": Very low ATR, tight candles, likely pre-breakout
- "mean_reverting": RSI extremes, price stretched from EMAs, likely snapback

TRADING BIAS:
- "bullish": Favor longs only
- "bearish": Favor shorts only
- "neutral": Allow both directions

VOLATILITY LEVEL:
- "low", "medium", "high", "extreme"

OPTIONAL PARAMETER OVERRIDES (only if you're highly confident):
{
  "parameters": {
    "min_signal_confidence": 0.55-0.75,
    "sl_atr_multiplier": 1.0-2.5,
    "tp_atr_multiplier": 0.5-1.5,
    "cooldown_seconds": 60-300,
    "max_risk_percent": 0.1-0.5,
    "max_positions": 1-3
  }
}

GUIDELINES:
1. Strong trend: lower confidence threshold, allow more trades, use trend-following stops
2. Choppy range: raise confidence threshold, fewer trades, wider stops
3. High volatility: reduce risk %, wider stops
4. Quiet accumulation: tight stops, fast TP, expect breakout
5. Only provide parameter overrides if market structure clearly justifies it

Return ONLY valid JSON:
{
  "regime_type": "one of the types above",
  "trading_bias": "bullish|bearish|neutral",
  "volatility_level": "low|medium|high|extreme",
  "confidence": 0.0 to 1.0,
  "reasoning": "brief explanation",
  "parameters": {} // optional, only if confident
}
"""

    def _regime_to_params(
        self,
        regime_type: str,
        trading_bias: str,
        volatility_level: str,
    ) -> tuple[str, dict[str, Any]]:
        """Convert LLM regime classification to concrete strategy parameters.

        Profitable scalping principles:
        - SL: 1.0x ATR (tight, since entries are at pullback extremes)
        - TP: 2.5-3.5x ATR (room to run after R:R gate verified)
        - Max 1 concurrent position (correlated exposure = same trade)
        - Higher confidence in choppy/volatile (don't trade noise)
        - Lower confidence in strong trend (let the trend work)
        """
        params: dict[str, Any] = {
            "max_risk_percent": 0.25,
            "max_positions": 1,
            "min_signal_confidence": 0.55,
            "sl_atr_multiplier": 1.0,
            "tp_atr_multiplier": 2.8,
            "cooldown_seconds": 90,
            "ema_fast": 8,
            "ema_slow": 21,
            "ema_trend": 55,
            "min_stop_points": 80,
            "min_risk_reward": 1.5,
            "trailing_stop_atr_multiplier": 0.6,
            "time_stop_bars": 15,
        }

        if regime_type == "strong_trend":
            params["strategy_mode"] = "trend_following"
            params["min_signal_confidence"] = 0.52
            params["cooldown_seconds"] = 60
            params["tp_atr_multiplier"] = 3.5
            params["sl_atr_multiplier"] = 1.0
            params["max_risk_percent"] = 0.30
            params["trailing_stop_atr_multiplier"] = 0.7
            params["time_stop_bars"] = 20

        elif regime_type == "volatile_break":
            params["strategy_mode"] = "trend_following"
            params["min_signal_confidence"] = 0.62
            params["cooldown_seconds"] = 120
            params["tp_atr_multiplier"] = 3.0
            params["sl_atr_multiplier"] = 1.2
            params["max_risk_percent"] = 0.20
            params["min_stop_points"] = 90
            params["min_risk_reward"] = 1.8
            params["trailing_stop_atr_multiplier"] = 0.8

        elif regime_type == "choppy_range":
            params["strategy_mode"] = "cautious"
            params["min_signal_confidence"] = 0.65
            params["cooldown_seconds"] = 180
            params["tp_atr_multiplier"] = 2.2
            params["sl_atr_multiplier"] = 1.1
            params["max_risk_percent"] = 0.15
            params["min_risk_reward"] = 2.0
            params["trailing_stop_atr_multiplier"] = 0.5
            params["time_stop_bars"] = 10

        elif regime_type == "quiet_accumulation":
            params["strategy_mode"] = "trend_following"
            params["min_signal_confidence"] = 0.58
            params["cooldown_seconds"] = 60
            params["tp_atr_multiplier"] = 3.0
            params["sl_atr_multiplier"] = 1.0
            params["max_risk_percent"] = 0.20
            params["min_stop_points"] = 80
            params["trailing_stop_atr_multiplier"] = 0.6

        elif regime_type == "mean_reverting":
            params["strategy_mode"] = "mean_reversion"
            params["min_signal_confidence"] = 0.60
            params["cooldown_seconds"] = 120
            params["tp_atr_multiplier"] = 1.8
            params["sl_atr_multiplier"] = 1.0
            params["max_risk_percent"] = 0.20
            params["trailing_stop_atr_multiplier"] = 0.4
            params["time_stop_bars"] = 8

        else:
            params["strategy_mode"] = "trend_following"

        vol_adjustments = {
            "low": {"sl_atr_multiplier": -0.1, "cooldown_seconds": -20},
            "medium": {},
            "high": {"sl_atr_multiplier": 0.2, "cooldown_seconds": 30, "max_risk_percent": -0.05, "min_signal_confidence": 0.03},
            "extreme": {"sl_atr_multiplier": 0.3, "cooldown_seconds": 60, "max_risk_percent": -0.10, "min_signal_confidence": 0.05, "min_risk_reward": 0.2},
        }

        adj = vol_adjustments.get(volatility_level, {})
        for key, delta in adj.items():
            if key in params:
                if isinstance(params[key], int):
                    params[key] = max(30, params[key] + int(delta))
                else:
                    params[key] = round(max(0.01, params[key] + delta), 2)

        params["sl_atr_multiplier"] = round(self._clamp(params["sl_atr_multiplier"], 0.8, 1.8), 2)
        params["tp_atr_multiplier"] = round(self._clamp(params["tp_atr_multiplier"], 1.5, 4.0), 2)
        params["min_signal_confidence"] = round(self._clamp(params["min_signal_confidence"], 0.45, 0.70), 2)
        params["cooldown_seconds"] = int(self._clamp(params["cooldown_seconds"], 60, 300))
        params["max_risk_percent"] = round(self._clamp(params["max_risk_percent"], 0.10, 0.35), 2)
        params["min_stop_points"] = int(self._clamp(params["min_stop_points"], 60, 120))
        params["ema_fast"] = int(self._clamp(params["ema_fast"], 5, 12))
        params["ema_slow"] = int(self._clamp(params["ema_slow"], 15, 30))
        params["ema_trend"] = int(self._clamp(params["ema_trend"], 40, 80))
        params["max_positions"] = 1
        params["min_risk_reward"] = round(self._clamp(float(params.get("min_risk_reward", 1.5)), 1.2, 2.5), 1)
        params["trailing_stop_atr_multiplier"] = round(self._clamp(params["trailing_stop_atr_multiplier"], 0.3, 1.0), 2)
        params["time_stop_bars"] = int(self._clamp(float(params.get("time_stop_bars", 15)), 6, 25))

        strategy_mode = params.pop("strategy_mode", "trend_following")
        return strategy_mode, params

    def _clamp(self, value: float, min_val: float, max_val: float) -> float:
        return max(min_val, min(max_val, value))

    def _clamp_param(self, key: str, value: Any) -> Any:
        """Clamp a single parameter from LLM output to safe range."""
        ranges: dict[str, tuple[float, float]] = {
            "max_risk_percent": (0.10, 0.50),
            "min_signal_confidence": (0.50, 0.80),
            "sl_atr_multiplier": (1.0, 2.5),
            "tp_atr_multiplier": (0.5, 1.5),
            "cooldown_seconds": (60, 300),
            "min_stop_points": (30, 100),
            "max_positions": (1, 3),
        }
        if key in ranges:
            lo, hi = ranges[key]
            try:
                val = float(value)
                clamped = max(lo, min(hi, val))
                if key == "max_positions":
                    return int(clamped)
                return clamped
            except (ValueError, TypeError):
                return lo
        return value

    def _chat_completion(self, payload: dict) -> dict:
        """Single DeepSeek call for regime classification."""
        url = self.settings.deepseek_base_url.rstrip("/") + "/chat/completions"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.settings.deepseek_api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            timeout_seconds = self.settings.llm_timeout_seconds + 5  # slightly more lenient
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"DeepSeek HTTP {exc.code}: {message}") from exc


def _is_retryable(error_msg: str) -> bool:
    retry_keywords = {"429", "503", "502", "timeout", "timed out", "connection", "reset", "broken pipe"}
    msg_lower = error_msg.lower()
    return any(kw in msg_lower for kw in retry_keywords)