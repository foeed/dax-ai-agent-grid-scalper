//+------------------------------------------------------------------+
//|                                            EAFastConnector.mq5   |
//|                  DAX V2 Grid Trading with FastAPI Backend         |
//|                         Version 2.5 - Optimized                   |
//+------------------------------------------------------------------+
#property copyright "DAX V2 AI Trading System"
#property link      ""
#property version   "2.50"

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\OrderInfo.mqh>
#include <Trade\AccountInfo.mqh>
#include <Indicators\Oscilators.mqh>
#include <Indicators\Trend.mqh>

//+------------------------------------------------------------------+
//| Structures                                                         |
//+------------------------------------------------------------------+
struct AIResult
{
   string       signal;
   double       risk_score;
   double       confidence;
   double       suggested_volume;
   double       suggested_sl;
   double       suggested_tp;
   string       risk_level;
   bool         news_caution;
};

//+------------------------------------------------------------------+
//| EA Inputs                                                          |
//+------------------------------------------------------------------+
input group "--- Backend Configuration ---"
input string   InpBackendURL      = "http://127.0.0.1:8000";
input bool     InpUseBackend      = true;
input int      InpRequestTimeout  = 5000;
input int      InpAnalysisInterval = 30;

input group "--- Grid Mechanics ---"
input int      InpGridDistance      = 300;
input int      InpGridMinDistance   = 30;
input int      InpGridMaxDistance   = 200;       // Max grid distance (points)
input int      InpGridOrders        = 2;
input int      InpTakeProfit        = 200;
input int      InpStopLoss          = 150;
input bool     InpDeleteOpposite    = true;

input group "--- Protection & Trailing ---"
input int      InpBreakEvenTrigger  = 100;
input int      InpBreakEvenOffset   = 15;
input int      InpTrailingStart     = 140;
input int      InpTrailingStep      = 30;

input group "--- Risk Management ---"
input double   InpMaxRiskPerTrade   = 2.0;
input double   InpMaxDailyLossPct   = 10.0;
input double   InpMaxDrawdownPct    = 15.0;
input int      InpMaxSpread         = 35;
input ulong    InpMagicNumber       = 770033;

//+------------------------------------------------------------------+
//| Global Variables                                                   |
//+------------------------------------------------------------------+
CTrade         m_trade;
CPositionInfo  m_position;
COrderInfo     m_order;
CAccountInfo   m_account;

// Indicator handles
int            m_handle_rsi;
int            m_handle_atr;
int            m_handle_ema20;
int            m_handle_ema50;
int            m_handle_ema200;

// State
double         m_start_day_balance;
int            m_current_day = -1;
bool           m_system_halted = false;
datetime       m_last_analysis = 0;
datetime       m_first_tick_time = 0;
int            m_request_retry_count = 0;

// AI Results
AIResult       m_ai;
double         m_rsi_val = 50;
double         m_atr_val = 0;
double         m_ema20_val = 0;
double         m_ema50_val = 0;
double         m_ema200_val = 0;
double         m_spread = 0;
double         m_bid = 0;
double         m_ask = 0;
double         m_d_high = 0;
double         m_d_low = 0;

