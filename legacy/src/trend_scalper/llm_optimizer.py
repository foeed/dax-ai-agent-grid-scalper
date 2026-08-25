from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from contextvars import ContextVar
from typing import Any

from .config import Settings
from .market_analyzer import MarketAnalyzer
from .models import Rate

logger = logging.getLogger(__name__)
_LLM_TIMEOUT_SECONDS: ContextVar[float | None] = ContextVar("llm_timeout_seconds", default=None)


class OptimizationResult:
    """Container for LLM-optimized trading parameters."""

    def __init__(
        self,
        success: bool,
        parameters: dict[str, Any],
        reasoning: str = "",
        trading_bias: str = "neutral",
        confidence: float = 0.5,
    ) -> None:
        self.success = success
        self.parameters = parameters
        self.reasoning = reasoning
        self.trading_bias = trading_bias  # bullish, bearish, or neutral
        self.confidence = confidence  # 0-1 how confident the LLM is in this tuning

    def __repr__(self) -> str:
        return f"OptimizationResult(success={self.success}, bias={self.trading_bias}, conf={self.confidence:.2f})"


class LLMParameterOptimizer:
    """Uses DeepSeek LLM to dynamically optimize trading parameters based on market conditions.

    This class analyzes multi-timeframe market structure and recommends parameter
    adjustments for aggressive scalping on M1 timeframe, including:
    - SL/TP ATR multipliers
    - Minimum signal confidence threshold
    - Cooldown seconds
    - EMA periods
    - Trading bias (bullish/bearish/neutral)
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.analyzer = MarketAnalyzer()

    def optimize(
        self,
        rates_by_timeframe: dict[str, list[Rate]],
        runtime: dict[str, Any] | None = None,
    ) -> OptimizationResult:
        """Run LLM-based parameter optimization.

        Args:
            rates_by_timeframe: Multi-timeframe OHLC data (M1, M5, M15)
            runtime: Current runtime settings (optional)

        Returns:
            OptimizationResult with recommended parameters
        """
        if not self.settings.deepseek_api_key:
            logger.warning("LLM optimizer disabled: no DeepSeek API key")
            return OptimizationResult(False, {}, "No API key configured")

        try:
            # Generate market analysis report
            market_report = self.analyzer.compact_report(rates_by_timeframe)

            # Build optimization prompt
            effective = runtime or {}
            prompt_payload = self._build_prompt_payload(market_report, effective)

            # Call DeepSeek
            timeout_seconds = float(effective.get("llm_timeout_seconds", self.settings.llm_timeout_seconds))
            timeout_token = _LLM_TIMEOUT_SECONDS.set(timeout_seconds)
            try:
                response = self._chat_completion(
                    {
                        "model": self.settings.deepseek_model,
                        "messages": [
                            {
                                "role": "system",
                                "content": self._system_prompt(),
                            },
                            {
                                "role": "user",
                                "content": json.dumps(prompt_payload, separators=(",", ":")),
                            },
                        ],
                        "response_format": {"type": "json_object"},
                        "stream": False,
                    }
                )
            finally:
                _LLM_TIMEOUT_SECONDS.reset(timeout_token)

            # Parse LLM response
            content = response["choices"][0]["message"].get("content") or "{}"
            raw = json.loads(content)

            # Extract and validate parameters
            parameters = self._extract_parameters(raw, effective)
            reasoning = str(raw.get("reasoning", "No reasoning provided"))[:500]
            trading_bias = str(raw.get("trading_bias", "neutral")).lower()
            if trading_bias not in {"bullish", "bearish", "neutral"}:
                trading_bias = "neutral"
            confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.5))))

            logger.info(
                "LLM optimizer: bias=%s conf=%.2f sl=%.2f tp=%.2f min_conf=%.2f cool=%ds",
                trading_bias,
                confidence,
                parameters.get("sl_atr_multiplier", 0),
                parameters.get("tp_atr_multiplier", 0),
                parameters.get("min_signal_confidence", 0),
                parameters.get("cooldown_seconds", 0),
            )

            return OptimizationResult(
                success=True,
                parameters=parameters,
                reasoning=reasoning,
                trading_bias=trading_bias,
                confidence=confidence,
            )

        except Exception as exc:
            logger.warning("LLM parameter optimization failed: %s", exc)
            return OptimizationResult(False, {}, f"Optimization error: {exc}")

    def _system_prompt(self) -> str:
        """System prompt for DeepSeek parameter optimization."""
        return """You are an expert trading system optimizer for aggressive scalping on Gold (XAUUSD) M1 timeframe.

TASK: Analyze the provided multi-timeframe market structure (M1, M5, M15) and recommend optimized parameters for fast scalping.

CONTEXT:
- Trading on M1 (1-minute bars) for fast entries/exits
- M5 and M15 provide trend context and confirmation
- Target: 5-15 pip quick profits with tight stops
- Strategy: EMA crossover + RSI + ATR-based stops
- Volatility-adaptive: tighter parameters in low vol, wider in high vol

