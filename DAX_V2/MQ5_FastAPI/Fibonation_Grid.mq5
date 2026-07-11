//+------------------------------------------------------------------+
//|                                      Fibonation_Grid.mq5         |
//|              Fibonacci Grid + Limit Orders + SL Clamping          |
//|              No backend - fully standalone                        |
//+------------------------------------------------------------------+
#property copyright "DAX V2 Fibonacci Grid"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\OrderInfo.mqh>
#include <Trade\AccountInfo.mqh>

//+------------------------------------------------------------------+
//| Fibonacci sequence for grid spacing                               |
//+------------------------------------------------------------------+
#define FIB_MAX 10
int Fibonacci[FIB_MAX] = {1, 1, 2, 3, 5, 8, 13, 21, 34, 55};

//+------------------------------------------------------------------+
//| Inputs                                                             |
//+------------------------------------------------------------------+
input group "--- Fibonacci Signal ---"
input int      InpFibHighShift     = 0;       // Daily high bar shift (0=today)
input int      InpFibLowShift      = 0;       // Daily low bar shift (0=today)
input double   InpFibBuyLevel      = 0.382;   // Buy at this Fib retracement (from low)
input double   InpFibSellLevel     = 0.618;   // Sell at this Fib retracement (from high)
input double   InpFibProximity     = 0.05;    // Proximity zone around Fib level (5%)

input group "--- Grid (Fibonacci Spacing) ---"
input double   InpBaseDistance     = 100;     // Base distance (points) * Fib multiplier
input int      InpMaxOrders        = 5;       // Max grid orders (uses Fib sequence)
input int      InpCooldownBars     = 10;      // Min bars between grid rebuilds
input int      InpMinGridPts       = 20;      // Min grid spacing (points)

input group "--- SL/TP Clamping ---"
input double   InpSlFactor         = 1.5;     // SL = base_distance * this factor
input double   InpTpFactor         = 2.0;     // TP = SL * this factor (R:R)
input int      InpSlMin            = 100;     // SL clamp MIN (points)
input int      InpSlMax            = 800;     // SL clamp MAX (points)
input int      InpTpMin            = 100;     // TP clamp MIN (points)
input int      InpTpMax            = 1200;    // TP clamp MAX (points)
input double   InpMaxSlPct         = 2.0;     // Max SL as % of price

input group "--- Trail ---"
input double   InpTrailBETrigger   = 0.5;     // Move SL to breakeven at this % of SL distance
input double   InpTrailTrigger     = 1.0;     // Start trailing at this % of SL distance
input double   InpTrailPct         = 0.4;     // Trail at this % of current profit

input group "--- Risk ---"
input double   InpLotSize          = 0.01;    // Lot size per order
input double   InpMaxDailyLossPct  = 30.0;    // Max daily loss % (circuit breaker)
input double   InpMaxDrawdownPct   = 30.0;    // Max drawdown % (circuit breaker)
input ulong    InpMagicNumber      = 770066;  // Magic number
input int      InpUpdateSec        = 5;       // Signal recalc interval (seconds)

//+------------------------------------------------------------------+
//| Globals                                                            |
//+------------------------------------------------------------------+
CTrade         m_trade;
CPositionInfo  m_position;
COrderInfo     m_order;
CAccountInfo   m_account;

double         m_start_balance;
int            m_day = -1;
bool           m_halted;
datetime       m_last_signal;
double         m_bid, m_ask, m_spread;
ENUM_ORDER_TYPE_FILLING m_fill_policy;

// Fibonacci levels (price)
double         m_fib_level[7];   // 0%, 23.6%, 38.2%, 50%, 61.8%, 78.6%, 100%
double         m_fib_high, m_fib_low, m_fib_range;

// Signal state
string         m_signal;
int            m_sl_pts;
int            m_tp_pts;
int            m_buy_orders;
int            m_sell_orders;
string         m_fib_info;

