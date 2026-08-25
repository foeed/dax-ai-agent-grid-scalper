//+------------------------------------------------------------------+
//|                                           EAFastConnector_v3.mq5 |
//|              DAX V2 AI Scalper - Full Pipeline Thin Client        |
//|                         All parameters from /api/v1/scalp/plan    |
//+------------------------------------------------------------------+
#property copyright "DAX V2 AI Trading System"
#property link      ""
#property version   "3.12"

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\OrderInfo.mqh>
#include <Trade\AccountInfo.mqh>

//+------------------------------------------------------------------+
//| Plan from backend                                                  |
//+------------------------------------------------------------------+
struct ScalpPlan
{
   string    signal;
   double    lot_size;
   int       sl_pts;
   int       tp_pts;
   int       grid_pts;
   int       buy_orders;
   int       sell_orders;
   double    risk_score;
   double    confidence;
   string    risk_level;
   bool      news_caution;
   string    reasoning;
};

//+------------------------------------------------------------------+
//| Inputs                                                             |
//+------------------------------------------------------------------+
input group "--- Backend ---"
input string   InpBackendURL      = "http://127.0.0.1:8000";
input int      InpUpdateSec       = 3;     // Update every seconds (HFT)
input int      InpRequestTimeout  = 5000;

input group "--- Risk ---"
input double   InpMaxRiskPerTrade = 2.0;
input double   InpMaxDailyLossPct = 50.0;
input double   InpMaxDrawdownPct  = 50.0;
input ulong    InpMagicNumber     = 770044;

//+------------------------------------------------------------------+
//| Globals                                                            |
//+------------------------------------------------------------------+
CTrade         m_trade;
CPositionInfo  m_position;
COrderInfo     m_order;
CAccountInfo   m_account;
ScalpPlan      m_plan;
double         m_start_balance;
int            m_day = -1;
bool           m_halted;
datetime       m_last_update;
double         m_bid, m_ask, m_spread;
ENUM_ORDER_TYPE_FILLING m_fill_policy;
bool           m_market_mode;           // true = broker blocks pending orders, use market
int            m_consecutive_failures;  // track BuildGrid failures for auto-detect
datetime       m_last_grid_attempt;     // rate-limit grid builds

//+------------------------------------------------------------------+
int OnInit()
{
   m_trade.SetExpertMagicNumber(InpMagicNumber);
   m_start_balance = m_account.Balance();
   m_halted = false;
   m_last_update = 0;
   m_day = -1;
   m_market_mode = false;
   m_consecutive_failures = 0;
   m_last_grid_attempt = 0;

   // Detect broker fill policy - use RETURN first (most compatible for pending limit orders)
   // RETURN works universally; FOK/IOC only needed for some market orders
   long fill_policy = SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   m_fill_policy = ORDER_FILLING_RETURN;
   m_trade.SetTypeFilling(m_fill_policy);

   // Initialize plan with safe defaults
   m_plan.signal       = "HOLD";
   m_plan.lot_size     = 0.01;
   m_plan.sl_pts       = 50;
   m_plan.tp_pts       = 30;
   m_plan.grid_pts     = 20;
   m_plan.buy_orders   = 2;
   m_plan.sell_orders  = 2;
   m_plan.risk_score   = 0.5;
   m_plan.confidence   = 0.5;
   m_plan.risk_level   = "MEDIUM";
   m_plan.news_caution = false;
   m_plan.reasoning    = "INIT";

   Print("DAX V2 AI Scalper v3.12 | Balance: $", DoubleToString(m_start_balance,2),
         " | Magic: ", InpMagicNumber,
         " | TF: ", EnumToString(Period()),
         " | Fill: RETURN (auto)",
         " | Backend: ", InpBackendURL);
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int r) { Comment(""); }

