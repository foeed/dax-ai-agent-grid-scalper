# Quick Start Guide

## 5-Minute Setup

### 1. Get Free API Keys

**NewsAPI (100% Free)**
- Go to: https://newsapi.org/register
- Sign up with email
- Copy your API key
- Cost: FREE (100 requests/day)

**DeepSeek API (Very Cheap)**
- Go to: https://platform.deepseek.com/
- Create account
- Add $5 credit (lasts months)
- Copy your API key

### 2. Install in MetaTrader

1. Open MetaTrader 5
2. Press `Ctrl+E` to open MetaEditor
3. Create new folder: `DAX_V2` in `MQL5\Experts\`
4. Copy these files:
   - `DeepSeekNewsGridScalper_V2.mq5` → `MQL5\Experts\DAX_V2\`
   - `Include\NewsAPI.mqh` → `MQL5\Include\DAX_V2\`
   - `Include\DeepSeekAI.mqh` → `MQL5\Include\DAX_V2\`

### 3. Compile

1. Open `DeepSeekNewsGridScalper_V2.mq5` in MetaEditor
2. Press `F7` to compile
3. Check for "0 errors" in Toolbox

### 4. Allow Web Requests

1. In MetaTrader: `Tools` → `Options` → `Expert Advisors`
2. Check: "Allow WebRequest for listed URL"
3. Add these URLs:
   - `https://api.newsapi.org`
   - `https://api.deepseek.com`

### 5. Attach to Chart

1. Open EURUSD chart (H1 or H4)
2. Drag `DeepSeekNewsGridScalper_V2` from Navigator
3. Enter your API keys in inputs
4. Check "Allow Algo Trading"
5. Click OK

### 6. Verify It's Working

Check the Experts tab for:
```
DeepSeekNewsGridScalper V2 Initialized
AI Analysis: ENABLED
News Filter: ENABLED
```

Check the chart for dashboard display.

---

## Recommended Settings

### Conservative (Start Here)
```
MaxRiskPerTrade: 1.0%
MaxDailyLoss: 5.0%
GridOrders: 1
GridDistance: 400
```

### Standard
```
MaxRiskPerTrade: 2.0%
MaxDailyLoss: 10.0%
GridOrders: 2
GridDistance: 300
```

### Aggressive (Experienced Only)
```
MaxRiskPerTrade: 3.0%
MaxDailyLoss: 15.0%
GridOrders: 3
GridDistance: 250
```

---

## What the AI Does

### Every 5 Minutes:
1. Fetches latest news for your currency pair
2. Analyzes technical indicators
3. Calculates risk score (0-100%)
4. Generates signal (BUY/SELL/HOLD)
5. Adjusts position sizing and stops

### Risk Score Levels:
- **0-30%**: Low risk, normal trading
- **30-60%**: Medium risk, slightly reduced size
- **60-80%**: High risk, minimal trading
- **80-100%**: Extreme risk, no new trades

### News Protection:
- 30 minutes before high-impact news → Caution mode
- Position sizes cut by 50%
- Spread limits tightened by 20%
- Only high-confidence trades allowed

---

## Dashboard Explained

```
========================================
 DEEPSEEK AI GRID SCALPER V2
========================================
 Engine     : OPERATIONAL          ← Current state
 Balance    : 1000.00 USD          ← Account balance
 Equity     : 1005.50 USD          ← Current equity
 P/L Today  : +5.50 USD            ← Today's profit/loss
 Spread     : 12 pts               ← Current spread
----------------------------------------
 AI Status  : Risk: 35% | Conf: 78% | Signal: BUY
 News       : CLEAR | Sentiment: 0.15
----------------------------------------
 RSI(14)    : 55.3                 ← RSI value
 ATR(14)    : 0.00850              ← Volatility
 EMA20/50   : 1.08500 / 1.08200    ← Trend indicators
========================================
```

---

## Troubleshooting

### "No trades executing"
1. Check spread > MaxSpread? Wait or use lower spread broker
2. Check AI Status - is it HOLD? AI waiting for setup
3. Check News - is it CAUTION? News mode active

### "WebRequest failed"
1. Verify URLs added in Options → Expert Advisors
2. Check internet connection
3. Restart MetaTrader

### "API key invalid"
1. NewsAPI: Check email for verification
2. DeepSeek: Check credits balance
3. Test with API keys removed (built-in mode)

---

## Best Pairs

**Recommended:**
- EURUSD (most liquid)
- GBPUSD
- USDJPY
- EURGBP

**Avoid (spreads too high):**
- Exotics (USDTRY, USDZAR)
- Minor pairs during off-hours

---

## Best Sessions

**Active Trading:**
- London: 07:00-16:00 UTC
- New York: 12:00-21:00 UTC
- Overlap: 12:00-16:00 UTC (best)

**Avoid:**
- Friday afternoon
- Sunday evening
- Major holidays

---

## Risk Warning

⚠️ This EA trades real money. Always:
- Start with demo account
- Use money you can afford to lose
- Monitor regularly
- Don't max out risk settings
- Keep API keys secure
