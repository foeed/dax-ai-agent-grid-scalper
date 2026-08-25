from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import redirect_stderr
from dataclasses import replace
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from trend_scalper.bot import build_parser
from trend_scalper.config import load_settings, validate_settings
from trend_scalper.indicators import add_indicators
from trend_scalper.models import AccountSnapshot
from trend_scalper.paper_client import PaperClient
from trend_scalper.risk import RiskManager
from trend_scalper.strategy import PullbackScalperStrategy as TrendScalperStrategy


class ConfigTests(unittest.TestCase):
    def test_loads_env_file_without_external_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "TRADING_MODE=paper\nSYMBOL=EURUSD\nBARS=120\nUSE_LLM=false\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                settings = load_settings(env_file)

        self.assertEqual(settings.trading_mode, "paper")
        self.assertEqual(settings.symbol, "EURUSD")
        self.assertEqual(settings.bars, 120)
        self.assertEqual(validate_settings(settings), [])

    def test_live_login_requires_password(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TRADING_MODE": "live",
                "MT5_PATH": r"C:\Program Files\MetaTrader 5\terminal64.exe",
                "MT5_LOGIN": "123456",
            },
            clear=True,
        ):
            settings = load_settings(None)

        self.assertIn("MT5_PASSWORD is required", "\n".join(validate_settings(settings)))


class IndicatorAndStrategyTests(unittest.TestCase):
    def test_indicators_add_expected_values(self) -> None:
        rates = [
            {"open": 100 + index, "high": 101 + index, "low": 99 + index, "close": 100.5 + index}
            for index in range(80)
        ]

        data = add_indicators(rates, ema_fast=8, ema_slow=21, ema_trend=55, atr_period=14, rsi_period=14)

        self.assertEqual(len(data), 80)
        self.assertIn("ema_fast", data[-1])
        self.assertIn("atr", data[-1])
        self.assertIsNotNone(data[-1]["rsi"])

    def test_paper_strategy_produces_valid_signal(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = load_settings(None)
        client = PaperClient(settings)
        signal = TrendScalperStrategy(settings).analyze(
            client.get_rates(), None, client.point()
        )

        self.assertIn(signal.action, {"BUY", "SELL", "HOLD"})
        self.assertGreaterEqual(signal.confidence, 0.0)
        self.assertLessEqual(signal.confidence, 1.0)
        if signal.is_trade:
            self.assertGreater(signal.sl_distance, 0)
            self.assertGreater(signal.tp_distance, 0)


class RiskTests(unittest.TestCase):
    def test_daily_trade_cap_blocks_after_recorded_trade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {}, clear=True):
                settings = replace(
                    load_settings(None),
                    max_trades_per_day=1,
                    cooldown_seconds=0,
                    state_path=Path(directory) / "state.json",
                )
            account = AccountSnapshot(balance=10_000, equity=10_000)
            risk = RiskManager(settings)

            allowed, _ = risk.can_trade(account)
            self.assertTrue(allowed)

            risk.record_trade(account)
            allowed, reason = risk.can_trade(account)

        self.assertFalse(allowed)
        self.assertEqual(reason, "Max trades per day reached")


class CliTests(unittest.TestCase):
    def test_parser_rejects_abbreviated_once_flag(self) -> None:
        parser = build_parser()
        self.assertFalse(parser.allow_abbrev)
        args = parser.parse_args(["--once"])
        self.assertTrue(args.once)


if __name__ == "__main__":
    unittest.main()