//+------------------------------------------------------------------+
void OnTick()
{
   m_bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   m_ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   m_spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   
   // Daily reset
   MqlDateTime dt; TimeCurrent(dt);
   if(dt.day_of_year != m_day) { m_day = dt.day_of_year; m_start_balance = m_account.Balance(); m_halted = false; }
   
   // Circuit breaker
   if(m_halted) return;
   double eq = m_account.Equity();
   double bal = m_account.Balance();
   if(((m_start_balance-eq)/m_start_balance)*100 >= InpMaxDailyLossPct || 
      ((bal-eq)/bal)*100 >= InpMaxDrawdownPct)
   {
      CloseAll(); PurgeAll();
      m_halted = true;
      Print("!!! BREAKER: Loss DL=", DoubleToString(((m_start_balance-eq)/m_start_balance)*100,1),"%");
      return;
   }
   
   // Periodic plan update
   if((int)(TimeCurrent()-m_last_update) >= InpUpdateSec)
   {
      if(FetchPlan()) m_last_update = TimeCurrent();
   }
   
   // Manage grid
   ManageGrid();
   
   // Trail active positions
   TrailPositions();
   
   // Dashboard
   Comment(
      "══════════════════════════════════\n",
      " DAX V2 HFT SCALPER v3.12\n",
      "══════════════════════════════════\n",
      " Bal: $", DoubleToString(bal,2),
      " | P/L: $", DoubleToString(eq-m_start_balance,2), "\n",
      " Pos: ", CountPositions(),
      " | Orders: ", CountOrders(),
      " | ", m_plan.signal, "\n",
      " Lot:", DoubleToString(m_plan.lot_size,2),
      " Grid:", IntegerToString(m_plan.grid_pts),"pts",
      " SL:", IntegerToString(m_plan.sl_pts),
      " TP:", IntegerToString(m_plan.tp_pts),"\n",
      " BuyLim:", IntegerToString(m_plan.buy_orders),
      " SellLim:", IntegerToString(m_plan.sell_orders),
      " | ", m_plan.risk_level,
      m_market_mode ? " | MARKET MODE" : "", "\n",
      " ", m_plan.reasoning, "\n",
      "══════════════════════════════════"
   );
}

