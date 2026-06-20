from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import load_settings
from .models import AccountSnapshot, TradeSignal
from .mt5_client import Mt5Client

logger = logging.getLogger(__name__)


class BridgeContext:
    def __init__(self, client: Mt5Client, token: str) -> None:
        self.client = client
        self.token = token


context: BridgeContext | None = None


class Mt5BridgeHandler(BaseHTTPRequestHandler):
    server_version = "TrendScalperMT5Bridge/0.1"

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _handle(self, method: str) -> None:
        try:
            if context is None:
                self._send({"error": "bridge not initialized"}, HTTPStatus.SERVICE_UNAVAILABLE)
                return
            if not self._authorized(context.token):
                self._send({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                return

            parsed = urlparse(self.path)
            if method == "GET":
                self._handle_get(parsed.path, parse_qs(parsed.query), context.client)
            elif method == "POST":
                self._handle_post(parsed.path, context.client)
            else:
                self._send({"error": "method not allowed"}, HTTPStatus.METHOD_NOT_ALLOWED)
        except Exception as exc:
            logger.exception("Bridge request failed: %s", exc)
            self._send({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_get(self, path: str, query: dict[str, list[str]], client: Mt5Client) -> None:
        if path == "/health":
            self._send({"ok": True})
            return
        if path == "/account":
            self._send(asdict(client.get_account_snapshot()))
            return
        if path == "/positions":
            self._send({"positions": [asdict(position) for position in client.get_positions()]})
            return
        if path == "/spread":
            self._send({"spread_points": client.spread_points()})
            return
        if path == "/point":
            self._send({"point": client.point()})
            return
        if path == "/rates":
            rates = client.get_rates()
            bars = int(query.get("bars", ["0"])[0] or 0)
            if bars > 0:
                rates = rates[-bars:]
            self._send({"rates": rates})
            return
        self._send({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def _handle_post(self, path: str, client: Mt5Client) -> None:
        payload = self._read_json()
        if path == "/volume":
            signal_value = self._signal_from_payload(payload["signal"])
            account = AccountSnapshot(**payload["account"])
            volume = client.calculate_volume(signal_value, account)
            self._send({"volume": volume})
            return
        if path == "/order":
            signal_value = self._signal_from_payload(payload["signal"])
            volume = float(payload["volume"])
            result = client.place_order(signal_value, volume)
            self._send(asdict(result))
            return
        self._send({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def _signal_from_payload(self, payload: dict[str, Any]) -> TradeSignal:
        return TradeSignal(
            action=payload["action"],
            confidence=float(payload["confidence"]),
            reason=str(payload["reason"]),
            sl_distance=float(payload["sl_distance"]),
            tp_distance=float(payload["tp_distance"]),
            metadata=dict(payload.get("metadata", {})),
        )

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _authorized(self, token: str) -> bool:
        if not token:
            return True
        return self.headers.get("Authorization") == f"Bearer {token}"

    def _send(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local MT5 HTTP bridge for Docker bot mode")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args(argv)

    settings = load_settings(args.env_file)
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    client = Mt5Client(settings)
    client.connect()

    global context
    context = BridgeContext(client, settings.bridge_token)

    server = ThreadingHTTPServer((settings.bridge_host, settings.bridge_port), Mt5BridgeHandler)
    logger.info("MT5 bridge listening on http://%s:%s", settings.bridge_host, settings.bridge_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping MT5 bridge")
    finally:
        server.server_close()
        client.shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