//+------------------------------------------------------------------+
int OnInit()
{
   m_trade.SetExpertMagicNumber(InpMagicNumber);
   m_start_balance = m_account.Balance();
   m_halted = false;
   m_last_signal = 0;
   m_day = -1;
   m_signal = "HOLD";
   m_sl_pts = 200;
   m_tp_pts = 400;
   m_buy_orders = 0;
   m_sell_orders = 0;

   m_fill_policy = ORDER_FILLING_RETURN;
   m_trade.SetTypeFilling(m_fill_policy);

   Print("Fibonation Grid v1.00 | Balance: $", DoubleToString(m_start_balance,2),
         " | Magic: ", InpMagicNumber,
         " | TF: ", EnumToString(Period()));
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

   // Daily reset
   MqlDateTime dt; TimeCurrent(dt);
   if(dt.day_of_year != m_day)
   {
      m_day = dt.day_of_year;
      m_start_balance = m_account.Balance();
      m_halted = false;
   }

   // Circuit breaker
   if(m_halted) return;
   double eq = m_account.Equity();
   double bal = m_account.Balance();
   if(bal > 0)
   {
      double daily_dd = ((m_start_balance - eq) / m_start_balance) * 100;
      double total_dd = ((bal - eq) / bal) * 100;
      if(daily_dd >= InpMaxDailyLossPct || total_dd >= InpMaxDrawdownPct)
      {
         CloseAll(); PurgeAll();
         m_halted = true;
         Print("!!! BREAKER: Daily DD=", DoubleToString(daily_dd,1), "% Total DD=", DoubleToString(total_dd,1), "%");
         return;
      }
   }

   // Recalculate signal periodically
   if((int)(TimeCurrent() - m_last_signal) >= InpUpdateSec)
   {
      CalcFibonacci();
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
      "========================================\n",
      " FIBONATION GRID v1.00\n",
      "========================================\n",
      " Bal: $", DoubleToString(bal,2),
      " | P/L: $", DoubleToString(pnl,2), " (", DoubleToString(pnl_pct,1), "%)\n",
      " Pos: ", CountPositions(),
      " | Orders: ", CountOrders(), "\n",
      " Signal: ", m_signal, "\n",
      " SL:", IntegerToString(m_sl_pts),
      " TP:", IntegerToString(m_tp_pts), "\n",
      " BuyLim:", IntegerToString(m_buy_orders),
      " SellLim:", IntegerToString(m_sell_orders), "\n",
      m_fib_info, "\n",
      "========================================"
   );
}

//+------------------------------------------------------------------+
//| Calculate Fibonacci retracement levels from daily range           |
//+------------------------------------------------------------------+
void CalcFibonacci()
{
   m_fib_high = iHigh(_Symbol, PERIOD_D1, InpFibHighShift);
   m_fib_low  = iLow(_Symbol, PERIOD_D1, InpFibLowShift);
   m_fib_range = m_fib_high - m_fib_low;

   if(m_fib_range <= 0)
   {
      m_fib_range = m_bid * 0.005;
      m_fib_high = m_bid + m_fib_range / 2;
      m_fib_low  = m_bid - m_fib_range / 2;
   }

   // Calculate retracement levels (from low to high)
   // Level[0] = 0% (bottom/low)
   // Level[1] = 23.6%
   // Level[2] = 38.2%
   // Level[3] = 50%
   // Level[4] = 61.8%
   // Level[5] = 78.6%
   // Level[6] = 100% (top/high)
   double fib_ratios[5] = {0.236, 0.382, 0.500, 0.618, 0.786};
   m_fib_level[0] = m_fib_low;
   for(int i = 0; i < 5; i++)
      m_fib_level[i+1] = m_fib_low + m_fib_range * fib_ratios[i];
   m_fib_level[6] = m_fib_high;
}

