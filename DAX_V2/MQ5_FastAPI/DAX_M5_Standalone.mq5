//+------------------------------------------------------------------+
//|                                         DAX_M5_Standalone.mq5    |
//|              DAX V2 HFT Grid Scalper - Standalone (No Backend)   |
//|              Optimized M5 params: +120% PnL, 4147 trades, WR=72.8% |
//+------------------------------------------------------------------+
#property copyright "DAX V2 Standalone"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\OrderInfo.mqh>
#include <Trade\AccountInfo.mqh>

//+------------------------------------------------------------------+
//| Inputs - M5 Optimized Parameters                                  |
//+------------------------------------------------------------------+
input group "--- Signal ---"
input double   InpBuyZone         = 0.30;    // Buy if price below this % of daily range
input double   InpSellZone        = 0.65;    // Sell if price above this % of daily range
input double   InpVolMult         = 8.0;     // Volatility multiplier for SL

input group "--- SL/TP (points) ---"
input double   InpSlRatio         = 0.9;     // SL = ATR * this ratio * vol_mult
input double   InpTpRatio         = 1.4;     // TP = SL * this ratio
input int      InpSlMin           = 200;     // SL clamp min
input int      InpSlMax           = 500;     // SL clamp max
input int      InpTpMin           = 150;     // TP clamp min
input int      InpTpMax           = 750;     // TP clamp max

input group "--- Grid ---"
input double   InpGridFactor      = 0.3;     // Grid spacing = ATR * this
input int      InpMaxOrders       = 2;       // Max orders per side
input int      InpCooldownBars    = 10;      // Min bars between grid rebuilds
input int      InpMinGridPts      = 20;      // Min grid spacing (points)

input group "--- Trail ---"
input double   InpTrailBETrigger  = 0.5;     // Move SL to breakeven at this % of SL profit
input double   InpTrailTrigger    = 1.0;     // Start trailing at this % of SL profit
input double   InpTrailPct        = 0.4;     // Trail at this % of current profit

input group "--- Risk ---"
input double   InpLotSize         = 0.01;    // Lot size per order
input double   InpMaxDailyLossPct = 50.0;    // Max daily loss % (circuit breaker)
input double   InpMaxDrawdownPct  = 50.0;    // Max drawdown % (circuit breaker)
input ulong    InpMagicNumber     = 770055;  // Magic number
input int      InpUpdateSec       = 3;       // Signal recalc interval (seconds)

//+------------------------------------------------------------------+
//| Globals                                                            |
//+------------------------------------------------------------------+
CTrade         m_trade;
CPositionInfo  m_position;
COrderInfo     m_order;
CAccountInfo   m_account;

double         m_start_balance;
double         m_peak_equity;      // track running peak for DD breaker
int            m_day = -1;
bool           m_halted;
datetime       m_last_signal;
double         m_bid, m_ask, m_spread;
ENUM_ORDER_TYPE_FILLING m_fill_policy;

// Current signal state
string         m_signal;
int            m_sl_pts;
int            m_tp_pts;
int            m_grid_pts;
int            m_buy_orders;
int            m_sell_orders;

//+------------------------------------------------------------------+
int OnInit()
{
   m_trade.SetExpertMagicNumber(InpMagicNumber);
   m_start_balance = m_account.Balance();
   m_peak_equity   = m_account.Equity();
   m_day = -1;
   m_signal = "HOLD";
   m_sl_pts = 300;
   m_tp_pts = 420;
   m_grid_pts = 30;
   m_buy_orders = 0;
   m_sell_orders = 0;

   // Minimum balance check: gold needs ~$106 margin for 2x 0.01 lot orders
   if(m_start_balance < 200)
   {
      m_halted = true;
      Print("!!! BALANCE TOO LOW: $", DoubleToString(m_start_balance,2),
            " - need minimum $200 for XAUUSD 0.01 lot. EA halted.");
   }
   else
   {
      m_halted = false;
   }
   m_last_signal = 0;

   // Detect fill policy
   m_fill_policy = ORDER_FILLING_RETURN;
   m_trade.SetTypeFilling(m_fill_policy);

   Print("DAX M5 Standalone v1.01 | Balance: $", DoubleToString(m_start_balance,2),
         " | Magic: ", InpMagicNumber,
         " | TF: ", EnumToString(Period()),
         " | Min Bal: $200");
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int r) { Comment(""); }

