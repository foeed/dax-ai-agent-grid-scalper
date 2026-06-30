//+------------------------------------------------------------------+
//|                                  DeepSeekNewsGridScalper_V2.mq5  |
//|                          AI-Enhanced Grid Trading System          |
//|                         Version 2.0 - Standard Account $40        |
//+------------------------------------------------------------------+
#property copyright "DAX V2 AI Trading System"
#property link      ""
#property version   "2.00"

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\OrderInfo.mqh>
#include <Trade\AccountInfo.mqh>
#include "Include\NewsAPI.mqh"
#include "Include\DeepSeekAI.mqh"

//+------------------------------------------------------------------+
//| EA Inputs                                                          |
//+------------------------------------------------------------------+
input group "--- AI Configuration ---"
input string   InpDeepSeekAPIKey   = "YOUR_DEEPSEEK_API_KEY";  // DeepSeek API Key
input string   InpNewsAPIKey       = "YOUR_NEWS_API_KEY";       // NewsAPI Key (free tier)
input bool     InpUseAIAnalysis    = true;       // Enable AI Analysis
input bool     InpUseNewsFilter    = true;       // Enable News Filter
input int      InpAIAnalysisInterval = 300;      // AI Analysis Interval (seconds)

input group "--- Grid Mechanics ($40 Standard Optimization) ---"
input int      InpGridDistance      = 300;       // Breakout Distance (Points)
input int      InpGridOrders        = 2;         // Maximum pending orders per side
input int      InpTakeProfit        = 200;       // Take Profit (Points)
input int      InpStopLoss          = 150;       // Tight Stop Loss (Points)
input bool     InpDeleteOpposite    = true;      // Cancel opposite grid on trigger

input group "--- Protection & Trailing ---"
input int      InpBreakEvenTrigger  = 100;       // Move to BE at (Points)
input int      InpBreakEvenOffset   = 15;        // Profit points to lock in
input int      InpTrailingStart     = 140;       // Start trailing SL at (Points)
input int      InpTrailingStep      = 30;        // Trailing Step size (Points)

input group "--- AI Risk Management ---"
input double   InpMaxRiskPerTrade   = 2.0;       // Max Risk Per Trade (%)
input double   InpMaxDailyLossPct   = 10.0;      // Max Daily Loss %
input double   InpMaxDrawdownPct    = 15.0;      // Max Floating Drawdown %
input int      InpMaxSpread         = 35;        // Maximum allowed spread (Points)
input bool     InpDynamicPositionSizing = true;  // Use AI Position Sizing
input bool     InpDynamicStopLoss   = true;      // Use AI Dynamic Stop Loss
input ulong    InpMagicNumber       = 770022;    // EA Magic Identifier

//+------------------------------------------------------------------+
//| Global Engine Variables                                            |
//+------------------------------------------------------------------+
CTrade         m_trade;
CPositionInfo  m_position;
COrderInfo     m_order;
CAccountInfo   m_account;
CNewsAPI       m_news_api;
CDeepSeekAI    m_ai;

// State variables
double         m_start_day_balance;
int            m_current_day = -1;
bool           m_system_halted = false;
bool           m_news_caution = false;
datetime       m_last_ai_analysis = 0;
datetime       m_last_news_check = 0;

// AI Analysis cache
AIAnalysisResult m_ai_result;
MarketDataForAI  m_market_data;

// Dashboard
string         m_dashboard_status = "INITIALIZING";

//+------------------------------------------------------------------+
//| Expert initialization function                                     |
//+------------------------------------------------------------------+
int OnInit()
{
   // Initialize trade object
   m_trade.SetExpertMagicNumber(InpMagicNumber);
   
   // Initialize risk parameters
   m_ai.m_max_risk_per_trade = InpMaxRiskPerTrade / 100.0;
   
   // Initialize APIs
   if(InpUseAIAnalysis)
   {
      m_ai.Initialize(InpDeepSeekAPIKey);
   }
   
   if(InpUseNewsFilter)
   {
      m_news_api.Initialize(InpNewsAPIKey);
   }
   
   // Initialize state
   m_start_day_balance = m_account.Balance();
   m_system_halted = false;
   m_news_caution = false;
   
   // Perform initial analysis
   UpdateMarketData();
   if(InpUseAIAnalysis)
   {
      m_ai_result = m_ai.AnalyzeMarket(m_market_data);
   }
   
   Print("DeepSeekNewsGridScalper V2 Initialized");
   Print("AI Analysis: ", InpUseAIAnalysis ? "ENABLED" : "DISABLED");
   Print("News Filter: ", InpUseNewsFilter ? "ENABLED" : "DISABLED");
   
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                   |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   Comment("");
}

