from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from types import FrameType

from .accounts import profile_to_runtime, select_profile, validate_account_size
from .config import Settings, load_settings, validate_settings
from .bridge_client import BridgeMt5Client
from .exit_manager import ExitManager
from .llm_filter import DeepSeekRiskFilter
from .models import EntrySignal
from .mt5_client import Mt5Client
from .paper_client import PaperClient
from .risk import RiskManager
from .strategy import PullbackScalperStrategy
from .monitoring import EventStore, RuntimeSettingsStore

logger = logging.getLogger(__name__)

_MAX_CONSECUTIVE_ERRORS = 10


class TrendScalperBot:
    def __init__(self, settings: Settings, auto_account: bool = False) -> None:
        self.settings = settings
        self.runtime_store = RuntimeSettingsStore(settings)
        self.event_store = EventStore(settings, self.runtime_store)
        self._auto_account = auto_account

        if settings.trading_mode == "live":
            self.client = Mt5Client(settings)
        elif settings.trading_mode == "bridge":
            self.client = BridgeMt5Client(settings)
        else:
            self.client = PaperClient(settings, settings)

        self.strategy = PullbackScalperStrategy()
        self.risk = RiskManager(settings)
        self.exit_mgr = ExitManager()
        self.llm = DeepSeekRiskFilter(settings) if settings.use_llm else None
        self._stop = False
        self._consecutive_errors = 0
        self._active_trade_id: int | None = None
        self._active_trade_mfe: float = 0.0
        self._account_profile_applied = False

    def run(self, once: bool = False) -> None:
        self.client.connect()
        self._apply_account_profile()
        try:
            while not self._stop:
                try:
                    start_time = time.time()
                    self.run_cycle()
                    if once:
                        break
                    elapsed = time.time() - start_time
                    poll = self.settings.poll_seconds
                    time.sleep(max(1.0, poll - elapsed))
                except Exception as exc:
                    self._consecutive_errors += 1
                    backoff = min(60, 2 ** min(self._consecutive_errors, 6))
                    logger.exception("Cycle error (#%d): %s. Backing off %ds...", self._consecutive_errors, exc, backoff)
                    if self._consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                        logger.critical("Too many consecutive errors. Stopping.")
                        break
                    time.sleep(backoff)
                    try:
                        self.client.connect()
                        self._consecutive_errors = 0
                    except Exception:
                        pass
        finally:
            self.client.shutdown()

    def stop(self, _signum: int | None = None, _frame: FrameType | None = None) -> None:
        self._stop = True

    def _apply_account_profile(self) -> None:
        """Auto-detect account size and apply safe profile on first cycle."""
        if self._auto_account and not self._account_profile_applied:
            try:
                account = self.client.get_account_snapshot()
                profile = select_profile(account.equity)
                logger.info("Account bracket: %s (equity=%.2f)", profile.bracket, account.equity)

                warning = validate_account_size(account.equity, profile.recommended_symbol, self.settings.min_lot)
                if warning:
                    logger.warning(warning)

                overrides = profile_to_runtime(profile, self.settings.symbol)
                self.runtime_store.apply_auto_tune(overrides)
                self._account_profile_applied = True
                logger.info(
                    "Auto-configured: symbol=%s tf=%s risk=%.1f%% trades/day=%d",
                    profile.recommended_symbol, profile.timeframe,
                    profile.max_risk_percent, profile.max_trades_per_day,
                )
            except Exception as exc:
                logger.warning("Could not auto-detect account: %s. Using defaults.", exc)

    def _get_runtime(self) -> dict:
        runtime = self.runtime_store.effective()
        for key in ("trading_mode", "dry_run", "use_llm"):
            runtime.pop(key, None)
        if self._auto_account and not self._account_profile_applied:
            self._apply_account_profile()
            runtime = self.runtime_store.effective()
            for key in ("trading_mode", "dry_run", "use_llm"):
                runtime.pop(key, None)
        return runtime

    def run_cycle(self) -> None:
        runtime = self._get_runtime()

        if self._active_trade_id is not None:
            self._manage_exits(runtime)
            return

        account = self.client.get_account_snapshot()
        allowed, risk_reason = self.risk.can_trade(account, runtime)
        if not allowed:
            return

        rates_m1 = self._fetch_rates("M1", int(runtime.get("bars", 300)))
        rates_m5 = self._fetch_rates("M5", int(runtime.get("bars", 300)))

        if not rates_m1:
            return

        point = self.client.point()
        signal = self.strategy.analyze(rates_m1, rates_m5, point, runtime)
        if not signal.is_trade:
            if signal.confidence > 0:
                logger.debug("No entry: %s (conf=%.3f)", signal.reason, signal.confidence)
            return

        spread = self.client.spread_points()
        max_spread = float(runtime.get("max_spread_points", self.settings.max_spread_points))
        if max_spread > 0 and spread > max_spread:
            logger.info("Spread blocked: %.1f > %.1f", spread, max_spread)
            return

        positions = self.client.get_positions()
        max_pos = int(runtime.get("max_positions", 1))
        if len(positions) >= max_pos:
            return

        if self.llm:
            decision = self.llm.review(
                signal.as_trade_signal(), rates_m1, account, spread,
                len(positions), runtime=runtime,
            )
            min_score = float(runtime.get("llm_min_score", self.settings.llm_min_score))
            if not decision.approved or decision.score < min_score:
                logger.info("LLM blocked %s: score=%.2f", signal.action, decision.score)
                return

        volume = self.client.calculate_volume(signal.as_trade_signal(), account)
        self._execute(signal, volume, account)

    def _manage_exits(self, runtime: dict) -> None:
        trade_id = self._active_trade_id
        if trade_id is None:
            return

        rates_m1 = self._fetch_rates("M1", 50)
        rates_m5 = self._fetch_rates("M5", 100)

        if not rates_m1:
            return

        from .indicators import add_indicators
        trend_dir = 0
        if rates_m5:
            data = add_indicators(rates_m5, 8, 21, 55, 14, 14)
            ready = [r for r in data if all(r.get(k) is not None for k in ("ema_fast", "ema_slow", "ema_trend", "rsi"))]
            if ready:
                last = ready[-1]
                if float(last["ema_fast"]) > float(last["ema_slow"]) > float(last["ema_trend"]):
                    trend_dir = 1
                elif float(last["ema_fast"]) < float(last["ema_slow"]) < float(last["ema_trend"]):
                    trend_dir = -1

        result = self.exit_mgr.evaluate(trade_id, rates_m1, rates_m5, trend_dir)
        state = self.exit_mgr.get_state(trade_id) or {}

        if state.get("highest_profit", 0) > self._active_trade_mfe:
            self._active_trade_mfe = state["highest_profit"]

        if result.action == "CLOSE":
            logger.info("Exit triggered: %s (MFE=%.5f)", result.reason, self._active_trade_mfe)
            self._close_active_trade(result.reason)
        else:
            logger.debug("Trade %d: bars=%d mfe=%.5f", trade_id, state.get("bars_held", 0), self._active_trade_mfe)

    def _close_active_trade(self, reason: str) -> None:
        tid = self._active_trade_id
        self.event_store.append("exit", {
            "trade_id": tid,
            "reason": reason,
            "mfe": round(self._active_trade_mfe, 6),
        })
        if self.settings.dry_run:
            logger.info("DRY_RUN: would close trade (reason: %s)", reason)
        else:
            try:
                positions = self.client.get_positions()
                for pos in positions:
                    logger.info("Live close needed: %s %s", pos.symbol, pos.side)
            except Exception as exc:
                logger.warning("Failed to close positions: %s", exc)

        if tid is not None:
            self.exit_mgr.remove_trade(tid)
        self._active_trade_id = None
        self._active_trade_mfe = 0.0

    def _fetch_rates(self, timeframe: str, bars: int) -> list | None:
        try:
            return self.client.get_rates()
        except Exception as exc:
            logger.debug("Rate fetch %s failed: %s", timeframe, exc)
            return None

    def _execute(self, signal: EntrySignal, volume: float, account) -> None:
        self.event_store.append("entry", {
            "action": signal.action,
            "confidence": signal.confidence,
            "entry_price": signal.entry_price,
            "sl_distance": signal.sl_distance,
            "tp_distance": signal.tp_distance,
            "reason": signal.reason[:200],
            "trend_strength": signal.trend_strength,
        })

        if self.settings.dry_run:
            logger.info(
                "DRY_RUN %s %.2f %s conf=%.3f sl=%.5f tp=%.5f trend_str=%.2f",
                signal.action, volume, self.settings.symbol,
                signal.confidence, signal.sl_distance, signal.tp_distance, signal.trend_strength,
            )
            self._active_trade_id = 1
            self.exit_mgr.register_trade(1, signal)
            return

        result = self.client.place_order(signal.as_trade_signal(), volume)
        if result.success:
            logger.info("Order sent: %s", result.message)
            self.risk.record_trade(account, success=True)
            if result.order_id:
                self._active_trade_id = result.order_id
                self.exit_mgr.register_trade(result.order_id, signal)
        else:
            logger.warning("Order failed: %s", result.message)
            self.risk.record_trade(account, success=False)
        self._consecutive_errors = 0


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Trend Scalper AI",
        allow_abbrev=False,
    )
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--check", action="store_true", help="Validate config and exit")
    parser.add_argument("--env-file", default=".env", help="Path to env file")
    parser.add_argument("--auto-account", action="store_true", help="Auto-detect account size and tune params")
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
        logger.info("Configuration OK for %s mode", settings.trading_mode)
        return 0

    bot = TrendScalperBot(settings, auto_account=args.auto_account)
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