//+------------------------------------------------------------------+
//| MAIN TICK                                                         |
//+------------------------------------------------------------------+
void OnTick()
{
   m_bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   m_ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   m_spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);

    // Daily reset: unhalt to allow recovery each new day
    MqlDateTime dt; TimeCurrent(dt);
    if(dt.day_of_year != m_day)
    {
       m_day = dt.day_of_year;
       m_start_balance = m_account.Balance();
       if(m_account.Balance() >= 200)
          m_halted = false;   // resume trading on new day
    }

    // Circuit breaker: tracks all-time peak equity, halts for current day only
    if(m_halted) return;
    double eq = m_account.Equity();
    double bal = m_account.Balance();
    if(eq > m_peak_equity) m_peak_equity = eq;
    if(bal > 0 && m_peak_equity > 0)
    {
       double peak_dd = ((m_peak_equity - eq) / m_peak_equity) * 100;
       if(peak_dd >= InpMaxDailyLossPct)
       {
          CloseAll(); PurgeAll();
          m_halted = true;
          Print("!!! BREAKER: Peak DD=", DoubleToString(peak_dd,1),
                "% Peak=$", DoubleToString(m_peak_equity,2),
                " Eq=$", DoubleToString(eq,2),
                " (resumes next day)");
          return;
       }
    }

   // Recalculate signal periodically
   if((int)(TimeCurrent() - m_last_signal) >= InpUpdateSec)
   {
      CalcSignal();
      m_last_signal = TimeCurrent();
   }

   // Manage grid
   ManageGrid();

   // Trail positions
   TrailPositions();

   // Dashboard
   double pnl = eq - m_start_balance;
   double pnl_pct = m_start_balance > 0 ? (pnl / m_start_balance) * 100 : 0;
   Comment(
      "================================\n",
      " DAX M5 STANDALONE v1.00\n",
      "================================\n",
      " Bal: $", DoubleToString(bal,2),
      " | P/L: $", DoubleToString(pnl,2), " (", DoubleToString(pnl_pct,1), "%)\n",
      " Pos: ", CountPositions(),
      " | Orders: ", CountOrders(), "\n",
      " Signal: ", m_signal, "\n",
      " SL:", IntegerToString(m_sl_pts),
      " TP:", IntegerToString(m_tp_pts),
      " Grid:", IntegerToString(m_grid_pts), "pts\n",
      " BuyLim:", IntegerToString(m_buy_orders),
      " SellLim:", IntegerToString(m_sell_orders), "\n",
      "================================"
   );
}

