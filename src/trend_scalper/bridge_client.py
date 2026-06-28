from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import asdict
from typing import Any

from .config import Settings
from .models import AccountSnapshot, OrderResult, PositionSnapshot, Rate, TradeSignal


class BridgeMt5Client:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.bridge_url.rstrip("/")

    def connect(self) -> None:
        self._get("/health")

    def shutdown(self) -> None:
        return None

    def get_rates(self) -> list[Rate]:
        raw = self._get(f"/rates?bars={self.settings.bars}")
        return list(raw["rates"])

    def get_account_snapshot(self) -> AccountSnapshot:
        raw = self._get("/account")
        return AccountSnapshot(
            balance=float(raw["balance"]),
            equity=float(raw["equity"]),
            currency=str(raw["currency"]),
        )

    def get_positions(self) -> list[PositionSnapshot]:
        raw = self._get("/positions")
        return [
            PositionSnapshot(
                symbol=str(item["symbol"]),
                side=str(item["side"]),
                volume=float(item["volume"]),
                profit=float(item["profit"]),
                magic=int(item["magic"]),
            )
            for item in raw["positions"]
        ]

    def spread_points(self) -> float:
        return float(self._get("/spread")["spread_points"])

    def point(self) -> float:
        return float(self._get("/point")["point"])

    def calculate_volume(self, signal: TradeSignal, account: AccountSnapshot) -> float:
        raw = self._post("/volume", {"signal": asdict(signal), "account": asdict(account)})
        return float(raw["volume"])

    def place_order(self, signal: TradeSignal, volume: float) -> OrderResult:
        raw = self._post("/order", {"signal": asdict(signal), "volume": volume})
        return OrderResult(
            success=bool(raw["success"]),
            message=str(raw["message"]),
            order_id=raw.get("order_id"),
            retcode=raw.get("retcode"),
        )

    def _get(self, path: str) -> Any:
        return self._request("GET", path)

    def _post(self, path: str, payload: dict[str, Any]) -> Any:
        return self._request("POST", path, payload)

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers=self._headers(),
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"MT5 bridge HTTP {exc.code}: {message}") from exc

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.bridge_password:
            headers["X-Bridge-Password"] = self.settings.bridge_password
        return headers
