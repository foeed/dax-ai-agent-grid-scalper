from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from trend_scalper.config import load_settings
from trend_scalper.llm_filter import DeepSeekRiskFilter
from trend_scalper.models import AccountSnapshot, TradeSignal
from trend_scalper.signal_service import SignalEngine


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
EA_PATH = PROJECT_ROOT / "mql5" / "Experts" / "TrendScalperEA.mq5"


class SignalEngineTests(unittest.TestCase):
    def test_engine_returns_buy_for_uptrend_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {}, clear=True):
                settings = replace(
                    load_settings(None),
                    state_path=Path(directory) / "state.json",
                    event_log_path=Path(directory) / "events.jsonl",
                    dashboard_settings_path=Path(directory) / "dashboard_settings.json",
                    cooldown_seconds=0,
                )
            result = SignalEngine(settings).evaluate(_payload(_uptrend_rates()))
            event_log_exists = settings.event_log_path.exists()
            event_log_text = settings.event_log_path.read_text(encoding="utf-8")

        self.assertEqual(result["action"], "BUY")
        self.assertGreater(result["sl_points"], 0)
        self.assertGreater(result["tp_points"], 0)
        self.assertTrue(event_log_exists)
        self.assertIn('"action":"BUY"', event_log_text)

    def test_engine_records_trade_result_and_risk_cap_blocks_next_signal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {}, clear=True):
                settings = replace(
                    load_settings(None),
                    state_path=Path(directory) / "state.json",
                    event_log_path=Path(directory) / "events.jsonl",
                    dashboard_settings_path=Path(directory) / "dashboard_settings.json",
                    max_trades_per_day=1,
                    cooldown_seconds=0,
                )
            engine = SignalEngine(settings)
            first = engine.evaluate(_payload(_uptrend_rates()))
            recorded = engine.record_trade_result({"success": True, "account": _account()})
            second = engine.evaluate(_payload(_uptrend_rates()))

        self.assertEqual(first["action"], "BUY")
        self.assertTrue(recorded["recorded"])
        self.assertEqual(second["action"], "HOLD")
        self.assertEqual(second["reason"], "Max trades per day reached")

    def test_engine_accepts_crypto_symbol_when_spread_cap_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {}, clear=True):
                settings = replace(
                    load_settings(None),
                    max_spread_points=0,
                    state_path=Path(directory) / "state.json",
                    event_log_path=Path(directory) / "events.jsonl",
                    dashboard_settings_path=Path(directory) / "dashboard_settings.json",
                    cooldown_seconds=0,
                )
            payload = _payload(_uptrend_rates())
            payload["symbol"] = "BTCUSD"
            payload["point"] = 0.01
            payload["spread_points"] = 2500
            result = SignalEngine(settings).evaluate(payload)

        self.assertEqual(result["action"], "BUY")
        self.assertGreater(result["sl_points"], 0)


class DeepSeekRiskFilterTests(unittest.TestCase):
    def test_prompt_marks_zero_spread_cap_as_disabled(self) -> None:
        captured: dict = {}

        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {}, clear=True):
                settings = replace(
                    load_settings(None),
                    max_spread_points=0,
                    deepseek_api_key="test-key",
                    state_path=Path(directory) / "state.json",
                    event_log_path=Path(directory) / "events.jsonl",
                )

            class CapturingFilter(DeepSeekRiskFilter):
                def _chat_completion(self, payload: dict) -> dict:
                    captured.update(payload)
                    return {
                        "choices": [
                            {"message": {"content": '{"approved":true,"score":0.9,"reason":"ok"}'}}
                        ]
                    }

            decision = CapturingFilter(settings).review(
                TradeSignal("BUY", 0.8, "trend ok", 1.0, 2.0),
                _uptrend_rates(),
                AccountSnapshot(balance=10_000, equity=10_000),
                spread_points=12_000,
                open_positions=0,
                symbol="SOLUSDm",
                timeframe="H1",
            )

        self.assertTrue(decision.approved)
        system_prompt = captured["messages"][0]["content"]
        user_payload = json.loads(captured["messages"][1]["content"].split("\n", 1)[1])
        self.assertIn("deterministic spread cap is disabled", system_prompt)
        self.assertEqual(user_payload["risk"]["max_spread_points"], 0)
        self.assertIn("disabled", user_payload["risk"]["max_spread_points_note"])

    def test_fail_open_approves_when_llm_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {}, clear=True):
                settings = replace(
                    load_settings(None),
                    deepseek_api_key="test-key",
                    state_path=Path(directory) / "state.json",
                    event_log_path=Path(directory) / "events.jsonl",
                )

            class FailingFilter(DeepSeekRiskFilter):
                def _chat_completion(self, payload: dict) -> dict:
                    raise TimeoutError("simulated timeout")

            decision = FailingFilter(settings).review(
                TradeSignal("BUY", 0.8, "trend ok", 1.0, 2.0),
                _uptrend_rates(),
                AccountSnapshot(balance=10_000, equity=10_000),
                spread_points=12_000,
                open_positions=0,
                fail_closed=False,
            )

        self.assertTrue(decision.approved)
        self.assertEqual(decision.score, 1.0)
        self.assertIn("LLM bypassed", decision.reason)