//+------------------------------------------------------------------+
//| Fibonacci-based signal generation                                 |
//+------------------------------------------------------------------+
void CalcSignal()
{
   double mid = (m_bid + m_ask) / 2;
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);

   // Position relative to Fib levels
   double pos_in_range = 0;
   if(m_fib_range > 0)
      pos_in_range = (mid - m_fib_low) / m_fib_range;
   if(pos_in_range < 0) pos_in_range = 0;
   if(pos_in_range > 1) pos_in_range = 1;

   // Check proximity to buy level (near 38.2% from bottom = support)
   double buy_zone = InpFibBuyLevel;
   double sell_zone = 1.0 - InpFibSellLevel;  // Convert from top

   double dist_to_buy = MathAbs(pos_in_range - buy_zone);
   double dist_to_sell = MathAbs(pos_in_range - sell_zone);

   // Signal logic
   if(dist_to_buy <= InpFibProximity && dist_to_buy < dist_to_sell)
   {
      m_signal = "BUY";
   }
   else if(dist_to_sell <= InpFibProximity && dist_to_sell < dist_to_buy)
   {
      m_signal = "SELL";
   }
   else if(pos_in_range < buy_zone - InpFibProximity)
   {
      m_signal = "BUY";  // Below all buy levels - support zone
   }
   else if(pos_in_range > 1.0 - (1.0 - sell_zone) + InpFibProximity)
   {
      m_signal = "SELL";  // Above all sell levels - resistance zone
   }
   else
   {
      m_signal = "HOLD";  // Between levels - wait
   }

   // ATR for dynamic sizing (use daily range as estimate)
   double atr = m_fib_range * 0.06;
   if(atr <= 0) atr = mid * 0.003;
   if(atr <= 0) atr = m_fib_range * 0.06;

   // Grid spacing using Fibonacci sequence
   int grid_pts = (int)MathRound(InpBaseDistance * point / 0.01);  // Convert base to points
   if(grid_pts < InpMinGridPts) grid_pts = InpMinGridPts;

   // SL clamping - Fibonacci-based
   // SL = base_distance * Fib multiplier (e.g., 3rd Fib level = 3x base)
   int sl_pts = (int)MathRound(grid_pts * InpSlFactor);
   int tp_pts = (int)MathRound(sl_pts * InpTpFactor);

   // Clamp SL to min/max
   if(sl_pts < InpSlMin) sl_pts = InpSlMin;
   if(sl_pts > InpSlMax) sl_pts = InpSlMax;

   // Clamp TP to min/max
   if(tp_pts < InpTpMin) tp_pts = InpTpMin;
   if(tp_pts > InpTpMax) tp_pts = InpTpMax;

   // Ensure R:R ratio after clamping
   int min_tp = (int)MathRound(sl_pts * InpTpFactor);
   if(tp_pts < min_tp) tp_pts = min_tp;
   if(tp_pts > InpTpMax) tp_pts = InpTpMax;

   // Max SL as % of price
   double max_sl_price = mid * (InpMaxSlPct / 100.0);
   int max_sl_pts = (int)MathRound(max_sl_price / point);
   if(sl_pts > max_sl_pts) sl_pts = max_sl_pts;

   m_sl_pts = sl_pts;
   m_tp_pts = tp_pts;

   // Directional orders (Fibonacci grid)
   if(m_signal == "BUY")
   {
      m_buy_orders = MathMin(InpMaxOrders, FIB_MAX);
      m_sell_orders = 0;
   }
   else if(m_signal == "SELL")
   {
      m_buy_orders = 0;
      m_sell_orders = MathMin(InpMaxOrders, FIB_MAX);
   }
   else
   {
      m_buy_orders = 0;
      m_sell_orders = 0;
   }

   // Build Fibonacci info string
   m_fib_info = "Fib: H=" + DoubleToString(m_fib_high,2) +
                " L=" + DoubleToString(m_fib_low,2) +
                " Pos=" + DoubleToString(pos_in_range*100,1) + "%" +
                " 38.2=" + DoubleToString(m_fib_level[2],2) +
                " 61.8=" + DoubleToString(m_fib_level[4],2);
}

