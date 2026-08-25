# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-08-25

### Added
- **DeepSeek AI integration**: LLM-powered market analysis with BUY/SELL/HOLD signal generation, confidence scoring, and 0-100% dynamic risk assessment (`DeepSeekAI.mqh`, `deepseek_service.py`)
- **NewsAPI intelligence**: Real-time forex news sentiment scoring, high-impact event detection, and automatic pre-news caution mode (30 min before events, -50% position size, +20% spread tightening) (`NewsAPI.mqh`, `news_service.py`)
- **Grid trading engine**: Breakout-based grid with configurable distance/orders, trailing stops, and break-even protection
- **FastAPI backend**: Python 3.11 REST API with 15+ endpoints covering trading signals, risk assessment, position sizing, dynamic SL/TP, circuit breakers, and news queries (`/docs` Swagger UI included)
- **Circuit breaker risk management**: Daily loss limit (default 10%), max drawdown halt (default 15%), AI-adjusted limits when risk score > 70%
- **EA variants**: `DeepSeekNewsGridScalper_V2` (flagship standalone), `EAFastConnector` (FastAPI mode), `DAX_M5_Standalone` (M5 scalping), `DAX_FibATR_Trend` (Fibonacci ATR trend), `Fibonation_Grid` (Fibonacci-spaced grid)
- **Docker support**: Production-ready Dockerfile + docker-compose.yml with health checks
- **On-chart dashboard**: Real-time engine status, balance/equity, AI risk/confidence/signal, news status, RSI/ATR/EMA indicators
- **SEO landing page** with FAQ, structured data (SoftwareApplication, FAQPage, HowTo schemas), Open Graph, and `llms.txt` / `llms-full.txt` for LLM discoverability
- **CI pipeline**: GitHub Actions with Python lint (ruff), Docker build test, MQL5 source validation

### Risk Management Details
- Micro account support: auto-reduced SL to 250 pts for small balances, 25% DD limit under minimum balance threshold
- Drawdown breaker: peak-equity tracking, daily reset, halts new trading instead of force-closing positions
- Grid overtrading fix: 30-minute cooldown between grid activations matching Python backtest logic

### Fixed
- Fibonacci array out-of-range error (size 6 → 7)
- Fibonacci EA optimized SL/TP calculation (atr*0.9*8, 1x/2x spacing)
- Grid overtrading via time-based cooldown

## [1.0.0] - Initial Development

### Added
- Basic grid trading engine
- Simple risk controls
- Standard trailing stops

[2.0.0]: https://github.com/foeed/dax-ai-agent-grid-scalper/releases/tag/v2.0.0
