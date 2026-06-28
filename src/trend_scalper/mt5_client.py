from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone
from typing import Any

from .config import Settings
from .models import AccountSnapshot, OrderResult, PositionSnapshot, Rate, TradeSignal

logger = logging.getLogger(__name__)

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover
    mt5 = None


TIMEFRAMES: dict[str, str] = {
    "M1": "TIMEFRAME_M1",
    "M2": "TIMEFRAME_M2",
    "M3": "TIMEFRAME_M3",
    "M4": "TIMEFRAME_M4",
    "M5": "TIMEFRAME_M5",
    "M10": "TIMEFRAME_M10",
    "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1",
    "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
    "W1": "TIMEFRAME_W1",
    "MN1": "TIMEFRAME_MN1",
}


class Mt5Client:
    def __init__(self, settings: Settings) -> None:
        if mt5 is None:
            raise RuntimeError(
                "MetaTrader5 package is not installed. Use Windows native Python with "
                "`pip install -r requirements-mt5.txt`, or run paper mode in Docker."
            )
        self.settings = settings

    def connect(self) -> None:
        init_kwargs: dict[str, Any] = {}
        if self.settings.mt5_path:
            init_kwargs["path"] = self.settings.mt5_path

        if not mt5.initialize(**init_kwargs):
            code, message = mt5.last_error()
            raise RuntimeError(f"MT5 initialize failed [{code}]: {message}")

        if self.settings.mt5_login is not None:
            if not mt5.login(
                self.settings.mt5_login,
                password=self.settings.mt5_password,
                server=self.settings.mt5_server or None,
            ):
                code, message = mt5.last_error()
                raise RuntimeError(f"MT5 login failed [{code}]: {message}")

        if not mt5.symbol_select(self.settings.symbol, True):
            code, message = mt5.last_error()
            raise RuntimeError(f"MT5 symbol_select failed [{code}]: {message}")

        account = self.get_account_snapshot()
        logger.info("Connected to MT5 account equity=%s %s", account.equity, account.currency)

    def shutdown(self) -> None:
        mt5.shutdown()

    def get_rates(self) -> list[Rate]:
        timeframe = self._timeframe()
        rates = mt5.copy_rates_from_pos(self.settings.symbol, timeframe, 0, self.settings.bars)
        if rates is None or len(rates) == 0:
            code, message = mt5.last_error()
            raise RuntimeError(f"MT5 copy_rates_from_pos failed [{code}]: {message}")
        return [self._rate_to_dict(rate) for rate in rates]

    def get_account_snapshot(self) -> AccountSnapshot:
        info = mt5.account_info()
        if info is None:
            code, message = mt5.last_error()
            raise RuntimeError(f"MT5 account_info failed [{code}]: {message}")
        return AccountSnapshot(
            balance=float(info.balance),
            equity=float(info.equity),
            currency=str(info.currency),
        )

    def get_positions(self) -> list[PositionSnapshot]:
        positions = mt5.positions_get(symbol=self.settings.symbol)
        if positions is None:
            return []

        snapshots: list[PositionSnapshot] = []
        for position in positions:
            magic = int(getattr(position, "magic", 0))
            if magic != self.settings.magic_number:
                continue
            side = "BUY" if int(position.type) == mt5.POSITION_TYPE_BUY else "SELL"
            snapshots.append(
                PositionSnapshot(
                    symbol=str(position.symbol),
                    side=side,
                    volume=float(position.volume),
                    profit=float(position.profit),
                    magic=magic,
                )
            )
        return snapshots

    def spread_points(self) -> float:
        tick = mt5.symbol_info_tick(self.settings.symbol)
        info = self._symbol_info()
        if tick is None:
            return math.inf
        point_val = float(getattr(info, "point", 0.0)) or 0.00001
        return abs(float(tick.ask) - float(tick.bid)) / point_val

    def point(self) -> float:
        return float(self._symbol_info().point)

    def calculate_volume(self, signal: TradeSignal, account: AccountSnapshot) -> float:
        info = self._symbol_info()
        if self.settings.fixed_lot is not None:
            return self._normalize_volume(self.settings.fixed_lot, info)

        risk_amount = account.equity * (self.settings.risk_percent / 100)
        tick_size = float(getattr(info, "trade_tick_size", 0.0) or info.point)
        tick_value = float(getattr(info, "trade_tick_value", 0.0) or 0.0)
        if tick_size <= 0 or tick_value <= 0 or signal.sl_distance <= 0:
            return self._normalize_volume(self.settings.min_lot, info)

        loss_per_lot = (signal.sl_distance / tick_size) * tick_value
        if loss_per_lot <= 0:
            return self._normalize_volume(self.settings.min_lot, info)

        raw_volume = risk_amount / loss_per_lot
        return self._normalize_volume(raw_volume, info)

    def place_order(self, signal: TradeSignal, volume: float) -> OrderResult:
        if signal.action not in {"BUY", "SELL"}:
            return OrderResult(False, "Cannot place HOLD order")

        tick = mt5.symbol_info_tick(self.settings.symbol)
        info = self._symbol_info()
        if tick is None:
            code, message = mt5.last_error()
            return OrderResult(False, f"MT5 tick unavailable [{code}]: {message}")

        stops_level = int(getattr(info, "trade_stops_level", 0) or 0)
        point_size = float(info.point)
        sl_price_distance = signal.sl_distance
        sl_points = int(math.ceil(sl_price_distance / point_size))
        if sl_points < stops_level:
            return OrderResult(False, f"SL distance {sl_points} pts below broker minimum {stops_level} pts")

        is_buy = signal.action == "BUY"
        order_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
        price = float(tick.ask if is_buy else tick.bid)
        sl = price - signal.sl_distance if is_buy else price + signal.sl_distance
        tp = price + signal.tp_distance if is_buy else price - signal.tp_distance
        digits = int(info.digits)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.settings.symbol,
            "volume": volume,
            "type": order_type,
            "price": round(price, digits),
            "sl": round(sl, digits),
            "tp": round(tp, digits),
            "deviation": self.settings.deviation_points,
            "magic": self.settings.magic_number,
            "comment": self.settings.order_comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(info),
        }

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            check = mt5.order_check(request)
            if check is None:
                code, message = mt5.last_error()
                return OrderResult(False, f"MT5 order_check failed [{code}]: {message}")
            if check.retcode != mt5.TRADE_RETCODE_DONE:
                return OrderResult(False, f"MT5 order_check rejected retcode={check.retcode}: {check.comment}")

            result = mt5.order_send(request)
            if result is None:
                code, message = mt5.last_error()
                return OrderResult(False, f"MT5 order_send failed [{code}]: {message}")

            retcode = int(result.retcode)

            if retcode == mt5.TRADE_RETCODE_DONE:
                filled_volume = float(getattr(result, "volume", 0.0) or 0.0)
                actual_price = float(getattr(result, "price", 0.0) or 0.0)
                if filled_volume < volume * 0.99:
                    logger.warning(
                        "Partial fill: requested %.2f lots, filled %.2f lots (%.1f%%)",
                        volume, filled_volume, (filled_volume / volume * 100) if volume > 0 else 0,
                    )
                slippage = abs(actual_price - price) if actual_price > 0 else 0.0
                if slippage > 0:
                    logger.info(
                        "Slippage: requested %.5f, filled %.5f (%.1f pts)",
                        price, actual_price, slippage / point_size if point_size > 0 else 0,
                    )
                return OrderResult(
                    success=True,
                    message=f"MT5 retcode={result.retcode}: {result.comment}",
                    order_id=int(result.order) if getattr(result, "order", 0) else None,
                    retcode=retcode,
                )

            retry_codes = {
                mt5.TRADE_RETCODE_REQUOTE,
                mt5.TRADE_RETCODE_CONNECTION,
                mt5.TRADE_RETCODE_TIMEOUT,
                mt5.TRADE_RETCODE_PRICE_CHANGED,
                mt5.TRADE_RETCODE_PRICE_OFF,
            }
            if retcode in retry_codes and attempt < max_retries:
                delay = 0.2 * attempt
                logger.warning("Order retry %d/%d (retcode=%d): %s (waiting %.1fs)",
                               attempt, max_retries, retcode, result.comment, delay)
                time_module.sleep(delay)
                tick = mt5.symbol_info_tick(self.settings.symbol)
                if tick:
                    price = float(tick.ask if is_buy else tick.bid)
                    sl = price - signal.sl_distance if is_buy else price + signal.sl_distance
                    tp = price + signal.tp_distance if is_buy else price - signal.tp_distance
                    request["price"] = round(price, digits)
                    request["sl"] = round(sl, digits)
                    request["tp"] = round(tp, digits)
                continue

            return OrderResult(
                success=False,
                message=f"MT5 retcode={result.retcode}: {result.comment}",
                order_id=int(result.order) if getattr(result, "order", 0) else None,
                retcode=retcode,
            )

        return OrderResult(False, "Max retries exhausted")

    def _symbol_info(self) -> Any:
        info = mt5.symbol_info(self.settings.symbol)
        if info is None:
            code, message = mt5.last_error()
            raise RuntimeError(f"MT5 symbol_info failed [{code}]: {message}")
        return info

    def _rate_to_dict(self, rate: Any) -> Rate:
        names = getattr(rate, "dtype", None)
        fields = names.names if names is not None else []
        output: Rate = {}
        for field in fields:
            value = rate[field]
            if hasattr(value, "item"):
                value = value.item()
            output[field] = value
        if "time" in output:
            output["time"] = datetime.fromtimestamp(int(output["time"]), timezone.utc).isoformat()
        return output

    def _timeframe(self) -> int:
        attribute = TIMEFRAMES.get(self.settings.timeframe)
        if not attribute:
            raise ValueError(f"Unsupported TIMEFRAME={self.settings.timeframe}")
        return int(getattr(mt5, attribute))

    def _normalize_volume(self, raw_volume: float, info: Any) -> float:
        broker_min = float(getattr(info, "volume_min", self.settings.min_lot))
        broker_max = float(getattr(info, "volume_max", self.settings.max_lot))
        step = float(getattr(info, "volume_step", 0.01) or 0.01)
        lower = max(self.settings.min_lot, broker_min)
        upper = min(self.settings.max_lot, broker_max)
        clipped = max(lower, min(upper, raw_volume))
        steps = math.floor(clipped / step)
        normalized = steps * step
        precision = max(0, len(str(step).split(".")[-1].rstrip("0")))
        return round(max(lower, normalized), precision)

    def _filling_mode(self, info: Any) -> int:
        filling = int(getattr(info, "filling_mode", 0))
        if filling == mt5.ORDER_FILLING_FOK:
            return mt5.ORDER_FILLING_FOK
        if filling == mt5.ORDER_FILLING_RETURN:
            return mt5.ORDER_FILLING_RETURN
        return mt5.ORDER_FILLING_IOC