//+------------------------------------------------------------------+
//| GRID MANAGEMENT - Fibonacci spacing                               |
//+------------------------------------------------------------------+
void ManageGrid()
{
   int live = CountPositions();
   int pend = CountOrders();

   if(live > 0 && pend > 0) CancelOpposite();

   if(m_signal == "HOLD") return;
   if(m_buy_orders == 0 && m_sell_orders == 0) return;

   // Rate limit by bar count
   static datetime s_last_grid_time = 0;
   int bars_since = iBars(_Symbol, Period()) - iBarShift(_Symbol, Period(), s_last_grid_time, false);
   if(bars_since < InpCooldownBars && s_last_grid_time > 0) return;

   if(live == 0 && pend == 0)
   {
      s_last_grid_time = TimeCurrent();
      BuildFibGrid();
   }
   else if(live == 0 && pend > 0)
   {
      double mid = (m_bid + m_ask) / 2;
      double nearest = GetNearestOrder();
      double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
      if(nearest > 0 && MathAbs(nearest - mid) > InpBaseDistance * 4 * point)
      {
         PurgeAll();
         s_last_grid_time = TimeCurrent();
         BuildFibGrid();
      }
   }
}

//+------------------------------------------------------------------+
//| Calculate lot size based on available margin                      |
//+------------------------------------------------------------------+
double CalcLotByMargin()
{
   double free_margin = m_account.FreeMargin();
   double min_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double lot_step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   int max_total = m_buy_orders + m_sell_orders;
   if(max_total < 1) max_total = 1;

   // Check margin for minimum lot
   double margin_1lot = 0;
   if(!OrderCalcMargin(ORDER_TYPE_BUY, _Symbol, min_lot, m_ask, margin_1lot))
   {
      long leverage = m_account.Leverage();
      double contract = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE);
      if(leverage <= 0) leverage = 100;
      margin_1lot = (m_ask * min_lot * contract) / leverage;
   }
   if(margin_1lot <= 0) return min_lot;

   // Reserve 20% for safety
   double safe_margin = free_margin * 0.80;
   double margin_per_lot = margin_1lot / min_lot;

   double max_lot_by_margin = safe_margin / (margin_per_lot * max_total);
   max_lot_by_margin = MathFloor(max_lot_by_margin / lot_step) * lot_step;

   if(max_lot_by_margin < min_lot)
   {
      max_lot_by_margin = safe_margin / margin_per_lot;
      max_lot_by_margin = MathFloor(max_lot_by_margin / lot_step) * lot_step;
   }
   if(max_lot_by_margin < min_lot) return 0;
   if(max_lot_by_margin > max_lot) max_lot_by_margin = max_lot;
   if(max_lot_by_margin > InpLotSize) max_lot_by_margin = InpLotSize;

   return NormalizeDouble(max_lot_by_margin, 2);
}