//+------------------------------------------------------------------+
bool FetchPlan()
{
   string tf = "M5";
   ENUM_TIMEFRAMES p = Period();
   if(p == PERIOD_M1) tf = "M1";
   else if(p == PERIOD_M5) tf = "M5";
   else if(p == PERIOD_M15) tf = "M15";
   else if(p == PERIOD_H1) tf = "H1";
   
   string json = "{";
   json += "\"symbol\":\"" + _Symbol + "\",";
   json += "\"bid\":" + DoubleToString(m_bid, 5) + ",";
   json += "\"ask\":" + DoubleToString(m_ask, 5) + ",";
   json += "\"spread\":" + DoubleToString(m_spread, 0) + ",";
   json += "\"volume\":" + IntegerToString((int)iVolume(_Symbol, p, 0)) + ",";
   json += "\"daily_high\":" + DoubleToString(iHigh(_Symbol, PERIOD_D1, 0), 5) + ",";
   json += "\"daily_low\":" + DoubleToString(iLow(_Symbol, PERIOD_D1, 0), 5) + ",";
   json += "\"daily_open\":" + DoubleToString(iOpen(_Symbol, PERIOD_D1, 0), 5) + ",";
   json += "\"account_balance\":" + DoubleToString(m_account.Balance(), 2) + ",";
   json += "\"account_equity\":" + DoubleToString(m_account.Equity(), 2) + ",";
   json += "\"timeframe\":\"" + tf + "\",";
   json += "\"open_positions\":" + IntegerToString(CountPositions());
   json += "}";
   
   char data[], result[];
   ArrayResize(result, 4096);
   StringToCharArray(json, data, 0, StringLen(json));
   string r_headers, headers = "Content-Type: application/json\r\n";
   string url = InpBackendURL + "/api/v1/scalp/plan";
   
   for(int a = 0; a < 2; a++)
   {
      int res = WebRequest("POST", url, headers, InpRequestTimeout, data, result, r_headers);
      if(res >= 200 && res < 300 && ArraySize(result) > 10)
      {
         string r = CharArrayToString(result);
         m_plan.signal       = Js(r, "signal");
         m_plan.lot_size     = Jd(r, "lot_size");
         m_plan.sl_pts       = (int)Jd(r, "sl_distance_pts");
         m_plan.tp_pts       = (int)Jd(r, "tp_distance_pts");
         m_plan.grid_pts     = (int)Jd(r, "grid_spacing_pts");
         m_plan.buy_orders   = (int)Jd(r, "buy_orders");
         m_plan.sell_orders  = (int)Jd(r, "sell_orders");
         m_plan.risk_score   = Jd(r, "risk_score");
         m_plan.confidence   = Jd(r, "confidence");
         m_plan.risk_level   = Js(r, "risk_level");
         m_plan.news_caution = StringFind(r, "\"news_caution\":true") >= 0;
         m_plan.reasoning    = Js(r, "reasoning");

         // Safety clamp
         if(m_plan.lot_size <= 0 || m_plan.lot_size > 0.1) m_plan.lot_size = 0.01;
         if(m_plan.sl_pts < 10)  m_plan.sl_pts = 30;
         if(m_plan.tp_pts < 10)  m_plan.tp_pts = 20;
         if(m_plan.grid_pts < 5) m_plan.grid_pts = 15;
          if(m_plan.signal != "HOLD")
          {
             if(m_plan.buy_orders < 1)  m_plan.buy_orders = 1;
             if(m_plan.sell_orders < 1) m_plan.sell_orders = 1;
          }

         Print("PLAN: ", m_plan.signal, " Lot:", DoubleToString(m_plan.lot_size,2),
               " SL:", m_plan.sl_pts, " TP:", m_plan.tp_pts,
               " Grid:", m_plan.grid_pts, " Buy:", m_plan.buy_orders,
               " Sell:", m_plan.sell_orders, " Risk:", m_plan.risk_level);
         return true;
      }
      Print("BACKEND FAIL res=", res, " attempt=", a+1);
      if(a < 1) Sleep(500);
   }
   return false;
}

//+------------------------------------------------------------------+
void ManageGrid()
{
   int live = CountPositions();
   int pend = CountOrders();
   
   // Cancel opposite grid when position exists (save one direction)
   if(live > 0 && pend > 0) CancelOpposite();
   
   // Only build grid if we have valid plan data
   bool valid = m_plan.lot_size >= 0.01 && m_plan.grid_pts >= 5 && m_plan.sl_pts >= 10;
   if(!valid) return;

   // Rate limit: don't attempt grid more than once per 3 seconds when failing
   int cooldown = m_market_mode ? 10 : 3;
   if((int)(TimeCurrent() - m_last_grid_attempt) < cooldown) return;

   // Market mode: FTMO fallback - use market orders instead of pending
   if(m_market_mode)
   {
      if(live == 0)
      {
         m_last_grid_attempt = TimeCurrent();
         BuildMarketOrders();
      }
      return;
   }

   // Pending order mode
   if(live == 0)
   {
      if(pend == 0)
      {
         m_last_grid_attempt = TimeCurrent();
         BuildGrid();
      }
      else
      {
         double mid = (m_bid + m_ask) / 2;
         double nearest = GetNearestOrder();
         double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
         if(nearest > 0 && MathAbs(nearest - mid) > m_plan.grid_pts * 4 * point)
         {
            PurgeAll();
            m_last_grid_attempt = TimeCurrent();
            BuildGrid();
         }
      }
   }
}