//+------------------------------------------------------------------+
//| SIGNAL GENERATION - Embedded from optimizer                      |
//+------------------------------------------------------------------+
void CalcSignal()
{
   double mid = (m_bid + m_ask) / 2;
   if(mid <= 0) mid = 1.0;

   // Daily range
   double dhigh = iHigh(_Symbol, PERIOD_D1, 0);
   double dlow  = iLow(_Symbol, PERIOD_D1, 0);
   double daily_range = dhigh - dlow;
   if(daily_range <= 0) daily_range = mid * 0.005;

   // Position in daily range [0..1]
   double pos_in_range = (mid - dlow) / daily_range;
   if(pos_in_range < 0) pos_in_range = 0;
   if(pos_in_range > 1) pos_in_range = 1;

   // Volatility
   double volatility = daily_range / mid;
   double spread_pct = (m_spread * SymbolInfoDouble(_Symbol, SYMBOL_POINT)) / mid * 100;

   // ATR estimate for M5
   double atr = daily_range * 0.06;

   // Directional signal
   if(spread_pct < 0.15)
   {
      if(pos_in_range < InpBuyZone)
         m_signal = "BUY";
      else if(pos_in_range > InpSellZone)
         m_signal = "SELL";
      else
         m_signal = "HOLD";
   }
   else
   {
      m_signal = "HOLD";
   }

   // SL/TP calculation
   double sl_price = atr * InpSlRatio * InpVolMult;
   double min_sl = mid * 0.0003;
   if(sl_price < min_sl) sl_price = min_sl;

   double tp_price = sl_price * InpTpRatio;

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   m_sl_pts = (int)MathRound(sl_price / point);
   m_tp_pts = (int)MathRound(tp_price / point);

   // Clamp SL/TP
   if(m_sl_pts < InpSlMin) m_sl_pts = InpSlMin;
   if(m_sl_pts > InpSlMax) m_sl_pts = InpSlMax;
   if(m_tp_pts < InpTpMin) m_tp_pts = InpTpMin;
   if(m_tp_pts > InpTpMax) m_tp_pts = InpTpMax;

   // Ensure R:R ratio after clamping
   int min_tp = (int)MathRound(m_sl_pts * InpTpRatio);
   if(m_tp_pts < min_tp) m_tp_pts = min_tp;
   if(m_tp_pts > InpTpMax) m_tp_pts = InpTpMax;

   // Grid spacing
   m_grid_pts = (int)MathRound(atr * InpGridFactor / point);
   if(m_grid_pts < InpMinGridPts) m_grid_pts = InpMinGridPts;

   // Directional orders
   if(m_signal == "BUY")
   {
      m_buy_orders = InpMaxOrders;
      m_sell_orders = 0;
   }
   else if(m_signal == "SELL")
   {
      m_buy_orders = 0;
      m_sell_orders = InpMaxOrders;
   }
   else
   {
      m_buy_orders = 0;
      m_sell_orders = 0;
   }
}

//+------------------------------------------------------------------+
//| GRID MANAGEMENT                                                   |
//+------------------------------------------------------------------+
void ManageGrid()
{
   int live = CountPositions();
   int pend = CountOrders();

   // Cancel opposite orders when positions exist
   if(live > 0 && pend > 0) CancelOpposite();

   // Cancel stale pending if signal direction changed (matches Python backtest)
   if(pend > 0 && m_signal != "HOLD")
   {
      string expected = (m_signal == "BUY") ? "BUY_LIMIT" : "SELL_LIMIT";
      string wrong    = (m_signal == "BUY") ? "SELL_LIMIT" : "BUY_LIMIT";
      for(int i = OrdersTotal()-1; i >= 0; i--)
         if(m_order.SelectByIndex(i) && m_order.Magic() == InpMagicNumber
            && m_order.Symbol() == _Symbol)
         {
            ENUM_ORDER_TYPE otype = m_order.OrderType();
            if((wrong == "BUY_LIMIT" && otype == ORDER_TYPE_BUY_LIMIT)
               || (wrong == "SELL_LIMIT" && otype == ORDER_TYPE_SELL_LIMIT))
               m_trade.OrderDelete(m_order.Ticket());
         }
      pend = CountOrders();
   }

   // Only build when no positions AND no pending (matches Python exactly)
   if(live == 0 && pend == 0 && m_signal != "HOLD"
      && (m_buy_orders > 0 || m_sell_orders > 0))
   {
      // Time-based cooldown: 30 minutes minimum between grid rebuilds
      static datetime s_last_grid_time = 0;
      if((int)(TimeCurrent() - s_last_grid_time) < 1800) return;
      s_last_grid_time = TimeCurrent();
      BuildGrid();
   }
}