//+------------------------------------------------------------------+
//| Build Fibonacci Grid - limit orders with Fibonacci spacing       |
//+------------------------------------------------------------------+
void BuildFibGrid()
{
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double lot = CalcLotByMargin();

   if(lot < 0.01 || m_sl_pts < 10 || m_tp_pts < 10)
   {
      Print("SKIP GRID: lot=", DoubleToString(lot,2),
            " sl=", m_sl_pts, " tp=", m_tp_pts,
            " free=$", DoubleToString(m_account.FreeMargin(),2));
      return;
   }

   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
   { Print("BLOCKED: AutoTrading is OFF"); return; }
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED))
   { Print("BLOCKED: EA algo trading is OFF"); return; }

   long stops_level = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   long freeze_level = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   int min_dist = (int)MathMax(stops_level, freeze_level) + 5;

   int placed = 0;
   int failed = 0;

   // Buy Limits - Fibonacci spacing below bid
   for(int i = 0; i < m_buy_orders && i < FIB_MAX; i++)
   {
      // Fibonacci distance: base * Fibonacci[i+1]
      int fib_dist = InpBaseDistance * Fibonacci[i];
      if(fib_dist < InpMinGridPts) fib_dist = InpMinGridPts;

      double entry = NormalizeDouble(m_bid - fib_dist * point, _Digits);
      double sl = NormalizeDouble(entry - m_sl_pts * point, _Digits);
      double tp = NormalizeDouble(entry + m_tp_pts * point, _Digits);

      // SL clamping - don't let SL go below min price
      if(sl < SymbolInfoDouble(_Symbol, SYMBOL_BID) - InpSlMax * point * 10)
         sl = SymbolInfoDouble(_Symbol, SYMBOL_BID) - InpSlMax * point * 10;

      if((m_bid - entry) / point < min_dist) continue;

      // Margin check
      double margin_needed = 0;
      if(!OrderCalcMargin(ORDER_TYPE_BUY, _Symbol, lot, entry, margin_needed))
         margin_needed = lot * m_ask * 100 / m_account.Leverage();
      if(margin_needed > m_account.FreeMargin() * 0.90)
      {
         Print("BUY# ", i+1, " (Fib=", Fibonacci[i], ") SKIP: need $",
               DoubleToString(margin_needed,2), " free=$", DoubleToString(m_account.FreeMargin(),2));
         continue;
      }

      bool ok = m_trade.BuyLimit(lot, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, m_fill_policy);
      if(!ok)
      {
         ulong err = m_trade.ResultRetcode();
         Print("BUY# ", i+1, " (Fib=", Fibonacci[i], ") FAIL retcode=", err, " [", m_trade.ResultComment(), "]");

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
      if(ok)
      {
         placed++;
         Print("BUY# ", i+1, " (Fib=", Fibonacci[i], ") OK entry=", DoubleToString(entry,_Digits),
               " sl=", DoubleToString(sl,_Digits), " tp=", DoubleToString(tp,_Digits));
      }
      else failed++;
      Sleep(50);
   }

   // Sell Limits - Fibonacci spacing above ask
   for(int i = 0; i < m_sell_orders && i < FIB_MAX; i++)
   {
      int fib_dist = InpBaseDistance * Fibonacci[i];
      if(fib_dist < InpMinGridPts) fib_dist = InpMinGridPts;

      double entry = NormalizeDouble(m_ask + fib_dist * point, _Digits);
      double sl = NormalizeDouble(entry + m_sl_pts * point, _Digits);
      double tp = NormalizeDouble(entry - m_tp_pts * point, _Digits);

      // SL clamping - don't let SL go above max price
      if(sl > SymbolInfoDouble(_Symbol, SYMBOL_ASK) + InpSlMax * point * 10)
         sl = SymbolInfoDouble(_Symbol, SYMBOL_ASK) + InpSlMax * point * 10;

      if((entry - m_ask) / point < min_dist) continue;

      // Margin check
      double margin_needed = 0;
      if(!OrderCalcMargin(ORDER_TYPE_SELL, _Symbol, lot, entry, margin_needed))
         margin_needed = lot * m_ask * 100 / m_account.Leverage();
      if(margin_needed > m_account.FreeMargin() * 0.90)
      {
         Print("SELL# ", i+1, " (Fib=", Fibonacci[i], ") SKIP: need $",
               DoubleToString(margin_needed,2), " free=$", DoubleToString(m_account.FreeMargin(),2));
         continue;
      }

      bool ok = m_trade.SellLimit(lot, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, m_fill_policy);
      if(!ok)
      {
         ulong err = m_trade.ResultRetcode();
         Print("SELL# ", i+1, " (Fib=", Fibonacci[i], ") FAIL retcode=", err, " [", m_trade.ResultComment(), "]");

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
      if(ok)
      {
         placed++;
         Print("SELL# ", i+1, " (Fib=", Fibonacci[i], ") OK entry=", DoubleToString(entry,_Digits),
               " sl=", DoubleToString(sl,_Digits), " tp=", DoubleToString(tp,_Digits));
      }
      else failed++;
      Sleep(50);
   }

   Print("FIB GRID: ", m_signal, " placed=", placed, " failed=", failed,
         " lot=", DoubleToString(lot,2),
         " SL=", m_sl_pts, " TP=", m_tp_pts,
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
      if(is_buy) profit_pts = (tick - entry) / point;
      else profit_pts = (entry - tick) / point;

      double sl_dist = MathAbs(entry - curr_sl) / point;
      if(sl_dist < 10) continue;

      // Breakeven
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

      // Trail
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