//+------------------------------------------------------------------+
//| Expert initialization function                                     |
//+------------------------------------------------------------------+
int OnInit()
{
   m_trade.SetExpertMagicNumber(InpMagicNumber);
   m_start_day_balance = m_account.Balance();
   m_system_halted = false;
   m_first_tick_time = 0;
   
   // Create technical indicators
   m_handle_rsi = iRSI(_Symbol, PERIOD_H1, 14, PRICE_CLOSE);
   m_handle_atr = iATR(_Symbol, PERIOD_H1, 14);
   m_handle_ema20 = iMA(_Symbol, PERIOD_H1, 20, 0, MODE_EMA, PRICE_CLOSE);
   m_handle_ema50 = iMA(_Symbol, PERIOD_H1, 50, 0, MODE_EMA, PRICE_CLOSE);
   m_handle_ema200 = iMA(_Symbol, PERIOD_H1, 200, 0, MODE_EMA, PRICE_CLOSE);
   
   // Init AI state
   m_ai.signal = "HOLD";
   m_ai.risk_score = 0.5;
   m_ai.confidence = 0.5;
   m_ai.suggested_volume = 0.01;
   m_ai.risk_level = "MEDIUM";
   m_ai.news_caution = false;
   
   Print("========================================");
   Print(" DAX V2 GRID SCALPER V2.5");
   Print(" Balance: $", DoubleToString(m_start_day_balance, 2));
   Print(" Backend: ", InpBackendURL);
   Print("========================================");
   
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                   |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   IndicatorRelease(m_handle_rsi);
   IndicatorRelease(m_handle_atr);
   IndicatorRelease(m_handle_ema20);
   IndicatorRelease(m_handle_ema50);
   IndicatorRelease(m_handle_ema200);
   Comment("");
}

//+------------------------------------------------------------------+
//| Expert tick function                                               |
//+------------------------------------------------------------------+
void OnTick()
{
   // Init state
   if(m_first_tick_time == 0)
   {
      m_first_tick_time = TimeCurrent();
      // Delay first analysis by 3s to avoid race
      m_last_analysis = TimeCurrent() + 3;
   }
   
   // Update market data (every tick, fast)
   UpdateMarketData();
   
   // Day reset and circuit breaker
   ManageDayReset();
   if(CheckRiskCircuitBreaker()) return;
   
   // Run analysis periodically
   if(ShouldRunAnalysis())
   {
      RunAnalysis();
   }
   
   // Spread gate
   if(m_spread > InpMaxSpread)
   {
      RefreshDashboard("SPREAD HIGH");
      return;
   }
   
   // Core trading logic
   ProcessActiveTrades();
   ManageGridStructure();
   
   // Display
   RefreshDashboard("");
}

//+------------------------------------------------------------------+
//| Update market data from indicators                                 |
//+------------------------------------------------------------------+
void UpdateMarketData()
{
   m_bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   m_ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   m_spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   m_d_high = iHigh(_Symbol, PERIOD_D1, 0);
   m_d_low = iLow(_Symbol, PERIOD_D1, 0);
   
   double buf[1];
   if(CopyBuffer(m_handle_rsi, 0, 0, 1, buf) > 0) m_rsi_val = buf[0];
   if(CopyBuffer(m_handle_atr, 0, 0, 1, buf) > 0) m_atr_val = buf[0];
   if(CopyBuffer(m_handle_ema20, 0, 0, 1, buf) > 0) m_ema20_val = buf[0];
   if(CopyBuffer(m_handle_ema50, 0, 0, 1, buf) > 0) m_ema50_val = buf[0];
   if(CopyBuffer(m_handle_ema200, 0, 0, 1, buf) > 0) m_ema200_val = buf[0];
}

//+------------------------------------------------------------------+
//| Should run analysis now                                            |
//+------------------------------------------------------------------+
bool ShouldRunAnalysis()
{
   int elapsed = (int)(TimeCurrent() - m_last_analysis);
   return elapsed >= InpAnalysisInterval;
}

//+------------------------------------------------------------------+
//| Run analysis (backend or local)                                    |
//+------------------------------------------------------------------+
void RunAnalysis()
{
   if(InpUseBackend)
   {
      if(TryBackendAnalysis())
      {
         Print("AI: Signal=", m_ai.signal, 
               " Risk=", DoubleToString(m_ai.risk_score*100,0), "%",
               " Conf=", DoubleToString(m_ai.confidence*100,0), "%");
      }
      else
      {
         // Fallback with real indicators
         LocalAnalysis();
         Print("AI(LOCAL): Signal=", m_ai.signal,
               " Risk=", DoubleToString(m_ai.risk_score*100,0), "%",
               " RSI=", DoubleToString(m_rsi_val,1),
               " ATR=", DoubleToString(m_atr_val,5));
      }
   }
   else
   {
      LocalAnalysis();
   }
   
   m_last_analysis = TimeCurrent();
}

