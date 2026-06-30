# DAX V2: FastAPI + MQ5 Architecture

## AI-Powered Grid Trading System

A production-ready trading system combining FastAPI backend with MetaTrader 5 Expert Advisor.

---

## 🏗️ Architecture

```
┌─────────────────────┐         ┌─────────────────────────┐
│   MetaTrader 5      │  HTTP   │    FastAPI Backend       │
│   (MQ5 EA)          │◄───────►│    (Python 3.11)        │
│                     │         │                         │
│  - Grid Trading     │         │  - DeepSeek AI          │
│  - Order Execution  │         │  - News API             │
│  - Risk Checks      │         │  - Risk Management      │
│  - Dashboard        │         │  - Position Sizing      │
└─────────────────────┘         └─────────────────────────┘
```

---

## 📁 Project Structure

```
DAX_V2/
├── Backend/                    # FastAPI Backend
│   ├── app/
│   │   ├── main.py            # FastAPI application
│   │   ├── core/
│   │   │   ├── config.py      # Settings
│   │   │   └── database.py    # Database manager
│   │   ├── routers/
│   │   │   ├── trading.py     # Trading endpoints
│   │   │   ├── analysis.py    # AI analysis
│   │   │   ├── news.py        # News endpoints
│   │   │   └── risk.py        # Risk management
│   │   ├── services/
│   │   │   ├── deepseek_service.py  # DeepSeek AI
│   │   │   ├── news_service.py      # News API
│   │   │   ├── risk_service.py      # Risk calculations
│   │   │   └── scheduler.py         # Background tasks
│   │   └── models/
│   │       └── schemas.py     # Pydantic models
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── requirements.txt
│   └── start.bat
│
├── MQ5_FastAPI/               # MetaTrader 5 EA
│   └── EAFastConnector.mq5   # Main EA file
│
├── Include/                   # Standalone MQ5 modules
│   ├── NewsAPI.mqh
│   └── DeepSeekAI.mqh
│
└── README_FastAPI.md          # This file
```

---

## 🚀 Quick Start

### Option 1: Docker (Recommended)

```bash
# 1. Clone/navigate to DAX_V2/Backend
cd DAX_V2/Backend

# 2. Create .env file
copy .env.example .env

# 3. Edit .env with your API keys
notepad .env

# 4. Run with Docker Compose
docker-compose up -d

# 5. Check status
docker-compose ps
```

### Option 2: Local Python

```bash
# 1. Navigate to backend
cd DAX_V2/Backend

# 2. Run start script
start.bat
```

### Option 3: Manual Setup

```bash
# 1. Create virtual environment
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set environment variables
set DEEPSEEK_API_KEY=your_key
set NEWS_API_KEY=your_key

# 4. Run server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 🔑 API Keys Setup

### DeepSeek API
1. Go to https://platform.deepseek.com/
2. Create account
3. Add credits ($5 lasts months)
4. Copy API key

### NewsAPI (Free)
1. Go to https://newsapi.org/register
2. Sign up
3. Copy API key
4. Free tier: 100 requests/day

---

## 📡 API Endpoints

### Trading
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/trading/signal` | Get AI trading signal |
| POST | `/api/v1/trading/analyze` | Full market analysis |
| POST | `/api/v1/trading/positions/update` | Update positions |
| GET | `/api/v1/trading/dashboard` | Dashboard data |

### Risk Management
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/risk/assess` | Risk assessment |
| POST | `/api/v1/risk/position-size` | Position sizing |
| POST | `/api/v1/risk/dynamic-sl` | Dynamic stop loss |
| POST | `/api/v1/risk/dynamic-tp` | Take profit calc |
| POST | `/api/v1/risk/circuit-breakers` | Check circuit breakers |

### News
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/news/{symbol}` | Get forex news |
| GET | `/api/v1/news/{symbol}/sentiment` | Sentiment score |
| GET | `/api/v1/news/{symbol}/high-impact` | High impact check |