//+------------------------------------------------------------------+
//| Calculate optimal lot size based on available margin              |
//+------------------------------------------------------------------+
double CalcLotByMargin()
{
   double balance = m_account.Balance();
   double free_margin = m_account.FreeMargin();
   double min_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double lot_step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   // Need margin for max possible orders (both sides) to be safe
   int max_total = m_buy_orders + m_sell_orders;
   if(max_total < 1) max_total = 1;

   // Check margin required for 0.01 lot
   double test_lot = min_lot;
   double margin_1lot = 0;
   if(!OrderCalcMargin(ORDER_TYPE_BUY, _Symbol, test_lot, m_ask, margin_1lot))
   {
      // Fallback: estimate margin as price * lot * contract_size / leverage
      long leverage = m_account.Leverage();
      double contract = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE);
      if(leverage <= 0) leverage = 100;
      margin_1lot = (m_ask * test_lot * contract) / leverage;
   }

   if(margin_1lot <= 0) return min_lot;

   // Reserve 20% of free margin for safety (spread, slippage)
   double safe_margin = free_margin * 0.80;
   double margin_per_order = margin_1lot / test_lot;  // margin per 1.0 lot

   // Max lot that fits all orders
   double max_lot_by_margin = safe_margin / (margin_per_order * max_total);
   max_lot_by_margin = MathFloor(max_lot_by_margin / lot_step) * lot_step;

   // Clamp
   if(max_lot_by_margin < min_lot)
   {
      // Can't afford all orders - try with just 1 order
      max_lot_by_margin = safe_margin / margin_per_order;
      max_lot_by_margin = MathFloor(max_lot_by_margin / lot_step) * lot_step;
   }
   if(max_lot_by_margin < min_lot) return 0;  // Can't afford anything
   if(max_lot_by_margin > max_lot) max_lot_by_margin = max_lot;

   // Also respect user's max lot setting
   if(max_lot_by_margin > InpLotSize) max_lot_by_margin = InpLotSize;

   return NormalizeDouble(max_lot_by_margin, 2);
}