//+------------------------------------------------------------------+
//| Try backend with retry                                             |
//+------------------------------------------------------------------+
bool TryBackendAnalysis()
{
   string json = BuildJSON();
   char post_data[], result[];
   string result_headers;
   string headers = "Content-Type: application/json\r\n";
   string url = InpBackendURL + "/api/v1/trading/signal";
   
   // Try up to 2 times with increasing timeout
   int timeouts[] = {5000, 8000};
   
   for(int attempt = 0; attempt < 2; attempt++)
   {
      StringToCharArray(json, post_data, 0, StringLen(json));
      ArrayResize(result, 4096);
      
      int res = WebRequest("POST", url, headers, timeouts[attempt],
                          post_data, result, result_headers);
      
      if(res >= 200 && res < 300 && ArraySize(result) > 0)
      {
         string response = CharArrayToString(result);
         if(StringLen(response) > 10)
         {
            ParseResponse(response);
            return true;
         }
      }
      
      // Wait before retry
      if(attempt < 1) Sleep(1000);
   }
   
   return false;
}

//+------------------------------------------------------------------+
//| Build JSON for backend                                             |
//+------------------------------------------------------------------+
string BuildJSON()
{
   string json = "{";
   json += "\"symbol\":\"" + _Symbol + "\",";
   json += "\"bid\":" + DoubleToString(m_bid, 5) + ",";
   json += "\"ask\":" + DoubleToString(m_ask, 5) + ",";
   json += "\"spread\":" + IntegerToString((int)m_spread) + ",";
   json += "\"volume\":" + IntegerToString((int)iVolume(_Symbol, PERIOD_H1, 0)) + ",";
   json += "\"daily_high\":" + DoubleToString(m_d_high, 5) + ",";
   json += "\"daily_low\":" + DoubleToString(m_d_low, 5) + ",";
   json += "\"daily_open\":" + DoubleToString(iOpen(_Symbol, PERIOD_D1, 0), 5);
   json += "}";
   return json;
}

//+------------------------------------------------------------------+
//| Parse backend response                                             |
//+------------------------------------------------------------------+
void ParseResponse(string response)
{
   m_ai.signal = ExtractString(response, "signal");
   m_ai.risk_score = ExtractDouble(response, "risk_score");
   m_ai.confidence = ExtractDouble(response, "confidence");
   m_ai.suggested_volume = ExtractDouble(response, "suggested_volume");
   m_ai.suggested_sl = ExtractDouble(response, "suggested_sl");
   m_ai.suggested_tp = ExtractDouble(response, "suggested_tp");
   m_ai.risk_level = ExtractString(response, "risk_level");
   m_ai.news_caution = StringFind(response, "\"news_caution\":true") >= 0;
   
   // Clamp volume to safe range for $40 account
   if(m_ai.suggested_volume <= 0 || m_ai.suggested_volume > 0.1)
      m_ai.suggested_volume = 0.01;
}

