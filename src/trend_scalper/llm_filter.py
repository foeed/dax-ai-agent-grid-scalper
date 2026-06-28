from __future__ import annotations

import json
import logging
import time as time_module
import urllib.error
import urllib.request
from contextvars import ContextVar
from typing import Any

from .config import Settings
from .models import AccountSnapshot, LLMDecision, Rate, TradeSignal

logger = logging.getLogger(__name__)
_LLM_TIMEOUT_SECONDS: ContextVar[float | None] = ContextVar("llm_timeout_seconds", default=None)


class DeepSeekRiskFilter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._consecutive_errors = 0
        self._circuit_open = False
        self._circuit_open_since = 0.0
        self._MAX_CONSECUTIVE_ERRORS = 5
        self._CIRCUIT_RESET_SECONDS = 300

    def review(
        self,
        signal: TradeSignal,
        rates: list[Rate],
        account: AccountSnapshot,
        spread_points: float,
        open_positions: int,
        symbol: str | None = None,
        timeframe: str | None = None,
        fail_closed: bool | None = None,
        runtime: dict[str, Any] | None = None,
    ) -> LLMDecision:
        if not signal.is_trade:
            return LLMDecision(False, 0.0, "No trade signal to review")

        effective = runtime or {}
        max_spread_points = float(effective.get("max_spread_points", self.settings.max_spread_points))
        payload = {
            "symbol": symbol or str(effective.get("symbol", self.settings.symbol)),
            "timeframe": timeframe or str(effective.get("timeframe", self.settings.timeframe)),
            "candidate_action": signal.action,
            "confidence": signal.confidence,
            "strategy_reason": signal.reason,
            "signal_metadata": signal.metadata,
            "risk": {
                "equity": account.equity,
                "risk_percent": float(effective.get("risk_percent", self.settings.risk_percent)),
                "daily_loss_limit_percent": float(
                    effective.get("daily_loss_limit_percent", self.settings.daily_loss_limit_percent)
                ),
                "max_trades_per_day": int(effective.get("max_trades_per_day", self.settings.max_trades_per_day)),
                "min_signal_confidence": float(
                    effective.get("min_signal_confidence", self.settings.min_signal_confidence)
                ),
                "max_spread_points": max_spread_points,
                "max_spread_points_note": (
                    "disabled; do not reject only because spread exceeds this value"
                    if max_spread_points <= 0
                    else "enabled"
                ),
                "spread_points": spread_points,
                "open_positions": open_positions,
                "max_positions": int(effective.get("max_positions", self.settings.max_positions)),
            },
            "recent_bars": self._compact_bars(rates),
        }

        try:
            timeout_seconds = float(effective.get("llm_timeout_seconds", self.settings.llm_timeout_seconds))
            timeout_token = _LLM_TIMEOUT_SECONDS.set(timeout_seconds)
            try:
                response = self._chat_completion(
                    {
                        "model": self.settings.deepseek_model,
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "You are a strict trading risk filter. Return only JSON. "
                                    "Approve only when the supplied trend-scalping signal is coherent, "
                                    "spread/risk are acceptable, and recent candles do not contradict it. "
                                    "If max_spread_points is 0 or lower, the deterministic spread cap is disabled; "
                                    "do not interpret it as zero allowed spread. "
                                    "Never invent missing market data."
                                ),
                            },
                            {
                                "role": "user",
                                "content": (
                                    "Review this candidate trade and return JSON with keys: "
                                    "approved boolean, score number from 0 to 1, reason short string.\n"
                                    f"{json.dumps(payload, separators=(',', ':'))}"
                                ),
                            },
                        ],
                        "response_format": {"type": "json_object"},
                        "stream": False,
                    }
                )
            finally:
                _LLM_TIMEOUT_SECONDS.reset(timeout_token)
            content = response["choices"][0]["message"].get("content") or "{}"
            raw = json.loads(content)
            self._consecutive_errors = 0
            return LLMDecision(
                approved=bool(raw.get("approved", False)),
                score=max(0.0, min(1.0, float(raw.get("score", 0.0)))),
                reason=str(raw.get("reason", "No reason provided"))[:240],
            )
        except Exception as exc:
            logger.warning("DeepSeek review failed: %s", exc)
            self._consecutive_errors += 1
            if self._consecutive_errors >= self._MAX_CONSECUTIVE_ERRORS:
                if not self._circuit_open:
                    self._circuit_open = True
                    self._circuit_open_since = time_module.time()
                    logger.warning("DeepSeek circuit breaker OPEN (consecutive errors: %d)", self._consecutive_errors)
            effective_fail_closed = (
                bool(effective.get("llm_fail_closed", self.settings.llm_fail_closed))
                if fail_closed is None
                else fail_closed
            )
            if effective_fail_closed:
                return LLMDecision(False, 0.0, f"LLM unavailable: {exc}")
            return LLMDecision(True, 1.0, f"LLM bypassed after error: {exc}")

    def _chat_completion(self, payload: dict) -> dict:
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

    def _compact_bars(self, rates: list[Rate]) -> list[dict[str, float | str]]:
        compact: list[dict[str, float | str]] = []
        for row in rates[-20:]:
            compact.append(
                {
                    "time": str(row.get("time", "")),
                    "open": round(float(row["open"]), 6),
                    "high": round(float(row["high"]), 6),
                    "low": round(float(row["low"]), 6),
                    "close": round(float(row["close"]), 6),
                }
            )
        return compact
