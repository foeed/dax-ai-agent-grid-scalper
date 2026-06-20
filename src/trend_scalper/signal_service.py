from __future__ import annotations

import argparse
import json
import logging
import math
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import Settings, load_settings, validate_settings
from .llm_filter import DeepSeekRiskFilter
from .models import AccountSnapshot, Rate
from .monitoring import EventStore, RuntimeSettingsStore
from .risk import RiskManager
from .strategy import TrendScalperStrategy

logger = logging.getLogger(__name__)


class SignalEngine:
    def __init__(
        self,
        settings: Settings,
        event_store: EventStore | None = None,
        runtime_settings: RuntimeSettingsStore | None = None,
    ) -> None:
        self.settings = settings
        self.runtime_settings = runtime_settings or RuntimeSettingsStore(settings)
        self.events = event_store or EventStore(settings, self.runtime_settings)
        self.strategy = TrendScalperStrategy(settings)
        self.risk = RiskManager(settings)
        self.llm: DeepSeekRiskFilter | None = None

    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self._evaluate(payload)
        self.events.append_signal(payload, result)
        return result

    def _evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        rates = self._rates(payload)
        point = float(payload.get("point") or self._default_point())
        account = self._account(payload.get("account", {}))
        spread_points = float(payload.get("spread_points", 0.0))
        positions_count = int(payload.get("positions_count", 0))

        allowed, risk_reason = self.risk.can_trade(account)
        if not allowed:
            return self._hold(risk_reason)

        if self.settings.max_spread_points > 0 and spread_points > self.settings.max_spread_points:
            return self._hold(
                f"Spread blocked trade: {spread_points:.1f} > {self.settings.max_spread_points:.1f}"
            )

        if positions_count >= self.settings.max_positions:
            return self._hold(
                f"Position cap blocked trade: {positions_count} >= {self.settings.max_positions}"
            )

        signal = self.strategy.analyze(rates, point)
        if not signal.is_trade:
            return self._hold(signal.reason, signal.confidence)

        runtime = self.runtime_settings.effective()
        llm_filter = self._llm_filter(runtime)
        if llm_filter:
            decision = llm_filter.review(
                signal,
                rates,
                account,
                spread_points,
                positions_count,
                symbol=str(payload.get("symbol", self.settings.symbol)),
                timeframe=str(payload.get("timeframe", self.settings.timeframe)),
                fail_closed=bool(runtime["llm_fail_closed"]),
            )
            if not decision.approved or decision.score < self.settings.llm_min_score:
                return self._hold(f"DeepSeek blocked: {decision.reason}", decision.score)

        sl_points = max(1, int(math.ceil(signal.sl_distance / point)))
        tp_points = max(1, int(math.ceil(signal.tp_distance / point)))
        return {
            "action": signal.action,
            "confidence": signal.confidence,
            "reason": signal.reason,
            "sl_distance": signal.sl_distance,
            "tp_distance": signal.tp_distance,
            "sl_points": sl_points,
            "tp_points": tp_points,
            "magic": self.settings.magic_number,
            "metadata": signal.metadata,
        }

    def _llm_filter(self, runtime: dict[str, Any] | None = None) -> DeepSeekRiskFilter | None:
        effective = runtime or self.runtime_settings.effective()
        if not effective["use_llm"]:
            return None
        if self.llm is None:
            self.llm = DeepSeekRiskFilter(self.settings)
        return self.llm

    def record_trade_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        success = bool(payload.get("success", False))
        if success:
            self.risk.record_trade(self._account(payload.get("account", {})))
        self.events.append_trade_result(payload, success)
        return {"ok": True, "recorded": success}

    def _rates(self, payload: dict[str, Any]) -> list[Rate]:
        rates = payload.get("rates")
        if not isinstance(rates, list):
            raise ValueError("Payload must include a rates list")

        normalized: list[Rate] = []
        for row in rates:
            if not isinstance(row, dict):
                raise ValueError("Each rate must be an object")
            normalized.append(
                {
                    **row,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                }
            )
        return normalized

    def _account(self, raw: dict[str, Any]) -> AccountSnapshot:
        return AccountSnapshot(
            balance=float(raw.get("balance", 10_000.0)),
            equity=float(raw.get("equity", raw.get("balance", 10_000.0))),
            currency=str(raw.get("currency", "USD")),
        )

    def _default_point(self) -> float:
        return 0.01 if self.settings.symbol.upper().startswith("XAU") else 0.00001

    def _hold(self, reason: str, confidence: float = 0.0) -> dict[str, Any]:
        return {
            "action": "HOLD",
            "confidence": confidence,
            "reason": reason,
            "sl_distance": 0.0,
            "tp_distance": 0.0,
            "sl_points": 0,
            "tp_points": 0,
            "magic": self.settings.magic_number,
            "metadata": {},
        }


class SignalContext:
    def __init__(
        self,
        engine: SignalEngine,
        token: str,
        event_store: EventStore,
        runtime_settings: RuntimeSettingsStore,
    ) -> None:
        self.engine = engine
        self.token = token
        self.events = event_store
        self.runtime_settings = runtime_settings