//+------------------------------------------------------------------+
//| Local analysis with real indicators                                |
//+------------------------------------------------------------------+
void LocalAnalysis()
{
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double mid = (m_bid + m_ask) / 2;
   double daily_rng = m_d_high - m_d_low;
   if(daily_rng <= 0) daily_rng = mid * 0.005;
   
   double buy_score = 0.0;
   double sell_score = 0.0;
   
   // 1. RSI (40%)
   if(m_rsi_val < 35) buy_score += 0.4;
   else if(m_rsi_val > 65) sell_score += 0.4;
   else if(m_rsi_val < 45) buy_score += 0.15;
   else if(m_rsi_val > 55) sell_score += 0.15;
   
   // 2. EMA alignment (30%)
   if(m_ema20_val > m_ema50_val && m_ema50_val > m_ema200_val && m_ema20_val > 0)
      buy_score += 0.3;
   else if(m_ema20_val < m_ema50_val && m_ema50_val < m_ema200_val && m_ema20_val > 0)
      sell_score += 0.3;
   
   // 3. Price position in daily range (20%)
   double pos = (mid - m_d_low) / daily_rng;
   if(pos < 0.25) buy_score += 0.2;
   else if(pos > 0.75) sell_score += 0.2;
   
   // 4. ATR volatility (10%)
   if(m_atr_val > 0 && m_atr_val < daily_rng * 0.5)
   {
      buy_score += 0.1;
      sell_score += 0.1;
   }
   
   // Determine signal
   double threshold = 0.35;
   if(buy_score > sell_score && buy_score >= threshold)
   {
      m_ai.signal = "BUY";
      m_ai.confidence = MathMin(0.95, buy_score);
      m_ai.risk_score = MathMax(0.15, 1.0 - (buy_score - sell_score));
      m_ai.suggested_sl = mid - (daily_rng * 0.3);
      m_ai.suggested_tp = mid + (daily_rng * 0.5);
   }
   else if(sell_score > buy_score && sell_score >= threshold)
   {
      m_ai.signal = "SELL";
      m_ai.confidence = MathMin(0.95, sell_score);
      m_ai.risk_score = MathMax(0.15, 1.0 - (sell_score - buy_score));
      m_ai.suggested_sl = mid + (daily_rng * 0.3);
      m_ai.suggested_tp = mid - (daily_rng * 0.5);
   }
   else
   {
      m_ai.signal = "HOLD";
      m_ai.confidence = 0.4;
      m_ai.risk_score = 0.55;
      m_ai.suggested_sl = 0;
      m_ai.suggested_tp = 0;
   }
   
   m_ai.suggested_volume = 0.01;
   m_ai.risk_level = (m_ai.risk_score < 0.4) ? "LOW" : ((m_ai.risk_score < 0.7) ? "MEDIUM" : "HIGH");
   m_ai.news_caution = false;
}

//+------------------------------------------------------------------+
//| Calculate dynamic grid distance based on ATR                        |
//+------------------------------------------------------------------+
int GetDynamicGridDistance()
{
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   if(point <= 0) return InpGridDistance;
   
   double atr_points = m_atr_val / point;
   double dynamic_dist = atr_points * 0.5;
   
   int result = (int)MathMax(InpGridMinDistance, MathMin(InpGridMaxDistance, dynamic_dist));
   return result;
}

//+------------------------------------------------------------------+
//| Get nearest pending order price (0 if none)                        |
//+------------------------------------------------------------------+
double GetNearestOrderPrice()
{
   double nearest = 0;
   double best_dist = DBL_MAX;
   double mid = (m_bid + m_ask) / 2;
   
   for(int i = 0; i < OrdersTotal(); i++)
   {
      if(m_order.SelectByIndex(i) && m_order.Magic() == InpMagicNumber && m_order.Symbol() == _Symbol)
      {
         double dist = MathAbs(m_order.PriceOpen() - mid);
         if(dist < best_dist)
         {
            best_dist = dist;
            nearest = m_order.PriceOpen();
         }
      }
   }
   return nearest;
}

//+------------------------------------------------------------------+
//| Manage grid structure                                              |
//+------------------------------------------------------------------+
void ManageGridStructure()
{
   int live = CountPositions();
   int pending = CountOrders();
   
   // Cancel opposite grid when position exists
   if(live > 0 && InpDeleteOpposite && pending > 0)
      CancelOppositeGridSide();
   
   // Build new grid when flat
   if(live == 0 && pending == 0 && ShouldTrade())
      BuildSmartGrid();
   
   // Auto-rebuild: if price strayed too far from deployed orders
   if(live == 0 && pending > 0)
   {
      double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
      int grid_dist = GetDynamicGridDistance();
      double mid = (m_bid + m_ask) / 2;
      double nearest_price = GetNearestOrderPrice();
      
      if(nearest_price > 0 && MathAbs(nearest_price - mid) > grid_dist * 2 * point)
      {
         Print("Grid stale: price moved too far. Purging & rebuilding...");
         PurgeAllPending();
         if(ShouldTrade())
            BuildSmartGrid();
         return;
      }
   }
   
   // Cleanup orphaned orders
   if(live == 0 && pending > 0 && !ShouldTrade())
      PurgeAllPending();
}