//+------------------------------------------------------------------+
//| Expert tick function                                               |
//+------------------------------------------------------------------+
void OnTick()
{
   // 1. Temporal management and hard limits
   ManageDayReset();
   if(CheckRiskCircuitBreaker()) return;
   
   // 2. Update market data for AI
   UpdateMarketData();
   
   // 3. Check news conditions
   if(InpUseNewsFilter)
   {
      CheckNewsConditions();
   }
   
   // 4. Periodic AI analysis
   if(InpUseAIAnalysis && ShouldRunAIAnalysis())
   {
      RunAIAnalysis();
   }
   
   // 5. Spread check (AI-adjusted)
   double max_spread = GetAdjustedMaxSpread();
   if(SymbolInfoInteger(_Symbol, SYMBOL_SPREAD) > max_spread)
   {
      RefreshDashboard("SPREAD CAUTION - IDLE");
      return;
   }
   
   // 6. News caution check
   if(m_news_caution)
   {
      RefreshDashboard("NEWS CAUTION - REDUCED ACTIVITY");
      // Still allow trades but with reduced size
   }
   
   // 7. Process trades and grid
   ProcessActiveTrades();
   ManageGridStructure();
   
   // 8. Update dashboard
   RefreshDashboard(m_dashboard_status);
}

//+------------------------------------------------------------------+
//| Update all market data for AI analysis                             |
//+------------------------------------------------------------------+
void UpdateMarketData()
{
   m_market_data.symbol = _Symbol;
   m_market_data.current_price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   m_market_data.bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   m_market_data.ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   m_market_data.spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   
   // Calculate technical indicators
   m_market_data.atr_14 = CalculateATR(14);
   m_market_data.rsi_14 = CalculateRSI(14);
   m_market_data.macd_signal = CalculateMACD();
   m_market_data.ema_20 = CalculateEMA(20);
   m_market_data.ema_50 = CalculateEMA(50);
   m_market_data.ema_200 = CalculateEMA(200);
   
   // Price data
   m_market_data.daily_high = iHigh(_Symbol, PERIOD_D1, 0);
   m_market_data.daily_low = iLow(_Symbol, PERIOD_D1, 0);
   m_market_data.daily_open = iOpen(_Symbol, PERIOD_D1, 0);
   m_market_data.volume = iVolume(_Symbol, PERIOD_H1, 0);
   
   // News data
   if(InpUseNewsFilter)
   {
      m_market_data.news_summary = m_news_api.GetNewsSummary(_Symbol, 24);
      m_market_data.news_sentiment = m_news_api.AnalyzeNewsSentiment(_Symbol, 4);
   }
}

//+------------------------------------------------------------------+
//| Check news conditions and adjust behavior                          |
//+------------------------------------------------------------------+
void CheckNewsConditions()
{
   // Check if high impact news is imminent
   if(m_news_api.IsHighImpactNewsImminent(_Symbol, 30))
   {
      m_news_caution = true;
      Print("NEWS ALERT: High impact news imminent - entering caution mode");
   }
   else if(m_news_api.IsHighImpactNewsImminent(_Symbol, 60))
   {
      m_news_caution = true;
   }
   else
   {
      m_news_caution = false;
   }
   
   m_last_news_check = TimeCurrent();
}

//+------------------------------------------------------------------+
//| Run AI analysis                                                    |
//+------------------------------------------------------------------+
void RunAIAnalysis()
{
   m_ai_result = m_ai.AnalyzeMarket(m_market_data);
   m_last_ai_analysis = TimeCurrent();
   
   // Update dashboard status based on AI signal
   switch(m_ai_result.signal)
   {
      case SIGNAL_BUY:
         m_dashboard_status = "AI: BULLISH SIGNAL (Conf: " + 
                            DoubleToString(m_ai_result.confidence * 100, 0) + "%)";
         break;
      case SIGNAL_SELL:
         m_dashboard_status = "AI: BEARISH SIGNAL (Conf: " + 
                            DoubleToString(m_ai_result.confidence * 100, 0) + "%)";
         break;
      case SIGNAL_HOLD:
         m_dashboard_status = "AI: HOLD - Waiting for clearer setup";
         break;
      default:
         m_dashboard_status = "AI: ANALYZING...";
         break;
   }
   
   Print("AI Analysis Updated: Risk=", DoubleToString(m_ai_result.risk_score, 2),
         " Signal=", EnumToString(m_ai_result.signal));
}