### System
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Root endpoint |
| GET | `/health` | Health check |
| POST | `/api/v1/mq5/connect` | MQ5 registration |

---

## 📊 MQ5 EA Setup

### 1. Copy EA File
Copy `MQ5_FastAPI/EAFastConnector.mq5` to:
```
C:\Users\[You]\AppData\Roaming\MetaQuotes\Terminal\[ID]\MQL5\Experts\
```

### 2. Compile in MetaEditor
- Open MetaEditor
- Open `EAFastConnector.mq5`
- Press F7 to compile

### 3. Configure EA
- Drag EA to chart (EURUSD H1 recommended)
- Set backend URL: `http://localhost:8000`
- Enable "Allow Algo Trading"
- Enable "Allow WebRequest"

### 4. Add Backend URL
In MetaTrader:
- Tools → Options → Expert Advisors
- Check "Allow WebRequest for listed URL"
- Add: `http://localhost:8000`

---

## 🔧 Configuration

### Backend (.env)
```env
DEEPSEEK_API_KEY=sk-xxxxx
NEWS_API_KEY=xxxxx
DEBUG=false
MAX_RISK_PER_TRADE=0.02
```

### MQ5 EA Inputs
| Parameter | Default | Description |
|-----------|---------|-------------|
| BackendURL | http://localhost:8000 | Backend address |
| UseBackend | true | Enable backend |
| AnalysisInterval | 60 | Seconds between calls |
| GridDistance | 300 | Grid spacing (points) |
| MaxRiskPerTrade | 2.0% | Max risk per trade |

---

## 🛡️ Risk Management Features

### AI-Driven Risk Scoring
- Technical analysis risk (30%)
- Volatility risk (30%)
- News risk (20%)
- AI confidence risk (20%)

### Circuit Breakers
- Daily loss limit (default 10%)
- Drawdown limit (default 15%)
- Automatic position closure

### Dynamic Adjustments
- Position size based on risk score
- Stop loss based on volatility
- Take profit based on risk-reward ratio

---

## 📈 API Response Example

### Trading Signal
```json
{
  "signal": "BUY",
  "risk_score": 0.35,
  "confidence": 0.78,
  "suggested_volume": 0.05,
  "suggested_sl": 1.08200,
  "suggested_tp": 1.09100,
  "risk_level": "MEDIUM",
  "news_caution": false
}
```

### Risk Assessment
```json
{
  "risk_score": 0.42,
  "risk_level": "MEDIUM",
  "max_allowed_volume": 0.08,
  "recommended_sl_distance": 0.00150,
  "news_risk": 0.2,
  "technical_risk": 0.4,
  "volatility_risk": 0.3,
  "warnings": []
}
```

---

## 🔍 Testing

### Test Backend
```bash
# Health check
curl http://localhost:8000/health

# Get signal
curl -X POST http://localhost:8000/api/v1/trading/signal \
  -H "Content-Type: application/json" \
  -d '{"symbol":"EURUSD","bid":1.08500,"ask":1.08510,"spread":10}'
```

### Test MQ5 Connection
1. Open MetaTrader
2. Check Experts tab for "Backend connected"
3. Verify dashboard shows on chart

---

## 🐛 Troubleshooting

### Backend won't start
- Check Python version (3.11+)
- Verify all dependencies installed
- Check port 8000 not in use

### MQ5 can't connect
- Verify backend running
- Check URL in EA settings
- Ensure WebRequest enabled
- Check firewall settings

### No AI analysis
- Verify API keys in .env
- Check backend logs
- Test with curl

---

## 📝 Development

### Add New Endpoint
1. Create router in `app/routers/`
2. Add to `main.py` includes
3. Implement service logic

### Add New Service
1. Create service in `app/services/`
2. Import in routers
3. Add to dependency injection

---

## ⚠️ Disclaimer

This system is for educational purposes. Trading involves substantial risk. Always test on demo account first. Never trade with money you cannot afford to lose.