//+------------------------------------------------------------------+
//| Count positions                                                    |
//+------------------------------------------------------------------+
int CountPositions()
{
   int cnt = 0;
   for(int i = PositionsTotal()-1; i >= 0; i--)
      if(m_position.SelectByIndex(i) && m_position.Magic() == InpMagicNumber && m_position.Symbol() == _Symbol)
         cnt++;
   return cnt;
}

//+------------------------------------------------------------------+
//| Count pending orders                                               |
//+------------------------------------------------------------------+
int CountOrders()
{
   int cnt = 0;
   for(int i = OrdersTotal()-1; i >= 0; i--)
      if(m_order.SelectByIndex(i) && m_order.Magic() == InpMagicNumber && m_order.Symbol() == _Symbol)
         cnt++;
   return cnt;
}

//+------------------------------------------------------------------+
//| Should allow new trades                                            |
//+------------------------------------------------------------------+
bool ShouldTrade()
{
   if(m_system_halted) return false;
   if(m_ai.risk_score > 0.75) return false;
   
   // Allow BUY/SELL signals even with low confidence for grid
   if(m_ai.signal == "BUY" || m_ai.signal == "SELL")
      return true;
   
   // Also trade on HOLD if conditions are reasonable
   if(m_ai.confidence < 0.3) return false;
   if(m_spread > InpMaxSpread * 1.5) return false;
   
   // For HOLD, allow grid but with neutral bias
   return true;  // Grid trading works on HOLD too (both ways)
}

//+------------------------------------------------------------------+
//| Get safe dynamic lot size                                           |
//+------------------------------------------------------------------+
double GetDynamicLot()
{
   double balance = m_account.Balance();
   double risk_lot = MathRound(balance * 0.02 / 10.0) * 0.01;
   return MathMax(0.01, MathMin(0.1, risk_lot));
}

//+------------------------------------------------------------------+
//| Build smart grid (LIMIT orders for scalping)                        |
//+------------------------------------------------------------------+
void BuildSmartGrid()
{
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   long stops_level = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double min_stop_dist = MathMax(InpStopLoss * point, (stops_level + 10) * point);
   
    double lot = GetDynamicLot();
   int grid_dist = GetDynamicGridDistance();
   
   int buy_cnt = InpGridOrders;
   int sell_cnt = InpGridOrders;
   
   // Bias grid based on signal
   if(m_ai.signal == "BUY")
   {
      buy_cnt = InpGridOrders + 1;
      sell_cnt = MathMax(1, InpGridOrders - 1);
   }
   else if(m_ai.signal == "SELL")
   {
      sell_cnt = InpGridOrders + 1;
      buy_cnt = MathMax(1, InpGridOrders - 1);
   }
   
   // Build BUY LIMITS (below price - buy the dip)
   for(int i = 1; i <= buy_cnt; i++)
   {
      double target = m_bid - (grid_dist * i * point);
      double sl, tp;
      
      if(m_ai.suggested_sl > 0 && m_ai.suggested_tp > 0 && m_ai.signal == "BUY")
      {
         sl = m_ai.suggested_sl;
         tp = m_ai.suggested_tp;
      }
      else
      {
         sl = target - min_stop_dist;
         tp = target + MathMax(InpTakeProfit * point, min_stop_dist * 2);
      }
      
      m_trade.BuyLimit(lot, target, _Symbol, sl, tp);
   }
   
   // Build SELL LIMITS (above price - sell the rip)
   for(int i = 1; i <= sell_cnt; i++)
   {
      double target = m_ask + (grid_dist * i * point);
      double sl, tp;
      
      if(m_ai.suggested_sl > 0 && m_ai.suggested_tp > 0 && m_ai.signal == "SELL")
      {
         sl = m_ai.suggested_sl;
         tp = m_ai.suggested_tp;
      }
      else
      {
         sl = target + min_stop_dist;
         tp = target - MathMax(InpTakeProfit * point, min_stop_dist * 2);
      }
      
      m_trade.SellLimit(lot, target, _Symbol, sl, tp);
   }
   
   Print("Grid: BuyLim=", buy_cnt, " SellLim=", sell_cnt,
         " D=", grid_dist, "pts", 
         " L=", DoubleToString(lot, 2), " ", m_ai.signal);
}