//+------------------------------------------------------------------+
//| Should run AI analysis now?                                         |
//+------------------------------------------------------------------+
bool ShouldRunAIAnalysis()
{
   int elapsed = (int)(TimeCurrent() - m_last_ai_analysis);
   return elapsed >= InpAIAnalysisInterval;
}

//+------------------------------------------------------------------+
//| Get adjusted max spread based on conditions                        |
//+------------------------------------------------------------------+
double GetAdjustedMaxSpread()
{
   double base_spread = InpMaxSpread;
   
   // Reduce spread tolerance during high risk
   if(m_ai_result.risk_score > 0.7)
   {
      base_spread *= 0.7; // 30% tighter
   }
   
   // Reduce spread tolerance during news
   if(m_news_caution)
   {
      base_spread *= 0.8; // 20% tighter
   }
   
   return base_spread;
}

//+------------------------------------------------------------------+
//| Manage grid structure with AI insights                             |
//+------------------------------------------------------------------+
void ManageGridStructure()
{
   int live_positions = 0;
   int standing_orders = 0;
   
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(m_position.SelectByIndex(i) && m_position.Magic() == InpMagicNumber && 
         m_position.Symbol() == _Symbol)
      {
         live_positions++;
      }
   }
   
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(m_order.SelectByIndex(i) && m_order.Magic() == InpMagicNumber && 
         m_order.Symbol() == _Symbol)
      {
         standing_orders++;
      }
   }
   
   // Check AI signal for grid direction
   bool allow_new_trades = ShouldAllowNewTrades();
   
   // Cancel opposite grid if position exists
   if(live_positions > 0 && InpDeleteOpposite && standing_orders > 0)
   {
      CancelOppositeGridSide();
   }
   
   // Build grid if allowed
   if(live_positions == 0 && standing_orders == 0 && allow_new_trades)
   {
      BuildSmartGrid();
   }
   
   // Cleanup orphaned orders
   if(live_positions == 0 && standing_orders > 0 && !allow_new_trades)
   {
      PurgeAllPending();
   }
}

//+------------------------------------------------------------------+
//| Should allow new trades based on AI and news                       |
//+------------------------------------------------------------------+
bool ShouldAllowNewTrades()
{
   // Don't trade if system halted
   if(m_system_halted) return false;
   
   // Don't trade during news caution if AI is uncertain
   if(m_news_caution && m_ai_result.confidence < 0.6)
   {
      return false;
   }
   
   // Don't trade against strong AI signal
   if(InpUseAIAnalysis && m_ai_result.confidence > 0.7)
   {
      if(m_ai_result.signal == SIGNAL_HOLD)
      {
         return false;
      }
   }
   
   // Check risk score
   if(m_ai_result.risk_score > 0.8)
   {
      return false;
   }
   
   return true;
}

