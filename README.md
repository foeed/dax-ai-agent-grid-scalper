# Trend Scalper AI Bot for MT5 + DeepSeek

Smart trend-scalping scaffold with:

- Deterministic EMA/ATR/RSI trend strategy.
- Optional DeepSeek LLM risk veto using direct HTTPS calls.
- Recommended hybrid architecture: Docker signal service + thin MT5 `.mq5` executor EA.
- Safe defaults: paper mode and `DRY_RUN=true`.

> This is engineering infrastructure, not a profit guarantee. Test on demo first.

## Recommended Architecture

```text
MT5 chart
  -> TrendScalperEA.mq5
  -> HTTP WebRequest
  -> Docker signal-service
  -> strategy + DeepSeek veto + risk gates
  -> BUY / SELL / HOLD response
  -> EA executes order in MT5
```

Why this mode is best:

- Docker keeps AI logic, tests, logs, and dependencies isolated.
- MT5 only runs the thin executor EA.
- No fragile Windows Python/MT5 runtime is needed for live execution.
- The EA can be attached/imported like a normal MT5 Expert Advisor.

## Quick Paper Test

```powershell
cd D:\DAX
copy .env.example .env
docker compose run --rm bot python -m trend_scalper --once
```

## Start Docker Signal Service

```powershell
cd D:\DAX
docker compose up signal-service
```

The service listens on:

```text
http://127.0.0.1:8766
```

Open the monitoring dashboard:

```text
http://127.0.0.1:8766/dashboard
```

If `SIGNAL_TOKEN` is set, paste it into the dashboard token box. The dashboard shows service status, dry-run mode, BUY/SELL/HOLD counts, daily risk state, and recent signal/trade events.

By default `DASHBOARD_AUTO_TOKEN=true`, so the local dashboard auto-fills the API token from the service. Keep the dashboard bound to your own machine; do not expose port `8766` to the internet.

The dashboard can edit runtime values for:

- Core controls: `Mode`, `Dry Run`, `LLM`, `LLM Fail Closed`
- Strategy controls: symbol, timeframe, bars, EMA/ATR/RSI, stop/target multipliers, confidence
- Risk controls: risk %, daily loss %, max trades/day, LLM score/timeout
- MT5 EA controls: sizing mode, lots/max-lots, max positions, spread caps, cooldown, magic, deviation, one-trade-per-bar

These dashboard edits are saved to `data/dashboard_settings.json`. They affect the Docker signal service immediately, including strategy/risk/LLM analysis parameters. The MT5 EA refreshes execution settings from `/api/runtime-settings`, so dashboard changes apply after the EA refresh interval. In `fixed lots` sizing, `Lots / Max Lots` is the exact order volume. In `risk %` sizing, `Risk %` calculates volume from account equity and stop-loss distance, while `Lots / Max Lots` acts as the maximum cap. If the broker minimum/step rounds the result, small risk changes may still normalize to the same lot. `LLM Fail Closed=true` blocks trades when DeepSeek times out; `false` lets deterministic signals continue if DeepSeek is unavailable. Keep dry-run enabled until you are ready on demo.

When `LLM=true`, the dashboard switches to LLM Expert Autopilot: Asset, Live Control, Strategy, and MT5 EA panels are hidden. The only visible user controls are `Risk %`, `Daily Loss %`, `Max Trades/Day`, `LLM Min Score`, and `LLM Timeout`. The backend uses those five values as the authority, then auto-tunes timeframe, bars, EMA/ATR/RSI, stops, confidence, spread limits, cooldown, risk sizing, max positions, and request timeout. `Daily Loss %` and `Max Trades/Day` are always user-controlled and are never overwritten by autopilot. Set `LLM=false` via settings/API/config to return to manual advanced controls.

If needed, edit `.env`:

```env
SIGNAL_HOST=0.0.0.0
SIGNAL_PORT=8766
SIGNAL_TOKEN=change-me-long-random-token
USE_LLM=false
DEEPSEEK_API_KEY=
EVENT_LOG_PATH=data/trade_events.jsonl
DASHBOARD_SETTINGS_PATH=data/dashboard_settings.json
DASHBOARD_EVENTS_LIMIT=100
DASHBOARD_AUTO_TOKEN=true
```

## Import EA Into MT5

1. Open MT5.
2. Click `File` -> `Open Data Folder`.
3. Go to `MQL5\Experts`.
4. Copy this file into that folder:

```text
D:\DAX\mql5\Experts\TrendScalperEA.mq5
```