//+------------------------------------------------------------------+
void BuildGrid()
{
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double lot = m_plan.lot_size;
   int dist = m_plan.grid_pts;

   // Validate - don't build grid with bad data
   if(lot < 0.01 || dist < 5 || m_plan.sl_pts < 10 || m_plan.tp_pts < 10)
   {
      Print("SKIP GRID: bad data lot=", lot, " dist=", dist,
            " sl=", m_plan.sl_pts, " tp=", m_plan.tp_pts);
      return;
   }

   // Check broker allows trading
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
   {
      Print("BLOCKED: AutoTrading is OFF - enable it in MT5 toolbar");
      return;
   }
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED))
   {
      Print("BLOCKED: EA algo trading is OFF - enable in EA properties");
      return;
   }

   long stops_level = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   long freeze_level = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   int min_dist = (int)MathMax(stops_level, freeze_level) + 5;

   int placed = 0;
   int failed = 0;

   // Buy Limits below bid
   for(int i = 1; i <= m_plan.buy_orders; i++)
   {
      double entry = NormalizeDouble(m_bid - dist * i * point, _Digits);
      double sl = NormalizeDouble(entry - m_plan.sl_pts * point, _Digits);
      double tp = NormalizeDouble(entry + m_plan.tp_pts * point, _Digits);

      if((m_bid - entry) / point < min_dist) continue;

      bool ok = m_trade.BuyLimit(lot, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, m_fill_policy);
      if(!ok)
      {
         ulong err = m_trade.ResultRetcode();
         string comment = m_trade.ResultComment();
         Print("BUY# ", i, " FAIL retcode=", err, " [", comment, "] fill=", EnumToString(m_fill_policy));
         // 10017 = TRADE_RETCODE_TRADE_DISABLED - broker blocks pending orders
         if(err == 10017)
         {
            m_market_mode = true;
            Print("!!! PENDING BLOCKED (10017) - switching to MARKET ORDER MODE");
            return;
         }
         // Try other fill modes on err=4756 (invalid fill)
         if(err == 4756)
         {
            ENUM_ORDER_TYPE_FILLING modes[3] = {ORDER_FILLING_RETURN, ORDER_FILLING_FOK, ORDER_FILLING_IOC};
            for(int f = 0; f < 3; f++)
            {
               if(modes[f] == m_fill_policy) continue;
               m_fill_policy = modes[f];
               m_trade.SetTypeFilling(m_fill_policy);
               if(m_trade.BuyLimit(lot, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, m_fill_policy))
               { ok = true; Print("BUY# ", i, " RETRY OK fill=", EnumToString(m_fill_policy)); break; }
            }
         }
      }
      if(ok) placed++; else failed++;
      Sleep(50);
   }
   
   // Sell Limits above ask
   for(int i = 1; i <= m_plan.sell_orders; i++)
   {
      double entry = NormalizeDouble(m_ask + dist * i * point, _Digits);
      double sl = NormalizeDouble(entry + m_plan.sl_pts * point, _Digits);
      double tp = NormalizeDouble(entry - m_plan.tp_pts * point, _Digits);

      if((entry - m_ask) / point < min_dist) continue;

      bool ok = m_trade.SellLimit(lot, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, m_fill_policy);
      if(!ok)
      {
         ulong err = m_trade.ResultRetcode();
         string comment = m_trade.ResultComment();
         Print("SELL# ", i, " FAIL retcode=", err, " [", comment, "] fill=", EnumToString(m_fill_policy));
         // 10017 = TRADE_RETCODE_TRADE_DISABLED - broker blocks pending orders
         if(err == 10017)
         {
            m_market_mode = true;
            Print("!!! PENDING BLOCKED (10017) - switching to MARKET ORDER MODE");
            return;
         }
         if(err == 4756)
         {
            ENUM_ORDER_TYPE_FILLING modes[3] = {ORDER_FILLING_RETURN, ORDER_FILLING_FOK, ORDER_FILLING_IOC};
            for(int f = 0; f < 3; f++)
            {
               if(modes[f] == m_fill_policy) continue;
               m_fill_policy = modes[f];
               m_trade.SetTypeFilling(m_fill_policy);
               if(m_trade.SellLimit(lot, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, m_fill_policy))
               { ok = true; Print("SELL# ", i, " RETRY OK fill=", EnumToString(m_fill_policy)); break; }
            }
         }
      }
      if(ok) placed++; else failed++;
      Sleep(50);
   }

   Print("GRID: placed ", placed, " failed ", failed,
         " (stops=", stops_level, " freeze=", freeze_level, " fill=", EnumToString(m_fill_policy), ")");

   // Auto-detect broker restrictions: if ALL orders failed, track consecutive failures
   if(placed == 0 && failed > 0)
   {
      m_consecutive_failures++;
      Print("GRID FAIL #", m_consecutive_failures, " - auto-switch to market mode after 3 failures");
      if(m_consecutive_failures >= 3)
      {
         m_market_mode = true;
         Print("!!! BROKER RESTRICTION DETECTED: pending orders blocked. Switching to MARKET ORDER MODE (FTMO compatible)");
      }
   }
   else
   {
      m_consecutive_failures = 0;  // Reset on any success
   }
}

