<div align="center">

# DAX AI Agent Grid Scalper

### AI-Powered MetaTrader 5 Expert Advisor + Python FastAPI Backend

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11](https://img.shields.io/badge/Python-3.11-blue.svg)](https://www.python.org/downloads/)
[![MetaTrader 5](https://img.shields.io/badge/MetaTrader-5-green.svg)](https://www.metatrader5.com/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](https://www.docker.com/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-009688.svg)](https://fastapi.tiangolo.com/)
[![GitHub stars](https://img.shields.io/github/stars/foeed/dax-ai-agent-grid-scalper?style=social)](https://github.com/foeed/dax-ai-agent-grid-scalper)
[![GitHub issues](https://img.shields.io/github/issues/foeed/dax-ai-agent-grid-scalper)](https://github.com/foeed/dax-ai-agent-grid-scalper/issues)
[![Last commit](https://img.shields.io/github/last-commit/foeed/dax-ai-agent-grid-scalper)](https://github.com/foeed/dax-ai-agent-grid-scalper)

**An intelligent algorithmic trading system that combines grid trading with DeepSeek AI LLM analysis, real-time news filtering, and automated risk management for forex scalping on MetaTrader 5.**

[Quick Start](#-quick-start) | [Architecture](#-architecture) | [EA Variants](#-ea-variants) | [Configuration](#%EF%B8%8F-configuration) | [Backtesting](#-backtesting) | [FAQ](#faq) | [Contributing](#-contributing)

</div>

---

## TL;DR for AI & Quick Evaluation

> **What**: Open-source (MIT) MetaTrader 5 Expert Advisor — MQL5 grid trading + optional Python 3.11 FastAPI backend.
> **AI**: DeepSeek LLM generates BUY/SELL/HOLD signals with confidence scores and a 0-100% risk score.
> **News**: Real-time NewsAPI sentiment filtering with automatic caution mode 30 min before high-impact events.
> **Risk**: Circuit breakers (daily loss limit 10%, max drawdown 15%), AI-adjusted position sizing and stop losses.
> **Cost**: Free software. NewsAPI free tier + ~$5 DeepSeek credits lasts months.
> **Setup**: 5 minutes standalone (copy .mq5 → compile → enter API keys). Docker one-liner for the backend.
> **Pairs**: EURUSD, GBPUSD, USDJPY (liquid majors). Best session: London/NY overlap 12:00–16:00 UTC.

---

## Why This Project?

| | DAX AI Grid Scalper | Typical MT5 EA | Manual Trading |
|---|---|---|---|
| **Market analysis** | DeepSeek LLM + news sentiment | Fixed rules only | Human judgment |
| **Adaptability** | Dynamic risk scoring 0-100% | Static parameters | Emotional |
| **News awareness** | Automatic caution mode | None | Manual checking |
| **Operation** | 24/5 automated | 24/5 automated | Limited hours |
| **Risk controls** | AI-adjusted circuit breakers | Basic SL/TP | Discipline-dependent |
| **Source code** | Fully open (MIT) | Usually closed | N/A |
| **Cost** | Free + cheap APIs | $50-$5000+ | Time |

---

## Features

### AI-Powered Trading Engine

| Feature | Description |
|---------|-------------|
| **DeepSeek AI Integration** | LLM-powered market analysis with professional-grade insights |
| **Sentiment Analysis** | Real-time news sentiment scoring for currency pairs |
| **Signal Generation** | AI-driven BUY/SELL/HOLD recommendations with confidence levels |
| **Risk Scoring** | 0-100% dynamic risk assessment based on technical + fundamental data |

### Advanced Grid Trading

| Feature | Description |
|---------|-------------|
| **Breakout Grid System** | Dynamic grid placement based on price action |
| **Smart Position Sizing** | AI-adjusted lot sizes based on current risk score |
| **Adaptive Stop Loss** | ATR-based dynamic stops with AI optimization |
| **Trailing & Break-Even** | Automated profit protection mechanisms |
| **Fibonacci Grid** | Fibonacci-based grid spacing for trend-following |
| **Multiple Timeframes** | M5, M15 support with optimized parameters |

### Real-Time News Intelligence

| Feature | Description |
|---------|-------------|
| **NewsAPI Integration** | Live forex news from 100,000+ sources |
| **Economic Calendar** | High-impact event detection and alerts |
| **News Caution Mode** | Automatic risk reduction 30min before major news |
| **Sentiment Scoring** | NLP-based market sentiment from news analysis |

### Risk Management

| Feature | Description |
|---------|-------------|
| **Circuit Breakers** | Daily loss and drawdown limits with auto-close |
| **AI-Adjusted Limits** | Tighter limits when AI detects elevated risk |
| **Position Size Enforcement** | Maximum risk per trade strictly enforced |
| **Spread Protection** | Dynamic spread filtering during volatility |

---

## Architecture

```
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚                      DAX AI Trading System                       â”‚
â”œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¤
â”‚    MetaTrader 5 (EA)    â”‚       FastAPI Backend (Python)         â”‚
â”‚                         â”‚                                        â”‚
â”‚  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”  â”‚  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”  â”‚
â”‚  â”‚ Grid Trading      â”‚  â”‚  â”‚ DeepSeek AI Service              â”‚  â”‚
â”‚  â”‚ Order Execution   â”‚  â”‚  â”‚ â”œâ”€â”€ Market Analysis               â”‚  â”‚
â”‚  â”‚ Risk Checks       â”‚  â”‚  â”‚ â”œâ”€â”€ Signal Generation             â”‚  â”‚
â”‚  â”‚ Dashboard (HTML)  â”‚  â”‚  â”‚ â””â”€â”€ Risk Scoring                  â”‚  â”‚
â”‚  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜  â”‚  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜  â”‚
â”‚                         â”‚                                        â”‚
â”‚  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”  â”‚  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”  â”‚
â”‚  â”‚ MQ5 Modules       â”‚  â”‚  â”‚ News Service                     â”‚  â”‚
â”‚  â”‚ â”œâ”€â”€ DeepSeekAI    â”‚  â”‚  â”‚ â”œâ”€â”€ NewsAPI Integration           â”‚  â”‚
â”‚  â”‚ â””â”€â”€ NewsAPI       â”‚  â”‚  â”‚ â”œâ”€â”€ Sentiment Analysis            â”‚  â”‚
â”‚  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜  â”‚  â”‚ â””â”€â”€ Economic Calendar             â”‚  â”‚
â”‚                         â”‚  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜  â”‚
â”‚                         â”‚                                        â”‚
â”‚                         â”‚  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”  â”‚
â”‚                         â”‚  â”‚ Risk Management                  â”‚  â”‚
â”‚                         â”‚  â”‚ â”œâ”€â”€ Circuit Breakers              â”‚  â”‚
â”‚                         â”‚  â”‚ â”œâ”€â”€ Position Sizing               â”‚  â”‚
â”‚                         â”‚  â”‚ â””â”€â”€ Dynamic SL/TP                 â”‚  â”‚
â”‚                         â”‚  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜  â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
         â”‚ HTTP (REST)                      â”‚
         â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

### Standalone vs FastAPI Mode

| Mode | How It Works | Best For |
|------|-------------|----------|
| **Standalone** | EA calls DeepSeek + NewsAPI directly via HTTP | Simple setup, single PC |
| **FastAPI** | EA calls Python backend, backend handles AI | Multi-device, advanced risk, scalability |

---

## Project Structure

```
dax-ai-agent-grid-scalper/
â”œâ”€â”€ DeepSeekNewsGridScalper_V2.mq5   # Main standalone EA (800+ lines)
â”œâ”€â”€ Include/
â”‚   â”œâ”€â”€ DeepSeekAI.mqh                # AI integration module
â”‚   â””â”€â”€ NewsAPI.mqh                   # News API integration
â”œâ”€â”€ Config/
â”‚   â””â”€â”€ EA_Config.ini                 # EA configuration
â”œâ”€â”€ Backend/                          # Python FastAPI Backend
â”‚   â”œâ”€â”€ app/
â”‚   â”‚   â”œâ”€â”€ main.py                   # FastAPI entry point
â”‚   â”‚   â”œâ”€â”€ core/                     # Config & database
â”‚   â”‚   â”œâ”€â”€ routers/                  # API route handlers
â”‚   â”‚   â”œâ”€â”€ services/                 # Business logic
â”‚   â”‚   â””â”€â”€ models/                   # Pydantic schemas
â”‚   â”œâ”€â”€ Dockerfile                    # Docker build
â”‚   â”œâ”€â”€ docker-compose.yml            # Docker orchestration
â”‚   â”œâ”€â”€ requirements.txt              # Python dependencies
â”‚   â””â”€â”€ .env.example                  # Environment template
â”œâ”€â”€ MQ5_FastAPI/                      # FastAPI-connected EA variants
â”‚   â”œâ”€â”€ EAFastConnector.mq5           # Main FastAPI EA
â”‚   â”œâ”€â”€ DAX_M5_Standalone.mq5         # M5 standalone
â”‚   â”œâ”€â”€ DAX_FibATR_Trend.mq5          # Fibonacci ATR trend
â”‚   â””â”€â”€ Fibonation_Grid.mq5           # Fibonacci grid
â”œâ”€â”€ docs/
â”‚   â””â”€â”€ index.html                    # Landing page (GitHub Pages)
â””â”€â”€ Scripts/
    â””â”€â”€ TestAPIs.mq5                  # API testing
```

---

## Quick Start

### Standalone Mode (5 minutes)

**1. Get API Keys**

| API | Cost | Sign Up |
|-----|------|---------|
| NewsAPI | **Free** (100 req/day) | [newsapi.org/register](https://newsapi.org/register) |
| DeepSeek AI | ~$5 for months | [platform.deepseek.com](https://platform.deepseek.com/) |

**2. Install in MetaTrader 5**

```bash
# Copy files to MT5
Copy DeepSeekNewsGridScalper_V2.mq5 â†’ MQL5/Experts/
Copy Include/ â†’ MQL5/Include/
```

**3. Compile & Configure**

1. Open MetaEditor, press `F7` to compile
2. Drag EA onto EURUSD chart (H1 or H4)
3. Enter API keys in EA inputs
4. Enable "Allow WebRequest" with these URLs:
   - `https://api.newsapi.org`
   - `https://api.deepseek.com`

**4. Verify**

Check Experts tab for `AI Analysis: ENABLED` and the on-chart dashboard.

### FastAPI Mode (10 minutes)

```bash
# Clone the repo
git clone https://github.com/foeed/dax-ai-agent-grid-scalper.git
cd dax-ai-agent-grid-scalper/Backend

# Setup environment
copy .env.example .env
# Edit .env with your API keys

# Run with Docker (recommended)
docker-compose up -d

# OR run locally
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then configure the EA to point to `http://localhost:8000`.

---

## EA Variants

| EA | Strategy | Timeframe | Mode | Best For |
|----|----------|-----------|------|----------|
| **DeepSeekNewsGridScalper_V2** | Grid + AI + News | M1-H4 | Standalone | Full-featured trading |
| **EAFastConnector** | Grid + Backend | M1-H4 | FastAPI | Scalable setup |
| **DAX_M5_Standalone** | Grid (M5 optimized) | M5 | Standalone | Scalping |
| **DAX_FibATR_Trend** | Fibonacci ATR Trend | M5-M30 | Standalone | Trend following |
| **Fibonation_Grid** | Fibonacci Grid | M5 | Standalone | Grid with Fib spacing |

---

## Configuration

### Risk Profiles

| Profile | Risk/Trade | Daily Loss | Grid Orders | Grid Distance | Suitable For |
|---------|-----------|------------|-------------|---------------|-------------|
| **Conservative** | 1.0% | 5.0% | 1 | 400 | Beginners |
| **Standard** | 2.0% | 10.0% | 2 | 300 | Most traders |
| **Aggressive** | 3.0% | 15.0% | 3 | 250 | Experienced |

### AI Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `UseAIAnalysis` | `true` | Enable DeepSeek AI analysis |
| `UseNewsFilter` | `true` | Enable news-based filtering |
| `AIAnalysisInterval` | `300` | Seconds between AI calls |
| `MaxRiskPerTrade` | `2.0%` | Maximum risk per trade |
| `DynamicPositionSizing` | `true` | AI-adjusted lot sizes |
| `DynamicStopLoss` | `true` | AI-adjusted stop losses |

### Circuit Breakers

| Parameter | Default | Action |
|-----------|---------|--------|
| `MaxDailyLoss` | `10.0%` | Close all positions |
| `MaxDrawdown` | `15.0%` | Emergency stop trading |
| `AIAdjustedLimits` | `true` | Tighter limits when AI risk > 70% |

---

## Risk Management

### AI Risk Scoring

```
Risk Score = Technical (30%) + Volatility (30%) + News (20%) + AI Confidence (20%)

0-30%  â†’ Low Risk    â†’ Full position sizes
30-60% â†’ Medium Risk â†’ Reduced position sizes
60-80% â†’ High Risk   â†’ Minimal trading
80-100% â†’ Extreme     â†’ No new trades
```

### News Protection

- **30 min before** high-impact news â†’ Caution mode activated
- **Position sizes** cut by 50%
- **Spread limits** tightened by 20%
- **Only high-confidence** trades allowed

---

## API Reference (FastAPI Mode)

### Trading Endpoints

```
POST /api/v1/trading/signal      # Get AI trading signal
POST /api/v1/trading/analyze     # Full market analysis
GET  /api/v1/trading/dashboard   # Dashboard data
```

### Risk Management

```
POST /api/v1/risk/assess         # Risk assessment
POST /api/v1/risk/position-size  # Position sizing
POST /api/v1/risk/dynamic-sl     # Dynamic stop loss
POST /api/v1/risk/circuit-breakers  # Circuit breaker check
```

### News

```
GET /api/v1/news/{symbol}              # Get forex news
GET /api/v1/news/{symbol}/sentiment    # Sentiment score
GET /api/v1/news/{symbol}/high-impact  # High impact check
```

Full API docs available at `http://localhost:8000/docs` (Swagger UI).

---

## Backtesting

The system has been backtested across multiple strategies and timeframes:

| Strategy | Timeframe | Period | Key Metrics |
|----------|-----------|--------|-------------|
| Grid Scalper | M5 | 30-90 days | XAUUSD optimization |
| Fibonacci Grid | M5 | 30 days | Fib spacing analysis |
| FibATR Trend | M5 | 30 days | ATR-based entries |

> **Disclaimer**: Past performance does not guarantee future results. Always test on demo accounts first.

---

## Recommended Settings

### Best Pairs

| Tier | Pairs |
|------|-------|
| **Primary** | EURUSD, GBPUSD, USDJPY |
| **Secondary** | EURGBP, AUDUSD, USDCAD |
| **Avoid** | Exotics (USDTRY, USDZAR), minor pairs off-hours |

### Best Sessions

| Session | Time (UTC) | Rating |
|---------|-----------|--------|
| London | 07:00-16:00 | Good |
| New York | 12:00-21:00 | Good |
| **Overlap** | **12:00-16:00** | **Best** |
| Asian | 23:00-08:00 | Avoid |

---

## Roadmap

- [ ] V3: Multi-pair portfolio management
- [ ] V3: Custom indicator integration
- [ ] V3: Web dashboard for monitoring
- [ ] V3: Telegram bot notifications
- [ ] V3: Backtesting module in FastAPI
- [ ] V3: Machine learning signal filtering

---

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## FAQ

<details>
<summary><b>What is DAX AI Agent Grid Scalper?</b></summary>

An open-source MetaTrader 5 Expert Advisor combining breakout grid trading with DeepSeek AI LLM market analysis, real-time NewsAPI news filtering, and automated circuit-breaker risk management, with an optional Python FastAPI backend.
</details>

<details>
<summary><b>How does DeepSeek AI improve grid trading?</b></summary>

DeepSeek AI generates BUY/SELL/HOLD signals with confidence scores, produces a 0-100% risk score from technical + volatility + news data, dynamically adjusts position sizes and stop losses, and blocks new trades during extreme-risk conditions.
</details>

<details>
<summary><b>Is it free to use?</b></summary>

Yes — MIT licensed. Only optional costs: NewsAPI free tier (100 requests/day) and DeepSeek API credits (~$5 lasts months at default settings).
</details>

<details>
<summary><b>What are the system requirements?</b></summary>

Standalone mode: Windows + MetaTrader 5. FastAPI mode additionally needs Python 3.11+ or Docker. A VPS is recommended for 24/5 operation.
</details>

<details>
<summary><b>Which currency pairs work best?</b></summary>

Liquid majors: EURUSD, GBPUSD, USDJPY, EURGBP. Avoid exotics (USDTRY, USDZAR) due to high spreads.
</details>

<details>
<summary><b>How does the news filtering work?</b></summary>

Real-time forex news from NewsAPI with sentiment scoring. 30 minutes before high-impact events, caution mode activates: position sizes cut by 50%, spread filters tightened 20%, only high-confidence trades allowed through.
</details>

<details>
<summary><b>Standalone vs FastAPI mode — which should I choose?</b></summary>

Standalone: EA calls AI/news APIs directly from MT5 — simplest setup for single terminals. FastAPI: Python backend centralizes AI/risk logic — better for scale, customization, and multi-terminal deployments.
</details>

<details>
<summary><b>Can I use it on a real money account?</b></summary>

The software is provided for educational purposes. Always test on demo first, start conservative (1% risk per trade), never trade money you cannot afford to lose. Forex trading carries substantial risk.
</details>

<details>
<summary><b>How much does the DeepSeek API cost?</b></summary>

Very little — about $5 in credits lasts several months at the default 5-minute analysis interval, making DeepSeek one of the cheapest LLM options for algorithmic trading.
</details>

<details>
<summary><b>How do I run the backend with Docker?</b></summary>

`cd Backend`, copy `.env.example` to `.env` and add your API keys, then `docker-compose up -d`. Point the EA's BackendURL input to `http://localhost:8000`.
</details>

---

## Support the Project

If you find this project useful, consider supporting development:

| Network | Currency | Address |
|---------|----------|---------|
| **BSC (BEP20)** | USDT | `0xb13d29622961004b54c15452a233a43215331fe2` |

> You can send any BEP20 token to the address above. Thank you for your support!

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## Disclaimer

This Expert Advisor and all associated code is for **educational and research purposes only**. Trading foreign exchange on margin carries a high level of risk and may not be suitable for all investors. Past performance is not indicative of future results. Always trade responsibly and never risk money you cannot afford to lose. The authors are not responsible for any financial losses incurred from using this software.

---

<div align="center">

**Built with DeepSeek AI, MetaTrader 5, Python FastAPI, and Docker**

[Star this repo](https://github.com/foeed/dax-ai-agent-grid-scalper) | [Report Bug](https://github.com/foeed/dax-ai-agent-grid-scalper/issues) | [Request Feature](https://github.com/foeed/dax-ai-agent-grid-scalper/issues)

</div>