//+------------------------------------------------------------------+
//| Process active trades                                              |
//+------------------------------------------------------------------+
void ProcessActiveTrades()
{
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   long stops_level = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   long freeze_level = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   double min_dist = MathMax(stops_level, freeze_level) * point + point * 5;
   
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      if(!m_position.SelectByIndex(i) || m_position.Magic() != InpMagicNumber || m_position.Symbol() != _Symbol)
         continue;
         
      double tick = (m_position.PositionType() == POSITION_TYPE_BUY) ? m_bid : m_ask;
      double entry = m_position.PriceOpen();
      double curr_sl = m_position.StopLoss();
      double curr_tp = m_position.TakeProfit();
      double traj = (m_position.PositionType() == POSITION_TYPE_BUY) ? 
                    (tick - entry) / point : (entry - tick) / point;
      
      if(traj < InpBreakEvenTrigger) continue;
      
      bool is_buy = (m_position.PositionType() == POSITION_TYPE_BUY);
      
      if(traj < InpTrailingStart)
      {
         // Break-even only
         double new_sl = is_buy ? entry + min_dist : entry - min_dist;
         double sl_dist = MathAbs(tick - new_sl);
         
         if(sl_dist >= min_dist && MathAbs(new_sl - curr_sl) > point * 5)
            m_trade.PositionModify(m_position.Ticket(), new_sl, curr_tp);
      }
      else
      {
         // Trailing
         double trail = is_buy ? (tick - MathMax(InpTrailingStep * point, min_dist)) :
                                 (tick + MathMax(InpTrailingStep * point, min_dist));
         double sl_dist = MathAbs(tick - trail);
         
         bool should_update = is_buy ? (trail > curr_sl + point * 3) : (trail < curr_sl - point * 3);
         
         if(sl_dist >= min_dist && should_update)
            m_trade.PositionModify(m_position.Ticket(), trail, curr_tp);
      }
   }
}

//+------------------------------------------------------------------+
//| Cancel opposite grid side                                          |
//+------------------------------------------------------------------+
void CancelOppositeGridSide()
{
   ENUM_POSITION_TYPE dir = POSITION_TYPE_BUY;
   bool found = false;
   
   for(int i = 0; i < PositionsTotal(); i++)
   {
      if(m_position.SelectByIndex(i) && m_position.Magic() == InpMagicNumber && m_position.Symbol() == _Symbol)
      {
         dir = m_position.PositionType();
         found = true;
         break;
      }
   }
   if(!found) return;
   
   for(int i = OrdersTotal()-1; i >= 0; i--)
   {
      if(m_order.SelectByIndex(i) && m_order.Magic() == InpMagicNumber && m_order.Symbol() == _Symbol)
      {
         if(dir == POSITION_TYPE_BUY && m_order.OrderType() == ORDER_TYPE_SELL_LIMIT)
            m_trade.OrderDelete(m_order.Ticket());
         if(dir == POSITION_TYPE_SELL && m_order.OrderType() == ORDER_TYPE_BUY_LIMIT)
            m_trade.OrderDelete(m_order.Ticket());
      }
   }
}

//+------------------------------------------------------------------+
//| Purge all pending orders                                           |
//+------------------------------------------------------------------+
void PurgeAllPending()
{
   for(int i = OrdersTotal()-1; i >= 0; i--)
   {
      if(m_order.SelectByIndex(i) && m_order.Magic() == InpMagicNumber && m_order.Symbol() == _Symbol)
         m_trade.OrderDelete(m_order.Ticket());
   }
}