//+------------------------------------------------------------------+
//| BuildMarketOrders - FTMO fallback when pending orders blocked     |
//| Uses Buy/Sell instead of BuyLimit/SellLimit, max 1 per direction  |
//+------------------------------------------------------------------+
void BuildMarketOrders()
{
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
   {
      Print("BLOCKED: AutoTrading is OFF - enable it in MT5 toolbar");
      return;
   }
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED))
   {
      Print("BLOCKED: EA algo trading is OFF - enable in EA properties");
      return;
   }

   double lot = m_plan.lot_size;
   if(lot < 0.01) lot = 0.01;
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);

   // Only open if we have no positions
   if(CountPositions() > 0) return;

   int placed = 0;

   // Buy market order
   if(m_plan.signal == "BUY" || m_plan.signal == "HOLD")
   {
      double sl = NormalizeDouble(m_bid - m_plan.sl_pts * point, _Digits);
      double tp = NormalizeDouble(m_bid + m_plan.tp_pts * point, _Digits);

      if(m_trade.Buy(lot, _Symbol, 0, sl, tp, "DAX-MKT-BUY"))
      {
         Print("MKT BUY OK lot=", DoubleToString(lot,2),
               " sl=", sl, " tp=", tp,
               " price=", DoubleToString(m_trade.ResultPrice(), _Digits));
         placed++;
      }
      else
      {
         ulong err = m_trade.ResultRetcode();
         Print("MKT BUY FAIL retcode=", err, " [", m_trade.ResultComment(), "]");
      }
   }

   // Sell market order
   if(m_plan.signal == "SELL" || m_plan.signal == "HOLD")
   {
      double sl = NormalizeDouble(m_ask + m_plan.sl_pts * point, _Digits);
      double tp = NormalizeDouble(m_ask - m_plan.tp_pts * point, _Digits);

      if(m_trade.Sell(lot, _Symbol, 0, sl, tp, "DAX-MKT-SELL"))
      {
         Print("MKT SELL OK lot=", DoubleToString(lot,2),
               " sl=", sl, " tp=", tp,
               " price=", DoubleToString(m_trade.ResultPrice(), _Digits));
         placed++;
      }
      else
      {
         ulong err = m_trade.ResultRetcode();
         Print("MKT SELL FAIL retcode=", err, " [", m_trade.ResultComment(), "]");
      }
   }

   Print("MKT ORDERS: placed ", placed, " (market mode - pending blocked by broker)");
}

