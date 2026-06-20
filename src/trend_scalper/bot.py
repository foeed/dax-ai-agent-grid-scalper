from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from types import FrameType

from .config import Settings, load_settings, validate_settings
from .bridge_client import BridgeMt5Client
from .llm_filter import DeepSeekRiskFilter
from .models import TradeSignal
from .mt5_client import Mt5Client
from .paper_client import PaperClient
from .risk import RiskManager
from .strategy import TrendScalperStrategy

logger = logging.getLogger(__name__)


class TrendScalperBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if settings.trading_mode == "live":
            self.client = Mt5Client(settings)
        elif settings.trading_mode == "bridge":
            self.client = BridgeMt5Client(settings)
        else:
            self.client = PaperClient(settings)
        self.strategy = TrendScalperStrategy(settings)
        self.risk = RiskManager(settings)
        self.llm = DeepSeekRiskFilter(settings) if settings.use_llm else None
        self._stop = False

    def run(self, once: bool = False) -> None:
        self.client.connect()
        try:
            while not self._stop:
                self.run_cycle()
                if once:
                    break
                time.sleep(self.settings.poll_seconds)
        finally:
            self.client.shutdown()

    def stop(self, _signum: int | None = None, _frame: FrameType | None = None) -> None:
        self._stop = True

    def run_cycle(self) -> None:
        account = self.client.get_account_snapshot()
        allowed, risk_reason = self.risk.can_trade(account)
        if not allowed:
            logger.info("Risk gate blocked trading: %s", risk_reason)
            return

        rates = self.client.get_rates()
        signal_value = self.strategy.analyze(rates, self.client.point())
        if not signal_value.is_trade:
            logger.info("No trade: %s confidence=%.3f", signal_value.reason, signal_value.confidence)
            return

        spread = self.client.spread_points()
        if self.settings.max_spread_points > 0 and spread > self.settings.max_spread_points:
            logger.info("Spread blocked trade: %.1f > %.1f", spread, self.settings.max_spread_points)
            return

        positions = self.client.get_positions()
        if len(positions) >= self.settings.max_positions:
            logger.info("Position cap blocked trade: %d >= %d", len(positions), self.settings.max_positions)
            return

        if self.llm:
            decision = self.llm.review(signal_value, rates, account, spread, len(positions))
            if not decision.approved or decision.score < self.settings.llm_min_score:
                logger.info(
                    "DeepSeek blocked %s: score=%.2f reason=%s",
                    signal_value.action,
                    decision.score,
                    decision.reason,
                )
                return
            logger.info("DeepSeek approved: score=%.2f reason=%s", decision.score, decision.reason)

        volume = self.client.calculate_volume(signal_value, account)
        self._execute(signal_value, volume, account)

    def _execute(self, signal_value: TradeSignal, volume: float, account) -> None:
        if self.settings.dry_run:
            logger.info(
                "DRY_RUN would place %s %.2f %s confidence=%.3f sl=%.5f tp=%.5f reason=%s",
                signal_value.action,
                volume,
                self.settings.symbol,
                signal_value.confidence,
                signal_value.sl_distance,
                signal_value.tp_distance,
                signal_value.reason,
            )
            return

        result = self.client.place_order(signal_value, volume)
        if result.success:
            logger.info("Order sent: %s", result.message)
            self.risk.record_trade(account)
        else:
            logger.warning("Order failed: %s", result.message)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Trend scalper AI bot for MT5 and DeepSeek",
        allow_abbrev=False,
    )
    parser.add_argument("--once", action="store_true", help="Run one trading cycle and exit")
    parser.add_argument("--check", action="store_true", help="Validate configuration and exit")
    parser.add_argument("--env-file", default=".env", help="Path to environment file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(args.env_file)
    configure_logging(settings.log_level)

    errors = validate_settings(settings)
    if errors:
        for error in errors:
            logger.warning("Config: %s", error)
        if settings.trading_mode == "live" or settings.use_llm:
            return 2

    if args.check:
        logger.info("Configuration looks usable for %s mode", settings.trading_mode)
        return 0

    bot = TrendScalperBot(settings)
    signal.signal(signal.SIGINT, bot.stop)
    signal.signal(signal.SIGTERM, bot.stop)
    try:
        bot.run(once=args.once)
    except KeyboardInterrupt:
        bot.stop()
    except Exception as exc:
        logger.exception("Bot stopped after error: %s", exc)
        return 1
    return 0