//+------------------------------------------------------------------+
//| Risk circuit breaker                                               |
//+------------------------------------------------------------------+
bool CheckRiskCircuitBreaker()
{
   if(m_system_halted) return true;
   
   double equity = m_account.Equity();
   double balance = m_account.Balance();
   
   double daily_loss = ((m_start_day_balance - equity) / m_start_day_balance) * 100;
   double drawdown = ((balance - equity) / balance) * 100;
   
   if(daily_loss >= InpMaxDailyLossPct || drawdown >= InpMaxDrawdownPct)
   {
      for(int i = PositionsTotal()-1; i >= 0; i--)
         if(m_position.SelectByIndex(i) && m_position.Magic() == InpMagicNumber)
            m_trade.PositionClose(m_position.Ticket());
      
      PurgeAllPending();
      m_system_halted = true;
      Print("!!! CIRCUIT BREAKER: Loss=", DoubleToString(daily_loss,1), "% DD=", DoubleToString(drawdown,1), "%");
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| Day reset                                                          |
//+------------------------------------------------------------------+
void ManageDayReset()
{
   MqlDateTime dt;
   TimeCurrent(dt);
   if(dt.day_of_year != m_current_day)
   {
      m_current_day = dt.day_of_year;
      m_start_day_balance = m_account.Balance();
      m_system_halted = false;
   }
}

//+------------------------------------------------------------------+
//| Dashboard                                                          |
//+------------------------------------------------------------------+
void RefreshDashboard(string status)
{
   double profit = m_account.Equity() - m_start_day_balance;
   
   string sig_color = (m_ai.signal == "BUY") ? "[+]" : ((m_ai.signal == "SELL") ? "[-]" : "[~]");
   int pos_count = CountPositions();
   
   string ui = "========================================\n";
   ui += " DAX V2 GRID SCALPER v2.5\n";
   ui += "========================================\n";
   ui += " Balance  : $" + DoubleToString(m_account.Balance(), 2) + "\n";
   ui += " Equity   : $" + DoubleToString(m_account.Equity(), 2) + "\n";
   ui += " P/L      : $" + DoubleToString(profit, 2) + "\n";
   ui += " Positions: " + IntegerToString(pos_count) + " | Spread: " + IntegerToString((int)m_spread) + "\n";
   ui += "----------------------------------------\n";
   ui += " Signal   : " + sig_color + " " + m_ai.signal + "\n";
   ui += " RSI(14)  : " + DoubleToString(m_rsi_val, 1) + " | ATR: " + DoubleToString(m_atr_val, 5) + "\n";
   ui += " EMA20/50 : " + DoubleToString(m_ema20_val, 5) + " / " + DoubleToString(m_ema50_val, 5) + "\n";
   ui += " Risk     : " + DoubleToString(m_ai.risk_score*100, 0) + "% | Conf: " + DoubleToString(m_ai.confidence*100, 0) + "%\n";
   if(status != "") ui += " Status   : " + status + "\n";
   ui += "========================================\n";
   
   Comment(ui);
}

//+------------------------------------------------------------------+
//| JSON Helper Functions                                              |
//+------------------------------------------------------------------+
string ExtractString(string json, string key)
{
   string s = "\"" + key + "\":\"";
   int p = StringFind(json, s);
   if(p < 0) return "";
   p += StringLen(s);
   int e = StringFind(json, "\"", p);
   if(e < 0) return "";
   return StringSubstr(json, p, e - p);
}

double ExtractDouble(string json, string key)
{
   string s = "\"" + key + "\":";
   int p = StringFind(json, s);
   if(p < 0) return 0.0;
   p += StringLen(s);
   string v = "";
   while(p < StringLen(json))
   {
      ushort ch = StringGetCharacter(json, p);
      if((ch >= '0' && ch <= '9') || ch == '.' || ch == '-')
      {
         v += CharToString((uchar)ch);
         p++;
      }
      else break;
   }
   return StringToDouble(v);
}