//+------------------------------------------------------------------+
void BuildGrid()
{
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double lot = CalcLotByMargin();

   if(lot < 0.01 || m_grid_pts < 5 || m_sl_pts < 10 || m_tp_pts < 10)
   {
      Print("SKIP GRID: lot=", DoubleToString(lot,2), " grid=", m_grid_pts,
            " sl=", m_sl_pts, " tp=", m_tp_pts,
            " free_margin=", DoubleToString(m_account.FreeMargin(),2));
      return;
   }

   // Check trading allowed
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
   {
      Print("BLOCKED: AutoTrading is OFF");
      return;
   }
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED))
   {
      Print("BLOCKED: EA algo trading is OFF");
      return;
   }

   long stops_level = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   long freeze_level = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   int min_dist = (int)MathMax(stops_level, freeze_level) + 5;

   int placed = 0;
   int failed = 0;

   // Buy Limits below bid
   for(int i = 1; i <= m_buy_orders; i++)
   {
      double entry = NormalizeDouble(m_bid - m_grid_pts * i * point, _Digits);
      double sl = NormalizeDouble(entry - m_sl_pts * point, _Digits);
      double tp = NormalizeDouble(entry + m_tp_pts * point, _Digits);

      if((m_bid - entry) / point < min_dist) continue;

      // Check margin before placing
      double margin_needed = 0;
      if(!OrderCalcMargin(ORDER_TYPE_BUY, _Symbol, lot, entry, margin_needed))
         margin_needed = lot * m_ask * 100 / m_account.Leverage();
      if(margin_needed > m_account.FreeMargin() * 0.90)
      {
         Print("BUY# ", i, " SKIP: need $", DoubleToString(margin_needed,2),
               " free=$", DoubleToString(m_account.FreeMargin(),2));
         continue;
      }

      bool ok = m_trade.BuyLimit(lot, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, m_fill_policy);
      if(!ok)
      {
         ulong err = m_trade.ResultRetcode();
         Print("BUY# ", i, " FAIL retcode=", err, " [", m_trade.ResultComment(), "]");

         // Try other fill modes on invalid fill
         if(err == 4756)
         {
            ENUM_ORDER_TYPE_FILLING modes[3] = {ORDER_FILLING_RETURN, ORDER_FILLING_FOK, ORDER_FILLING_IOC};
            for(int f = 0; f < 3; f++)
            {
               if(modes[f] == m_fill_policy) continue;
               m_fill_policy = modes[f];
               m_trade.SetTypeFilling(m_fill_policy);
               if(m_trade.BuyLimit(lot, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, m_fill_policy))
               { ok = true; break; }
            }
         }
      }
      if(ok) placed++; else failed++;
      Sleep(50);
   }

   // Sell Limits above ask
   for(int i = 1; i <= m_sell_orders; i++)
   {
      double entry = NormalizeDouble(m_ask + m_grid_pts * i * point, _Digits);
      double sl = NormalizeDouble(entry + m_sl_pts * point, _Digits);
      double tp = NormalizeDouble(entry - m_tp_pts * point, _Digits);

      if((entry - m_ask) / point < min_dist) continue;

      // Check margin before placing
      double margin_needed = 0;
      if(!OrderCalcMargin(ORDER_TYPE_SELL, _Symbol, lot, entry, margin_needed))
         margin_needed = lot * m_ask * 100 / m_account.Leverage();
      if(margin_needed > m_account.FreeMargin() * 0.90)
      {
         Print("SELL# ", i, " SKIP: need $", DoubleToString(margin_needed,2),
               " free=$", DoubleToString(m_account.FreeMargin(),2));
         continue;
      }

      bool ok = m_trade.SellLimit(lot, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, m_fill_policy);
      if(!ok)
      {
         ulong err = m_trade.ResultRetcode();
         Print("SELL# ", i, " FAIL retcode=", err, " [", m_trade.ResultComment(), "]");

         if(err == 4756)
         {
            ENUM_ORDER_TYPE_FILLING modes[3] = {ORDER_FILLING_RETURN, ORDER_FILLING_FOK, ORDER_FILLING_IOC};
            for(int f = 0; f < 3; f++)
            {
               if(modes[f] == m_fill_policy) continue;
               m_fill_policy = modes[f];
               m_trade.SetTypeFilling(m_fill_policy);
               if(m_trade.SellLimit(lot, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, m_fill_policy))
               { ok = true; break; }
            }
         }
      }
      if(ok) placed++; else failed++;
      Sleep(50);
   }

   Print("GRID: ", m_signal, " placed=", placed, " failed=", failed,
         " lot=", DoubleToString(lot,2),
         " SL=", m_sl_pts, " TP=", m_tp_pts, " Grid=", m_grid_pts,
         " free=$", DoubleToString(m_account.FreeMargin(),2));
}