context: SignalContext | None = None


class SignalHandler(BaseHTTPRequestHandler):
    server_version = "TrendScalperSignalService/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send({"ok": True})
            return
        if parsed.path in {"/", "/dashboard"}:
            token = context.token if context and context.engine.settings.dashboard_auto_token else ""
            self._send_html(dashboard_html(token))
            return
        if parsed.path == "/api/status":
            if not self._authorized(context.token if context else ""):
                self._send({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                return
            self._send(context.events.status() if context else {"ok": False})
            return
        if parsed.path == "/api/events":
            if not self._authorized(context.token if context else ""):
                self._send({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                return
            query = parse_qs(parsed.query)
            limit = int(query.get("limit", ["100"])[0] or 100)
            self._send({"events": context.events.recent(limit) if context else []})
            return
        if parsed.path == "/api/settings":
            if not self._authorized(context.token if context else ""):
                self._send({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                return
            self._send(
                {
                    "settings": context.runtime_settings.effective() if context else {},
                    "overrides": context.runtime_settings.get_overrides() if context else {},
                }
            )
            return
        self._send({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        try:
            if context is None:
                self._send({"error": "service not initialized"}, HTTPStatus.SERVICE_UNAVAILABLE)
                return
            if not self._authorized(context.token):
                self._send({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                return

            payload = self._read_json()
            if self.path == "/signal":
                self._send(context.engine.evaluate(payload))
                return
            if self.path == "/trade-result":
                self._send(context.engine.record_trade_result(payload))
                return
            if self.path == "/api/settings":
                self._send({"settings": context.runtime_settings.update(payload)})
                return
            self._send({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            logger.exception("Signal request failed: %s", exc)
            self._send({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _authorized(self, token: str) -> bool:
        if not token:
            return True
        auth_ok = self.headers.get("Authorization") == f"Bearer {token}"
        header_ok = self.headers.get("X-Signal-Token") == token
        return auth_ok or header_ok

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _send(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, default=str, separators=(",", ":")).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = html.encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def dashboard_html(auto_token: str = "") -> str:
    return DASHBOARD_HTML_TEMPLATE.replace("__AUTO_SIGNAL_TOKEN__", json.dumps(auto_token))


DASHBOARD_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Trend Scalper Monitor</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, Segoe UI, Arial, sans-serif; }
    body { margin: 0; background: #080b12; color: #e8eefc; }
    header { padding: 24px; border-bottom: 1px solid #1f2937; background: linear-gradient(135deg,#111827,#0f172a); }
    h1 { margin: 0 0 8px; font-size: 26px; }
    main { padding: 20px; display: grid; gap: 18px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit,minmax(190px,1fr)); gap: 12px; }
    .card, .panel { background: #111827; border: 1px solid #263244; border-radius: 14px; padding: 16px; box-shadow: 0 10px 30px #0004; }
    .label { color: #94a3b8; font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }
    .value { font-size: 24px; font-weight: 700; margin-top: 6px; }
    input, select { background: #0b1220; color: #e8eefc; border: 1px solid #334155; border-radius: 10px; padding: 10px; min-width: 160px; }
    button { background: #2563eb; color: white; border: 0; border-radius: 10px; padding: 10px 14px; cursor: pointer; }
    .row { display: flex; flex-wrap: wrap; gap: 10px; align-items: end; }
    .field { display: grid; gap: 6px; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { text-align: left; border-bottom: 1px solid #263244; padding: 10px 8px; vertical-align: top; }
    th { color: #93c5fd; position: sticky; top: 0; background: #111827; }
    .BUY { color: #22c55e; font-weight: 700; }
    .SELL { color: #ef4444; font-weight: 700; }
    .HOLD { color: #f59e0b; font-weight: 700; }
    .muted { color: #94a3b8; }
    .error { color: #fca5a5; }
  </style>
</head>
<body>
  <header>
    <h1>Trend Scalper Monitor</h1>
    <div class="muted">Live dashboard for the Docker signal service and MT5 executor EA.</div>
  </header>
  <main>
    <section class="panel">
      <div class="label">Signal Token</div>
      <div class="row">
        <input id="token" type="password" placeholder="Paste SIGNAL_TOKEN if API is protected">
        <button onclick="saveToken()">Save Token</button>
        <button onclick="refresh()">Refresh</button>
      </div>
      <span id="message" class="muted"></span>
    </section>
    <section class="panel">
      <h2>Edit Agent Runtime</h2>
      <div class="row">
        <label class="field"><span class="label">Mode</span>
          <select id="editMode">
            <option value="paper">paper</option>
            <option value="live">live</option>
            <option value="bridge">bridge</option>
          </select>
        </label>
        <label class="field"><span class="label">Dry Run</span>
          <select id="editDryRun">
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
        </label>
        <label class="field"><span class="label">LLM</span>
          <select id="editLlm">
            <option value="false">false</option>
            <option value="true">true</option>
          </select>
        </label>
        <label class="field"><span class="label">LLM Fail Closed</span>
          <select id="editLlmFailClosed">
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
        </label>
        <button onclick="saveRuntimeSettings()">Save Runtime</button>
      </div>
      <p class="muted">Fail Closed=true blocks trades when DeepSeek times out. Fail Closed=false lets deterministic signals continue if DeepSeek is unavailable. MT5 order execution still depends on the EA input <code>DryRun</code>.</p>
    </section>
    <section class="grid" id="cards"></section>
    <section class="panel">
      <h2>Recent Events</h2>
      <table>
        <thead><tr><th>Time</th><th>Type</th><th>Symbol</th><th>Action</th><th>Confidence</th><th>Spread</th><th>Reason</th></tr></thead>
        <tbody id="events"><tr><td colspan="7" class="muted">Loading...</td></tr></tbody>
      </table>
    </section>
  </main>
  <script>
    window.__SIGNAL_TOKEN__ = __AUTO_SIGNAL_TOKEN__;
    const tokenInput = document.getElementById('token');
    tokenInput.value = localStorage.getItem('signalToken') || window.__SIGNAL_TOKEN__ || '';
    function headers() {
      const token = tokenInput.value.trim();
      return token ? {'Authorization': `Bearer ${token}`} : {};
    }
    function saveToken() {
      localStorage.setItem('signalToken', tokenInput.value.trim());
      refresh();
    }
    async function getJson(path) {
      const response = await fetch(path, {headers: headers()});
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      return response.json();
    }
    async function postJson(path, payload) {
      const response = await fetch(path, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', ...headers()},
        body: JSON.stringify(payload)
      });
      if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
      return response.json();
    }
    function boolString(value) { return value ? 'true' : 'false'; }
    function card(label, value) {
      return `<div class="card"><div class="label">${label}</div><div class="value">${value ?? '-'}</div></div>`;
    }
    async function saveRuntimeSettings() {
      try {
        await postJson('/api/settings', {
          trading_mode: document.getElementById('editMode').value,
          dry_run: document.getElementById('editDryRun').value === 'true',
          use_llm: document.getElementById('editLlm').value === 'true',
          llm_fail_closed: document.getElementById('editLlmFailClosed').value === 'true'
        });
        await refresh();
      } catch (error) {
        const msg = document.getElementById('message');
        msg.textContent = `Save failed: ${error.message}`;
        msg.className = 'error';
      }
    }
    async function refresh() {
      const msg = document.getElementById('message');
      try {
        const status = await getJson('/api/status');
        const events = await getJson('/api/events?limit=100');
        const settings = status.settings || {};
        const summary = status.summary || {};
        const risk = status.risk_state || {};
        document.getElementById('editMode').value = settings.trading_mode || 'paper';
        document.getElementById('editDryRun').value = boolString(settings.dry_run);
        document.getElementById('editLlm').value = boolString(settings.use_llm);
        document.getElementById('editLlmFailClosed').value = boolString(settings.llm_fail_closed);
        document.getElementById('cards').innerHTML = [
          card('Service', status.ok ? 'OK' : 'DOWN'),
          card('Mode', settings.trading_mode),
          card('Dry Run', settings.dry_run),
          card('LLM', settings.use_llm),
          card('LLM Fail Closed', settings.llm_fail_closed),
          card('Signals', summary.events_count || 0),
          card('BUY', (summary.actions || {}).BUY || 0),
          card('SELL', (summary.actions || {}).SELL || 0),
          card('HOLD', (summary.actions || {}).HOLD || 0),
          card('Trades Today', risk.trades_count ?? 0),
          card('Trade Success', summary.trade_success || 0)
        ].join('');
        document.getElementById('events').innerHTML = (events.events || []).map(row => `
          <tr>
            <td>${row.ts || ''}</td><td>${row.type || ''}</td><td>${row.symbol || ''}</td>
            <td class="${row.action || ''}">${row.action || ''}</td><td>${row.confidence ?? ''}</td>
            <td>${row.spread_points ?? ''}</td><td>${row.reason || ''}</td>
          </tr>`).join('') || '<tr><td colspan="7" class="muted">No events yet</td></tr>';
        msg.textContent = `Updated ${new Date().toLocaleTimeString()}`;
        msg.className = 'muted';
      } catch (error) {
        msg.textContent = `Dashboard API error: ${error.message}`;
        msg.className = 'error';
      }
    }
    refresh();
    setInterval(refresh, 5000);
  </script>
</body>
</html>
"""


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Trend Scalper HTTP signal service")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args(argv)

    settings = load_settings(args.env_file)
    configure_logging(settings.log_level)
    errors = validate_settings(settings)
    if errors:
        for error in errors:
            logger.warning("Config: %s", error)
        if settings.use_llm:
            return 2

    runtime_settings = RuntimeSettingsStore(settings)
    event_store = EventStore(settings, runtime_settings)

    global context
    context = SignalContext(
        SignalEngine(settings, event_store, runtime_settings),
        settings.signal_token,
        event_store,
        runtime_settings,
    )

    server = ThreadingHTTPServer((settings.signal_host, settings.signal_port), SignalHandler)
    logger.info("Signal service listening on http://%s:%s", settings.signal_host, settings.signal_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping signal service")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