//+------------------------------------------------------------------+
//| Build grid with AI-adjusted parameters                             |
//+------------------------------------------------------------------+
void BuildSmartGrid()
{
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   
   // Get AI-adjusted position size
   double lot_size = GetSmartLotSize();
   
   // Adjust grid distance based on volatility
   int adjusted_grid_distance = InpGridDistance;
   if(m_market_data.atr_14 > 0)
   {
      double volatility_factor = m_market_data.atr_14 / 
                                 (SymbolInfoDouble(_Symbol, SYMBOL_POINT) * 100);
      adjusted_grid_distance = (int)(InpGridDistance * MathMax(0.5, MathMin(2.0, volatility_factor)));
   }
   
   // Determine grid direction bias based on AI signal
   int buy_orders = InpGridOrders;
   int sell_orders = InpGridOrders;
   
   if(InpUseAIAnalysis && m_ai_result.confidence > 0.5)
   {
      if(m_ai_result.signal == SIGNAL_BUY)
      {
         buy_orders = InpGridOrders + 1;
         sell_orders = MathMax(1, InpGridOrders - 1);
      }
      else if(m_ai_result.signal == SIGNAL_SELL)
      {
         sell_orders = InpGridOrders + 1;
         buy_orders = MathMax(1, InpGridOrders - 1);
      }
   }
   
   // Deploy Buy Stops
   for(int i = 1; i <= buy_orders; i++)
   {
      double target_buy = ask + (adjusted_grid_distance * i * point);
      double buy_tp, buy_sl;
      
      if(InpDynamicStopLoss && m_ai_result.suggested_sl > 0)
      {
         buy_sl = m_ai_result.suggested_sl;
         buy_tp = m_ai_result.suggested_tp;
      }
      else
      {
         buy_tp = target_buy + (InpTakeProfit * point);
         buy_sl = target_buy - (InpStopLoss * point);
      }
      
      m_trade.BuyStop(lot_size, target_buy, _Symbol, buy_sl, buy_tp);
   }
   
   // Deploy Sell Stops
   for(int i = 1; i <= sell_orders; i++)
   {
      double target_sell = bid - (adjusted_grid_distance * i * point);
      double sell_tp, sell_sl;
      
      if(InpDynamicStopLoss && m_ai_result.suggested_sl > 0)
      {
         sell_sl = m_ai_result.suggested_sl;
         sell_tp = m_ai_result.suggested_tp;
      }
      else
      {
         sell_tp = target_sell - (InpTakeProfit * point);
         sell_sl = target_sell + (InpStopLoss * point);
      }
      
      m_trade.SellStop(lot_size, target_sell, _Symbol, sell_sl, sell_tp);
   }
}

//+------------------------------------------------------------------+
//| Get smart lot size based on AI and risk                            |
//+------------------------------------------------------------------+
double GetSmartLotSize()
{
   double base_lot = 0.01;
   
   if(!InpDynamicPositionSizing)
   {
      return base_lot;
   }
   
   double account_balance = m_account.Balance();
   double stop_loss_distance = InpStopLoss * SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   
   // Use AI-calculated position size
   double ai_lot = m_ai.CalculateRiskAdjustedSize(
      account_balance,
      stop_loss_distance,
      SymbolInfoDouble(_Symbol, SYMBOL_BID),
      m_ai_result.risk_score
   );
   
   // Reduce size during news caution
   if(m_news_caution)
   {
      ai_lot *= 0.5;
   }
   
   // Ensure minimum lot size
   double min_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   return MathMax(min_lot, ai_lot);
}