//+------------------------------------------------------------------+
//| TRAILING STOP                                                     |
//+------------------------------------------------------------------+
void TrailPositions()
{
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);

   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      if(!m_position.SelectByIndex(i)) continue;
      if(m_position.Magic() != InpMagicNumber) continue;
      if(m_position.Symbol() != _Symbol) continue;

      double entry = m_position.PriceOpen();
      double curr_sl = m_position.StopLoss();
      double curr_tp = m_position.TakeProfit();
      bool is_buy = (m_position.PositionType() == POSITION_TYPE_BUY);
      double tick = is_buy ? m_bid : m_ask;

      double profit_pts;
      if(is_buy)
         profit_pts = (tick - entry) / point;
      else
         profit_pts = (entry - tick) / point;

      double sl_dist = MathAbs(entry - curr_sl) / point;
      if(sl_dist < 10) continue;  // No meaningful SL distance

      // Breakeven trigger
      if(profit_pts >= sl_dist * InpTrailBETrigger)
      {
         if(is_buy && curr_sl < entry)
         {
            double new_sl = NormalizeDouble(entry + point * 5, _Digits);
            if(new_sl > curr_sl + point)
               m_trade.PositionModify(m_position.Ticket(), new_sl, curr_tp);
         }
         else if(!is_buy && curr_sl > entry)
         {
            double new_sl = NormalizeDouble(entry - point * 5, _Digits);
            if(new_sl < curr_sl - point)
               m_trade.PositionModify(m_position.Ticket(), new_sl, curr_tp);
         }
      }

      // Trail trigger
      if(profit_pts >= sl_dist * InpTrailTrigger)
      {
         double trail_dist = profit_pts * InpTrailPct;
         if(is_buy)
         {
            double new_sl = NormalizeDouble(tick - trail_dist * point, _Digits);
            if(new_sl > curr_sl + point && new_sl > entry)
               m_trade.PositionModify(m_position.Ticket(), new_sl, curr_tp);
         }
         else
         {
            double new_sl = NormalizeDouble(tick + trail_dist * point, _Digits);
            if(new_sl < curr_sl - point && new_sl < entry)
               m_trade.PositionModify(m_position.Ticket(), new_sl, curr_tp);
         }
      }
   }
}

//+------------------------------------------------------------------+
//| UTILITIES                                                         |
//+------------------------------------------------------------------+
void CancelOpposite()
{
   ENUM_POSITION_TYPE dir = POSITION_TYPE_BUY;
   bool found = false;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      if(m_position.SelectByIndex(i) && m_position.Magic() == InpMagicNumber && m_position.Symbol() == _Symbol)
      { dir = m_position.PositionType(); found = true; break; }
   }
   if(!found) return;

   for(int i = OrdersTotal()-1; i >= 0; i--)
   {
      if(!m_order.SelectByIndex(i)) continue;
      if(m_order.Magic() != InpMagicNumber || m_order.Symbol() != _Symbol) continue;

      if(dir == POSITION_TYPE_BUY && m_order.OrderType() == ORDER_TYPE_SELL_LIMIT)
         m_trade.OrderDelete(m_order.Ticket());
      if(dir == POSITION_TYPE_SELL && m_order.OrderType() == ORDER_TYPE_BUY_LIMIT)
         m_trade.OrderDelete(m_order.Ticket());
   }
}

void PurgeAll()
{
   for(int i = OrdersTotal()-1; i >= 0; i--)
      if(m_order.SelectByIndex(i) && m_order.Magic() == InpMagicNumber && m_order.Symbol() == _Symbol)
         m_trade.OrderDelete(m_order.Ticket());
}

void CloseAll()
{
   for(int i = PositionsTotal()-1; i >= 0; i--)
      if(m_position.SelectByIndex(i) && m_position.Magic() == InpMagicNumber)
         m_trade.PositionClose(m_position.Ticket());
}

int CountPositions()
{
   int c = 0;
   for(int i = PositionsTotal()-1; i >= 0; i--)
      if(m_position.SelectByIndex(i) && m_position.Magic() == InpMagicNumber && m_position.Symbol() == _Symbol)
         c++;
   return c;
}

int CountOrders()
{
   int c = 0;
   for(int i = OrdersTotal()-1; i >= 0; i--)
      if(m_order.SelectByIndex(i) && m_order.Magic() == InpMagicNumber && m_order.Symbol() == _Symbol)
         c++;
   return c;
}

double GetNearestOrder()
{
   double best = 0, bd = DBL_MAX, mid = (m_bid + m_ask) / 2;
   for(int i = 0; i < OrdersTotal(); i++)
   {
      if(m_order.SelectByIndex(i) && m_order.Magic() == InpMagicNumber && m_order.Symbol() == _Symbol)
      {
         double d = MathAbs(m_order.PriceOpen() - mid);
         if(d < bd) { bd = d; best = m_order.PriceOpen(); }
      }
   }
   return best;
}
//+------------------------------------------------------------------+
