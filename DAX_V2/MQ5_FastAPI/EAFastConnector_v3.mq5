//+------------------------------------------------------------------+
//|                                           EAFastConnector_v3.mq5 |
//|              DAX V2 AI Scalper - Full Pipeline Thin Client        |
//|                         All parameters from /api/v1/scalp/plan    |
//+------------------------------------------------------------------+
#property copyright "DAX V2 AI Trading System"
#property link      ""
#property version   "3.00"

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
input int      InpUpdateSec       = 10;
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

//+------------------------------------------------------------------+
int OnInit()
{
   m_trade.SetExpertMagicNumber(InpMagicNumber);
   m_start_balance = m_account.Balance();
   m_halted = false;
   m_last_update = 0;
   m_plan.signal = "HOLD";
   Print("DAX V2 AI Scalper v3.0 | Balance: $", DoubleToString(m_start_balance,2),
         " | TF: ", EnumToString(Period()));
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
      "══════════════════════════════\n",
      " DAX V2 AI SCALPER v3.0\n",
      "══════════════════════════════\n",
      " Bal: $", DoubleToString(bal,2),
      " | Eq: $", DoubleToString(eq,2),
      " | P/L: $", DoubleToString(eq-m_start_balance,2), "\n",
      " Sig: ", m_plan.signal,
      " | Risk: ", DoubleToString(m_plan.risk_score*100,0), "%\n",
      " Lot: ", DoubleToString(m_plan.lot_size,2),
      " | Grid: ", IntegerToString(m_plan.grid_pts), "pts\n",
      " SL: ", IntegerToString(m_plan.sl_pts),
      " | TP: ", IntegerToString(m_plan.tp_pts), "pts\n",
      " Buy:", IntegerToString(m_plan.buy_orders),
      " Sell:", IntegerToString(m_plan.sell_orders),
      " | ", m_plan.risk_level, "\n",
      " News: ", m_plan.news_caution ? "CAUTION" : "OK",
      " | ", m_plan.reasoning, "\n",
      "══════════════════════════════"
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
         m_plan.signal      = Js(r, "signal");
         m_plan.lot_size    = Jd(r, "lot_size");
         m_plan.sl_pts      = (int)Jd(r, "sl_distance_pts");
         m_plan.tp_pts      = (int)Jd(r, "tp_distance_pts");
         m_plan.grid_pts    = (int)Jd(r, "grid_spacing_pts");
         m_plan.buy_orders  = (int)Jd(r, "buy_orders");
         m_plan.sell_orders = (int)Jd(r, "sell_orders");
         m_plan.risk_score  = Jd(r, "risk_score");
         m_plan.confidence  = Jd(r, "confidence");
         m_plan.risk_level  = Js(r, "risk_level");
         m_plan.news_caution= StringFind(r, "\"news_caution\":true") >= 0;
         m_plan.reasoning   = Js(r, "reasoning");
         if(m_plan.lot_size <= 0 || m_plan.lot_size > 1) m_plan.lot_size = 0.01;
         return true;
      }
      if(a < 1) Sleep(500);
   }
   return false;
}

//+------------------------------------------------------------------+
void ManageGrid()
{
   int live = CountPositions();
   int pend = CountOrders();
   
   // Cancel opposite grid when position exists
   if(live > 0 && pend > 0) CancelOpposite();
   
   // Build grid when flat
   if(live == 0)
   {
      if(pend == 0 && m_plan.signal != "HOLD")
         BuildGrid();
      else if(pend > 0)
      {
         // Check if orders are stale (price moved too far)
         double mid = (m_bid + m_ask) / 2;
         double nearest = GetNearestOrder();
         double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
         if(nearest > 0 && MathAbs(nearest - mid) > m_plan.grid_pts * 3 * point)
         {
            PurgeAll();
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
   
   // Buy Limits below bid
   for(int i = 1; i <= m_plan.buy_orders; i++)
   {
      double entry = m_bid - dist * i * point;
      double sl = entry - m_plan.sl_pts * point;
      double tp = entry + m_plan.tp_pts * point;
      m_trade.BuyLimit(lot, entry, _Symbol, sl, tp);
   }
   
   // Sell Limits above ask
   for(int i = 1; i <= m_plan.sell_orders; i++)
   {
      double entry = m_ask + dist * i * point;
      double sl = entry + m_plan.sl_pts * point;
      double tp = entry - m_plan.tp_pts * point;
      m_trade.SellLimit(lot, entry, _Symbol, sl, tp);
   }
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