5. Open MetaEditor.
6. Compile `TrendScalperEA.mq5`.
7. In MT5, open `Tools` -> `Options` -> `Expert Advisors`.
8. Enable `Allow WebRequest for listed URL`.
9. Add:

```text
http://127.0.0.1:8766
```

10. Refresh `Navigator` -> `Expert Advisors`.
11. Drag `TrendScalperEA` onto any demo crypto chart, for example `BTCUSD`, `ETHUSD`, or your broker's crypto symbol name.
12. Keep EA input `DryRun=true` first.
13. If `LLM=true`, keep EA input `RequestTimeoutMs=30000` or higher so DeepSeek has time to answer.
14. If the Experts log still shows `RequestTimeoutMs=5000`, remove the EA from the chart and attach it again so MT5 loads the updated input defaults.

Only after demo testing, change the EA input:

```text
DryRun=false
```

### MT5 Strategy Tester

MT5 blocks `WebRequest` inside Strategy Tester, which produces error `4014`. The EA now detects tester mode when `UseLocalBacktest=true` and runs the same EMA/ATR/RSI trend logic locally instead of calling the Docker signal service. Dashboard/LLM runtime updates are live-trading features; tester runs use the EA input defaults for strategy/risk parameters.

## DeepSeek Setup

Edit `.env`:

```env
USE_LLM=true
DEEPSEEK_API_KEY=your_deepseek_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

DeepSeek is a veto/risk layer only. It does not invent orders; it can approve or block deterministic strategy signals.

## Key Risk Settings

```env
FIXED_LOT=0.01
RISK_PERCENT=0.25
MAX_LOT=0.10
MAX_SPREAD_POINTS=0
MAX_POSITIONS=1
MAX_TRADES_PER_DAY=8
DAILY_LOSS_LIMIT_PERCENT=2.0
COOLDOWN_SECONDS=180
```

In hybrid EA mode, the EA input `Lots` controls execution size and is normalized to the broker's symbol min/max/step. Keep it tiny on demo. `MAX_SPREAD_POINTS=0` disables the Docker-side spread cap, which is useful for crypto symbols with large point spreads.

## Strategy Settings

```env
EMA_FAST=8
EMA_SLOW=21
EMA_TREND=55
ATR_PERIOD=14
RSI_PERIOD=14
SL_ATR_MULTIPLIER=1.3
TP_ATR_MULTIPLIER=1.8
MIN_SIGNAL_CONFIDENCE=0.62
```

The strategy uses the last completed candle.

## Validation

Run everything:

```powershell
.\scripts\run_all_tests.ps1
```

Manual commands:

```powershell
$env:PYTHONPATH=".\src"; python -m compileall src tests
$env:PYTHONPATH=".\src"; python -m unittest discover -s tests
$env:PYTHONPATH=".\src"; python -m trend_scalper --check --env-file .env.example
docker compose run --rm signal-service python -m unittest discover -s tests
docker compose run --rm bot python -m trend_scalper --once
```

## Legacy Modes

Native Windows Python mode:

```powershell
.\scripts\run_native_windows.ps1 -Once
```

Docker bot + Windows MT5 bridge mode:

```powershell
.\scripts\run_native_windows.ps1 -Bridge
docker compose run --rm bot python -m trend_scalper --once
```

Recommended live path remains Docker signal service + `TrendScalperEA.mq5`.

## Project Layout

```text
mql5/Experts/TrendScalperEA.mq5  MT5 executor EA
src/trend_scalper/signal_service.py  Docker HTTP brain for EA
src/trend_scalper/strategy.py        EMA/ATR/RSI trend scalper
src/trend_scalper/llm_filter.py      DeepSeek JSON risk review
src/trend_scalper/risk.py            daily loss, cooldown, trade-count gates
src/trend_scalper/monitoring.py      dashboard status and event log store
tests/                              unit + e2e tests
```

## Important Notes

- Start with `DryRun=true`, `DRY_RUN=true`, and a demo account.
- Do not expose the signal service or MT5 bridge to the internet.
- Keep `SIGNAL_TOKEN` strong if binding beyond localhost.
- Add only `http://127.0.0.1:8766` to MT5 WebRequest unless you deliberately change the service host.
- The EA uses the chart symbol (`_Symbol`), so it can run on any broker crypto symbol that has enough bars and allows automated trading.
- Dashboard event logs are written to `data/trade_events.jsonl`.
- Dashboard runtime edits are written to `data/dashboard_settings.json`.