//+------------------------------------------------------------------+
//| Process active trades with AI management                           |
//+------------------------------------------------------------------+
void ProcessActiveTrades()
{
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(m_position.SelectByIndex(i) && m_position.Magic() == InpMagicNumber && 
         m_position.Symbol() == _Symbol)
      {
         double current_tick = (m_position.PositionType() == POSITION_TYPE_BUY) ? 
                              SymbolInfoDouble(_Symbol, SYMBOL_BID) : 
                              SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         double base_entry = m_position.PriceOpen();
         double current_sl = m_position.StopLoss();
         
         // Check if AI suggests closing
         if(InpUseAIAnalysis && m_ai_result.confidence > 0.8)
         {
            bool should_close = false;
            
            if(m_position.PositionType() == POSITION_TYPE_BUY && m_ai_result.signal == SIGNAL_SELL)
            {
               should_close = true;
            }
            else if(m_position.PositionType() == POSITION_TYPE_SELL && m_ai_result.signal == SIGNAL_BUY)
            {
               should_close = true;
            }
            
            if(should_close)
            {
               Print("AI Signal Override: Closing position against strong signal");
               m_trade.PositionClose(m_position.Ticket());
               continue;
            }
         }
         
         // Standard trailing and break-even logic
         if(m_position.PositionType() == POSITION_TYPE_BUY)
         {
            double trajectory = (current_tick - base_entry) / point;
            
            if(trajectory >= InpBreakEvenTrigger && current_sl < base_entry)
            {
               m_trade.PositionModify(m_position.Ticket(), 
                                     base_entry + (InpBreakEvenOffset * point), 
                                     m_position.TakeProfit());
               continue;
            }
            
            if(trajectory >= InpTrailingStart)
            {
               double calculated_trailing = current_tick - (InpTrailingStep * point);
               if(calculated_trailing > current_sl)
               {
                  m_trade.PositionModify(m_position.Ticket(), 
                                        calculated_trailing, 
                                        m_position.TakeProfit());
               }
            }
         }
         else if(m_position.PositionType() == POSITION_TYPE_SELL)
         {
            double trajectory = (base_entry - current_tick) / point;
            
            if(trajectory >= InpBreakEvenTrigger && (current_sl > base_entry || current_sl == 0))
            {
               m_trade.PositionModify(m_position.Ticket(), 
                                     base_entry - (InpBreakEvenOffset * point), 
                                     m_position.TakeProfit());
               continue;
            }
            
            if(trajectory >= InpTrailingStart)
            {
               double calculated_trailing = current_tick + (InpTrailingStep * point);
               if(calculated_trailing < current_sl || current_sl == 0)
               {
                  m_trade.PositionModify(m_position.Ticket(), 
                                        calculated_trailing, 
                                        m_position.TakeProfit());
               }
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Cancel opposite grid side                                          |
//+------------------------------------------------------------------+
void CancelOppositeGridSide()
{
   ENUM_POSITION_TYPE active_trend = POSITION_TYPE_BUY;
   bool track = false;
   
   for(int i = 0; i < PositionsTotal(); i++)
   {
      if(m_position.SelectByIndex(i) && m_position.Magic() == InpMagicNumber && 
         m_position.Symbol() == _Symbol)
      {
         active_trend = m_position.PositionType();
         track = true;
         break;
      }
   }
   
   if(!track) return;
   
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(m_order.SelectByIndex(i) && m_order.Magic() == InpMagicNumber && 
         m_order.Symbol() == _Symbol)
      {
         if(active_trend == POSITION_TYPE_BUY && m_order.OrderType() == ORDER_TYPE_SELL_STOP)
         {
            m_trade.OrderDelete(m_order.Ticket());
         }
         if(active_trend == POSITION_TYPE_SELL && m_order.OrderType() == ORDER_TYPE_BUY_STOP)
         {
            m_trade.OrderDelete(m_order.Ticket());
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Purge all pending orders                                           |
//+------------------------------------------------------------------+
void PurgeAllPending()
{
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(m_order.SelectByIndex(i) && m_order.Magic() == InpMagicNumber && 
         m_order.Symbol() == _Symbol)
      {
         m_trade.OrderDelete(m_order.Ticket());
      }
   }
}

//+------------------------------------------------------------------+
//| Risk circuit breaker                                               |
//+------------------------------------------------------------------+
bool CheckRiskCircuitBreaker()
{
   if(m_system_halted) return true;
   
   double account_equity = m_account.Equity();
   double account_balance = m_account.Balance();
   
   double daily_loss_evaluation = ((m_start_day_balance - account_equity) / m_start_day_balance) * 100;
   double drawdown_evaluation = ((account_balance - account_equity) / account_balance) * 100;
   
   // Use tighter limits if AI indicates high risk
   double max_daily = InpMaxDailyLossPct;
   double max_dd = InpMaxDrawdownPct;
   
   if(m_ai_result.risk_score > 0.7)
   {
      max_daily *= 0.7;
      max_dd *= 0.7;
   }
   
   if(daily_loss_evaluation >= max_daily || drawdown_evaluation >= max_dd)
   {
      // Emergency close all positions
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         if(m_position.SelectByIndex(i) && m_position.Magic() == InpMagicNumber)
         {
            m_trade.PositionClose(m_position.Ticket());
         }
      }
      
      PurgeAllPending();
      m_system_halted = true;
      
      Print("CRITICAL: Risk circuit breaker triggered!");
      Print("Daily Loss: ", DoubleToString(daily_loss_evaluation, 2), "%");
      Print("Drawdown: ", DoubleToString(drawdown_evaluation, 2), "%");
      
      return true;
   }
   
   return false;
}

//+------------------------------------------------------------------+
//| Day reset management                                               |
//+------------------------------------------------------------------+
void ManageDayReset()
{
   MqlDateTime current_time;
   TimeCurrent(current_time);
   
   if(current_time.day_of_year != m_current_day)
   {
      m_current_day = current_time.day_of_year;
      m_start_day_balance = m_account.Balance();
      m_system_halted = false;
      m_news_caution = false;
      
      Print("New trading day started. Balance: ", DoubleToString(m_start_day_balance, 2));
   }
}

//+------------------------------------------------------------------+
//| Technical Indicator Calculations                                    |
//+------------------------------------------------------------------+
double CalculateATR(int period)
{
   double atr[];
   ArraySetAsSeries(atr, true);
   
   int handle = iATR(_Symbol, PERIOD_H1, period);
   if(CopyBuffer(handle, 0, 0, 1, atr) > 0)
   {
      return atr[0];
   }
   
   return 0.0;
}

double CalculateRSI(int period)
{
   double rsi[];
   ArraySetAsSeries(rsi, true);
   
   int handle = iRSI(_Symbol, PERIOD_H1, period, PRICE_CLOSE);
   if(CopyBuffer(handle, 0, 0, 1, rsi) > 0)
   {
      return rsi[0];
   }
   
   return 50.0;
}

double CalculateMACD()
{
   double macd[];
   double signal[];
   ArraySetAsSeries(macd, true);
   ArraySetAsSeries(signal, true);
   
   int handle = iMACD(_Symbol, PERIOD_H1, 12, 26, 9, PRICE_CLOSE);
   if(CopyBuffer(handle, 0, 0, 1, macd) > 0 && CopyBuffer(handle, 1, 0, 1, signal) > 0)
   {
      return macd[0] - signal[0];
   }
   
   return 0.0;
}

double CalculateEMA(int period)
{
   double ema[];
   ArraySetAsSeries(ema, true);
   
   int handle = iMA(_Symbol, PERIOD_H1, period, 0, MODE_EMA, PRICE_CLOSE);
   if(CopyBuffer(handle, 0, 0, 1, ema) > 0)
   {
      return ema[0];
   }
   
   return 0.0;
}

//+------------------------------------------------------------------+
//| Dashboard                                                          |
//+------------------------------------------------------------------+
void RefreshDashboard(string engine_msg)
{
   double profit_tracking = m_account.Equity() - m_start_day_balance;
   
   string ai_status = "DISABLED";
   if(InpUseAIAnalysis)
   {
      ai_status = "Risk: " + DoubleToString(m_ai_result.risk_score * 100, 0) + "%";
      ai_status += " | Conf: " + DoubleToString(m_ai_result.confidence * 100, 0) + "%";
      ai_status += " | Signal: " + GetSignalText(m_ai_result.signal);
   }
   
   string news_status = "DISABLED";
   if(InpUseNewsFilter)
   {
      news_status = m_news_caution ? "CAUTION" : "CLEAR";
      news_status += " | Sentiment: " + DoubleToString(m_market_data.news_sentiment, 2);
   }
   
   string ui = "========================================\n";
   ui += " DEEPSEEK AI GRID SCALPER V2\n";
   ui += "========================================\n";
   ui += " Engine     : " + engine_msg + "\n";
   ui += " Balance    : " + DoubleToString(m_account.Balance(), 2) + " USD\n";
   ui += " Equity     : " + DoubleToString(m_account.Equity(), 2) + " USD\n";
   ui += " P/L Today  : " + DoubleToString(profit_tracking, 2) + " USD\n";
   ui += " Spread     : " + IntegerToString(SymbolInfoInteger(_Symbol, SYMBOL_SPREAD)) + " pts\n";
   ui += "----------------------------------------\n";
   ui += " AI Status  : " + ai_status + "\n";
   ui += " News       : " + news_status + "\n";
   ui += "----------------------------------------\n";
   ui += " RSI(14)    : " + DoubleToString(m_market_data.rsi_14, 1) + "\n";
   ui += " ATR(14)    : " + DoubleToString(m_market_data.atr_14, 5) + "\n";
   ui += " EMA20/50   : " + DoubleToString(m_market_data.ema_20, 5) + " / " + 
         DoubleToString(m_market_data.ema_50, 5) + "\n";
   ui += "========================================\n";
   
   Comment(ui);
}

//+------------------------------------------------------------------+
//| Get signal text                                                    |
//+------------------------------------------------------------------+
string GetSignalText(ENUM_SIGNAL_TYPE signal)
{
   switch(signal)
   {
      case SIGNAL_BUY: return "BUY";
      case SIGNAL_SELL: return "SELL";
      case SIGNAL_HOLD: return "HOLD";
      default: return "NONE";
   }
}
