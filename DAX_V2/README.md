# DeepSeek AI News Grid Scalper V2

## AI-Enhanced Grid Trading System for Standard Accounts

A sophisticated Expert Advisor that combines grid trading with DeepSeek AI analysis and real-time news filtering for enhanced risk management.

---

## 🚀 Features

### Core Trading Engine
- **Dynamic Grid System**: Breakout-based grid with configurable distance and orders
- **Smart Position Sizing**: AI-adjusted lot sizes based on risk score
- **Adaptive Stop Loss**: ATR-based dynamic stops with AI optimization
- **Trailing & Break-Even**: Automated profit protection

### AI Integration
- **DeepSeek AI Analysis**: Professional-grade market analysis
- **Sentiment Analysis**: News sentiment scoring
- **Risk Assessment**: Real-time risk score (0-100%)
- **Signal Generation**: BUY/SELL/HOLD recommendations
- **Confidence Levels**: AI certainty measurement

### News Intelligence
- **Free NewsAPI Integration**: Real-time forex news
- **Economic Calendar**: High-impact event detection
- **News Caution Mode**: Automatic risk reduction before news
- **Sentiment Scoring**: Market sentiment from news analysis

### Risk Management
- **Circuit Breakers**: Daily loss and drawdown limits
- **AI-Adjusted Limits**: Tighter limits during high-risk conditions
- **Position Size Limits**: Maximum risk per trade enforcement
- **Spread Protection**: Dynamic spread filtering

---

## 📁 File Structure

```
DAX_V2/
├── DeepSeekNewsGridScalper_V2.mq5    # Main EA file
├── Include/
│   ├── NewsAPI.mqh                    # News API integration
│   └── DeepSeekAI.mqh                 # DeepSeek AI handler
├── Config/
│   └── EA_Config.ini                  # Configuration file
├── Files/                             # Log files directory
└── README.md                          # This file
```

---

## ⚙️ Installation

### Step 1: Copy Files
1. Copy `DeepSeekNewsGridScalper_V2.mq5` to:
   ```
   C:\Users\[YourName]\AppData\Roaming\MetaQuotes\Terminal\[TerminalID]\MQL5\Experts\
   ```

2. Copy the `Include` folder to:
   ```
   C:\Users\[YourName]\AppData\Roaming\MetaQuotes\Terminal\[TerminalID]\MQL5\Include\
   ```

### Step 2: Get API Keys

#### NewsAPI (Free)
1. Go to https://newsapi.org/register
2. Create a free account
3. Copy your API key
4. Free tier: 100 requests/day

#### DeepSeek API
1. Go to https://platform.deepseek.com/
2. Create an account
3. Add credits (minimal cost)
4. Copy your API key

### Step 3: Configure EA
1. Open MetaEditor
2. Compile `DeepSeekNewsGridScalper_V2.mq5`
3. Drag EA onto chart
4. Enter your API keys in inputs
5. Enable "Allow WebRequest" in Common tab

### Step 4: Add NewsAPI Domain
In MetaTrader:
1. Go to Tools → Options → Expert Advisors
2. Add to URL list: `https://api.newsapi.org`
3. Add to URL list: `https://api.deepseek.com`

---

## 📊 Configuration

### AI Settings
| Parameter | Description | Default |
|-----------|-------------|---------|
| DeepSeekAPIKey | Your DeepSeek API key | Required |
| NewsAPIKey | Your NewsAPI key | Required |
| UseAIAnalysis | Enable AI features | true |
| UseNewsFilter | Enable news filtering | true |
| AIAnalysisInterval | Seconds between AI calls | 300 |

### Risk Management
| Parameter | Description | Default |
|-----------|-------------|---------|
| MaxRiskPerTrade | Max risk per trade % | 2.0% |
| MaxDailyLoss | Max daily loss % | 10.0% |
| MaxDrawdown | Max drawdown % | 15.0% |
| DynamicPositionSizing | AI-adjusted sizing | true |
| DynamicStopLoss | AI-adjusted stops | true |

### Grid Mechanics
| Parameter | Description | Default |
|-----------|-------------|---------|
| GridDistance | Breakout distance (points) | 300 |
| GridOrders | Max orders per side | 2 |
| TakeProfit | Take profit (points) | 200 |
| StopLoss | Stop loss (points) | 150 |

---

## 🛡️ Safety Features

### Circuit Breakers
- **Daily Loss Limit**: Closes all positions at 10% daily loss
- **Drawdown Limit**: Emergency stop at 15% drawdown
- **AI-Adjusted Limits**: Tighter limits when AI detects high risk

### News Protection
- **Pre-News Caution**: Reduces activity 30 min before high-impact news
- **Position Size Reduction**: Cuts lot size by 50% during news
- **Spread Filtering**: Tighter spread limits during volatility

### AI Risk Controls
- **Risk Score**: 0-100% risk assessment
- **Confidence Filter**: Only trades when AI is confident
- **Signal Filtering**: Prevents trading against strong AI signals

---

## 📈 Dashboard

The EA displays real-time information on chart:

```
========================================
 DEEPSEEK AI GRID SCALPER V2
========================================
 Engine     : OPERATIONAL
 Balance    : 1000.00 USD
 Equity     : 1005.50 USD
 P/L Today  : +5.50 USD
 Spread     : 12 pts
----------------------------------------
 AI Status  : Risk: 35% | Conf: 78% | Signal: BUY
 News       : CLEAR | Sentiment: 0.15
----------------------------------------
 RSI(14)    : 55.3
 ATR(14)    : 0.00850
 EMA20/50   : 1.08500 / 1.08200
========================================
```

---

## 🔧 Troubleshooting

### Common Issues

1. **"WebRequest not allowed"**
   - Enable "Allow WebRequest for listed URL" in EA properties
   - Add API domains to allowed list

2. **"NewsAPI connection failed"**
   - Check API key is correct
   - Verify free tier limits not exceeded
   - EA will work in offline mode

3. **"DeepSeek API error"**
   - Verify API key and credits
   - Check internet connection
   - EA falls back to built-in analysis

4. **No trades executing**
   - Check spread is within limits
   - Verify AI allows new trades
   - Check for news caution mode

---

## 📝 Important Notes

1. **Demo Test First**: Always test on demo account
2. **API Costs**: DeepSeek API has minimal costs
3. **NewsAPI Limits**: Free tier = 100 requests/day
4. **Risk Warning**: Trading involves risk of capital loss
5. **VPS Recommended**: For 24/5 operation

---

## 🔄 Version History

### V2.0 (Current)
- Added DeepSeek AI integration
- Added NewsAPI news filtering
- Enhanced risk management
- AI-driven position sizing
- Dynamic stop loss calculation
- Market regime detection

### V1.0 (Base)
- Basic grid trading
- Simple risk controls
- Standard trailing stops

---

## 📞 Support

For issues or questions:
1. Check README for solutions
2. Review EA logs in Experts tab
3. Verify API key configuration
4. Test on demo account first

---

## ⚠️ Disclaimer

This EA is for educational purposes. Trading forex carries substantial risk. Past performance is not indicative of future results. Always use proper risk management and never trade with money you cannot afford to lose.