OUTPUT JSON SCHEMA:
{
  "trading_bias": "bullish" | "bearish" | "neutral",
  "confidence": 0.0 to 1.0,
  "reasoning": "Brief explanation of market conditions and recommendations",
  "parameters": {
    "sl_atr_multiplier": 1.0 to 2.5,
    "tp_atr_multiplier": 0.5 to 1.5,
    "min_signal_confidence": 0.50 to 0.80,
    "cooldown_seconds": 60 to 240,
    "ema_fast": 5 to 12,
    "ema_slow": 15 to 30,
    "ema_trend": 40 to 80,
    "max_positions": 1 to 3
  }
}

GUIDELINES:
1. **Scalping TP**: Use TP multipliers 0.5x-1.0x ATR for fast exits (NOT 2x+ ATR)
2. **Safe SL**: Use SL multipliers 1.2x-2.0x ATR to avoid premature stops
3. **Volatility**: In high volatility, widen stops/targets; in low volatility, tighten them
4. **Trend alignment**: If M1/M5/M15 align strongly, lower confidence threshold and cooldown for more trades
5. **Ranging markets**: Raise confidence threshold, increase cooldown, use neutral bias
6. **Divergent timeframes**: Use conservative parameters and neutral bias
7. **RSI extremes**: Favor mean reversion (counter-trend) in overbought/oversold zones
8. **Max Positions**: Allow up to 3 in strong aligned trends, reduce to 1 in choppy/volatile markets, default to 1 when uncertain

Return ONLY valid JSON. No explanations outside the JSON structure.
"""

    def _build_prompt_payload(self, market_report: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
        """Build the user prompt payload for the LLM."""
        current_params = {
            "sl_atr_multiplier": float(runtime.get("sl_atr_multiplier", self.settings.sl_atr_multiplier)),
            "tp_atr_multiplier": float(runtime.get("tp_atr_multiplier", self.settings.tp_atr_multiplier)),
            "min_signal_confidence": float(runtime.get("min_signal_confidence", self.settings.min_signal_confidence)),
            "cooldown_seconds": int(runtime.get("cooldown_seconds", self.settings.cooldown_seconds)),
            "ema_fast": int(runtime.get("ema_fast", self.settings.ema_fast)),
            "ema_slow": int(runtime.get("ema_slow", self.settings.ema_slow)),
            "ema_trend": int(runtime.get("ema_trend", self.settings.ema_trend)),
            "max_positions": int(runtime.get("max_positions", self.settings.max_positions)),
        }

        return {
            "market_structure": market_report,
            "current_parameters": current_params,
            "objective": "Optimize for aggressive M1 scalping with fast take-profit (0.5x-1.0x ATR TP) and safe stop-loss (1.2x-2.0x ATR SL)",
        }

    def _extract_parameters(self, raw: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
        """Extract and validate parameters from LLM response."""
        params = raw.get("parameters", {})

        # Defaults from current runtime or settings
        def get_default(key: str, default: Any) -> Any:
            return runtime.get(key, getattr(self.settings, key, default))

        # Extract with clamping to safe ranges
        sl_atr = self._clamp(float(params.get("sl_atr_multiplier", get_default("sl_atr_multiplier", 1.5))), 1.0, 2.5)
        tp_atr = self._clamp(float(params.get("tp_atr_multiplier", get_default("tp_atr_multiplier", 0.8))), 0.5, 1.5)
        min_conf = self._clamp(float(params.get("min_signal_confidence", get_default("min_signal_confidence", 0.65))), 0.50, 0.80)
        cooldown = int(self._clamp(float(params.get("cooldown_seconds", get_default("cooldown_seconds", 120))), 60, 240))
        ema_fast = int(self._clamp(float(params.get("ema_fast", get_default("ema_fast", 8))), 5, 12))
        ema_slow = int(self._clamp(float(params.get("ema_slow", get_default("ema_slow", 21))), 15, 30))
        ema_trend = int(self._clamp(float(params.get("ema_trend", get_default("ema_trend", 55))), 40, 80))

        # Extract max_positions from LLM recommendation
        max_positions = int(self._clamp(float(params.get("max_positions", get_default("max_positions", 1))), 1, 3))

        # Ensure ema_fast < ema_slow < ema_trend
        if ema_fast >= ema_slow:
            ema_fast = max(5, ema_slow - 5)
        if ema_slow >= ema_trend:
            ema_slow = max(15, ema_trend - 10)

        return {
            "sl_atr_multiplier": round(sl_atr, 2),
            "tp_atr_multiplier": round(tp_atr, 2),
            "min_signal_confidence": round(min_conf, 2),
            "cooldown_seconds": cooldown,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "ema_trend": ema_trend,
            "atr_period": 14,  # Keep stable for ATR calculation
            "rsi_period": 14,  # Keep stable for RSI calculation
            "max_positions": max_positions,
        }

    def _chat_completion(self, payload: dict) -> dict:
        """Call DeepSeek chat completion API."""
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
            timeout_seconds = _LLM_TIMEOUT_SECONDS.get() or self.settings.llm_timeout_seconds
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"DeepSeek HTTP {exc.code}: {message}") from exc

    def _clamp(self, value: float, min_val: float, max_val: float) -> float:
        """Clamp value to [min_val, max_val] range."""
        return max(min_val, min(max_val, value))