class SignalHttpE2ETests(unittest.TestCase):
    def test_http_signal_endpoint_returns_buy(self) -> None:
        port = _free_port()
        token = "test-token"
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env.signal"
            env_file.write_text(
                "\n".join(
                    [
                        "TRADING_MODE=paper",
                        "USE_LLM=false",
                        "DRY_RUN=true",
                        "SYMBOL=XAUUSD",
                        "BARS=300",
                        "COOLDOWN_SECONDS=0",
                        "SIGNAL_HOST=127.0.0.1",
                        f"SIGNAL_PORT={port}",
                        f"SIGNAL_TOKEN={token}",
                        f"STATE_PATH={Path(directory) / 'state.json'}",
                        f"EVENT_LOG_PATH={Path(directory) / 'events.jsonl'}",
                        f"DASHBOARD_SETTINGS_PATH={Path(directory) / 'dashboard_settings.json'}",
                        "DASHBOARD_AUTO_TOKEN=true",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            process = subprocess.Popen(
                [sys.executable, "-m", "trend_scalper.signal_service", "--env-file", str(env_file)],
                cwd=PROJECT_ROOT,
                env=_subprocess_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                _wait_for_health(port)
                result = _post_json(
                    f"http://127.0.0.1:{port}/signal",
                    _payload(_uptrend_rates()),
                    token=token,
                )
                status = _get_json(f"http://127.0.0.1:{port}/api/status", token=token)
                events = _get_json(f"http://127.0.0.1:{port}/api/events?limit=5", token=token)
                dashboard = _get_text(f"http://127.0.0.1:{port}/dashboard")
                settings_update = _post_json(
                    f"http://127.0.0.1:{port}/api/settings",
                    {
                        "trading_mode": "live",
                        "dry_run": False,
                        "use_llm": False,
                        "llm_fail_closed": False,
                    },
                    token=token,
                )
                updated_status = _get_json(f"http://127.0.0.1:{port}/api/status", token=token)
                with self.assertRaises(urllib.error.HTTPError):
                    _get_json(f"http://127.0.0.1:{port}/api/status", token="")
            finally:
                process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)

        self.assertEqual(result["action"], "BUY")
        self.assertGreater(result["confidence"], 0)
        self.assertTrue(status["ok"])
        self.assertGreaterEqual(status["summary"]["events_count"], 1)
        self.assertEqual(events["events"][0]["action"], "BUY")
        self.assertIn("Trend Scalper Monitor", dashboard)
        self.assertIn('window.__SIGNAL_TOKEN__ = "test-token"', dashboard)
        self.assertIn("Edit Agent Runtime", dashboard)
        self.assertIn("LLM Fail Closed", dashboard)
        self.assertEqual(settings_update["settings"]["trading_mode"], "live")
        self.assertFalse(settings_update["settings"]["dry_run"])
        self.assertFalse(settings_update["settings"]["llm_fail_closed"])
        self.assertEqual(updated_status["settings"]["trading_mode"], "live")
        self.assertFalse(updated_status["settings"]["dry_run"])
        self.assertFalse(updated_status["settings"]["llm_fail_closed"])


class Mql5ExpertTests(unittest.TestCase):
    def test_ea_contains_required_executor_integration_points(self) -> None:
        source = EA_PATH.read_text(encoding="utf-8")

        self.assertIn("WebRequest", source)
        self.assertIn("CopyRates", source)
        self.assertIn("trade.Buy", source)
        self.assertIn("trade.Sell", source)
        self.assertIn("/trade-result", source)
        self.assertIn("if(DryRun)\n      return;", source)
        self.assertIn("input int MaxSpreadPoints = 0", source)
        self.assertIn("input int RequestTimeoutMs = 30000", source)
        self.assertIn("input int RequestRetries = 1", source)
        self.assertIn("status == 1003", source)
        self.assertIn("error == 5203", source)
        self.assertIn("NormalizeVolume", source)
        self.assertIn("JsonValueStart", source)
        self.assertIn("IsJsonWhitespace", source)


def _payload(rates: list[dict]) -> dict:
    return {
        "symbol": "XAUUSD",
        "timeframe": "M1",
        "point": 0.01,
        "spread_points": 12,
        "positions_count": 0,
        "account": _account(),
        "rates": rates,
    }


def _account() -> dict:
    return {"balance": 10_000, "equity": 10_000, "currency": "USD"}


def _uptrend_rates() -> list[dict]:
    rates: list[dict] = []
    price = 2300.0
    for index in range(300):
        open_value = price
        close = open_value + 0.25
        rates.append(
            {
                "time": f"2026-01-01T00:{index % 60:02d}:00Z",
                "open": open_value,
                "high": close + 0.08,
                "low": open_value - 0.08,
                "close": close,
                "tick_volume": 250,
            }
        )
        price = close
    return rates


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_health(port: int) -> None:
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.1)
    raise TimeoutError("signal service did not become healthy")


def _post_json(url: str, payload: dict, token: str) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json(url: str, token: str) -> dict:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, method="GET", headers=headers)
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.read().decode("utf-8")


def _subprocess_env() -> dict[str, str]:
    env = {
        "PYTHONPATH": str(SRC_PATH),
        "PYTHONIOENCODING": "utf-8",
        "PATH": os.environ.get("PATH", ""),
    }
    for key in ("SystemRoot", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


if __name__ == "__main__":
    unittest.main()