//+------------------------------------------------------------------+
void TrailPositions()
{
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      if(!m_position.SelectByIndex(i) || m_position.Magic() != InpMagicNumber || m_position.Symbol() != _Symbol)
         continue;
      
      double tick = m_position.PositionType() == POSITION_TYPE_BUY ? m_bid : m_ask;
      double entry = m_position.PriceOpen();
      double curr_sl = m_position.StopLoss();
      double curr_tp = m_position.TakeProfit();
      double profit_pts = (m_position.PositionType() == POSITION_TYPE_BUY ? (tick-entry) : (entry-tick)) / point;
      
      // Break even at 50% of SL distance
      if(profit_pts >= m_plan.sl_pts * 0.5 && curr_sl < entry)
         m_trade.PositionModify(m_position.Ticket(), entry + point * 5, curr_tp);
      
      // Trail at SL distance
      double trail = m_position.PositionType() == POSITION_TYPE_BUY ? 
                     tick - m_plan.sl_pts * point : tick + m_plan.sl_pts * point;
      if(m_position.PositionType() == POSITION_TYPE_BUY && trail > curr_sl + point * 5)
         m_trade.PositionModify(m_position.Ticket(), trail, curr_tp);
      else if(m_position.PositionType() == POSITION_TYPE_SELL && trail < curr_sl - point * 5)
         m_trade.PositionModify(m_position.Ticket(), trail, curr_tp);
   }
}

//+------------------------------------------------------------------+
void CancelOpposite()
{
   ENUM_POSITION_TYPE dir = POSITION_TYPE_BUY;
   bool f = false;
   for(int i=0; i<PositionsTotal(); i++)
      if(m_position.SelectByIndex(i) && m_position.Magic()==InpMagicNumber && m_position.Symbol()==_Symbol)
         { dir=m_position.PositionType(); f=true; break; }
   if(!f) return;
   for(int i=OrdersTotal()-1; i>=0; i--)
      if(m_order.SelectByIndex(i) && m_order.Magic()==InpMagicNumber && m_order.Symbol()==_Symbol)
      {
         if(dir==POSITION_TYPE_BUY && m_order.OrderType()==ORDER_TYPE_SELL_LIMIT) m_trade.OrderDelete(m_order.Ticket());
         if(dir==POSITION_TYPE_SELL && m_order.OrderType()==ORDER_TYPE_BUY_LIMIT) m_trade.OrderDelete(m_order.Ticket());
      }
}

void PurgeAll() { for(int i=OrdersTotal()-1; i>=0; i--) if(m_order.SelectByIndex(i) && m_order.Magic()==InpMagicNumber && m_order.Symbol()==_Symbol) m_trade.OrderDelete(m_order.Ticket()); }
void CloseAll() { for(int i=PositionsTotal()-1; i>=0; i--) if(m_position.SelectByIndex(i) && m_position.Magic()==InpMagicNumber) m_trade.PositionClose(m_position.Ticket()); }

int CountPositions() { int c=0; for(int i=PositionsTotal()-1;i>=0;i--) if(m_position.SelectByIndex(i) && m_position.Magic()==InpMagicNumber && m_position.Symbol()==_Symbol) c++; return c; }
int CountOrders() { int c=0; for(int i=OrdersTotal()-1;i>=0;i--) if(m_order.SelectByIndex(i) && m_order.Magic()==InpMagicNumber && m_order.Symbol()==_Symbol) c++; return c; }

double GetNearestOrder()
{
   double best=0, bd=DBL_MAX, mid=(m_bid+m_ask)/2;
   for(int i=0; i<OrdersTotal(); i++)
      if(m_order.SelectByIndex(i) && m_order.Magic()==InpMagicNumber && m_order.Symbol()==_Symbol)
         { double d=MathAbs(m_order.PriceOpen()-mid); if(d<bd) { bd=d; best=m_order.PriceOpen(); } }
   return best;
}

// JSON helpers
string Js(string json, string key)
{
   string s="\""+key+"\":\""; int p=StringFind(json,s); if(p<0) return "";
   p+=StringLen(s); int e=StringFind(json,"\"",p); if(e<0) return "";
   return StringSubstr(json,p,e-p);
}
double Jd(string json, string key)
{
   string s="\""+key+"\":"; int p=StringFind(json,s); if(p<0) return 0;
   p+=StringLen(s); string v="";
   while(p<StringLen(json))
   {
      ushort ch=StringGetCharacter(json,p);
      if((ch>='0'&&ch<='9')||ch=='.'||ch=='-') { v+=CharToString((uchar)ch); p++; } else break;
   }
   return StringToDouble(v);
}
