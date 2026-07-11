//+------------------------------------------------------------------+
//|                                          DAX_FibATR_Trend.mq5    |
//|     Fibonacci Grid with Adaptive ATR Spacing + Trend Filter       |
//|     Global Drawdown Protection (15% default)                      |
//|     No backend - fully standalone                                 |
//+------------------------------------------------------------------+
#property copyright "DAX V2 - FibATR Trend"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\OrderInfo.mqh>
#include <Trade\AccountInfo.mqh>

// === Fibonacci ratios for grid spacing ===
#define FIB_RATIOS_COUNT 3
double FibRatios[FIB_RATIOS_COUNT] = {1.0, 1.618, 2.618};  // Fibonacci extension ratios

// === TREND STATES ===
#define TREND_UP    1
#define TREND_DOWN -1
#define TREND_FLAT  0

//+------------------------------------------------------------------+
//| Inputs                                                             |
//+------------------------------------------------------------------+
input group "--- Trend Filter (D1) ---"
input int      InpTrendEMAPeriod   = 50;       // D1 EMA period for trend direction
input int      InpTrendLookback    = 100;      // D1 bars to load for trend calc
input int      InpTrendSlopeMin    = 3;        // Min EMA slope (points) for trend
input int      InpADXPeriod        = 14;       // ADX period on D1
input double   InpADXThreshold     = 22.0;     // ADX > this = trending, else ranging

input group "--- Adaptive ATR Spacing (M5) ---"
input int      InpAtrPeriod        = 14;       // ATR period on M5
input int      InpAtrLookback      = 20;       // M5 bars for ATR calc
input double   InpAtrSlFactor      = 1.5;      // SL = ATR * this
input double   InpAtrTpFactor      = 2.5;      // TP = SL * this (R:R 1.67)
input int      InpSlMin            = 200;      // SL clamp MIN (points)
input int      InpSlMax            = 600;      // SL clamp MAX (points)
input int      InpTpMin            = 300;      // TP clamp MIN (points)
input int      InpTpMax            = 900;      // TP clamp MAX (points)

input group "--- Grid ---"
input int      InpMaxOrders        = 2;        // Max orders per side (uses Fib ratios)
input int      InpCooldownBars     = 10;       // Min bars between grid rebuilds
input int      InpMinGridPts       = 20;       // Min grid spacing (points)
input int      InpMaxGridPts       = 500;      // Max grid spacing (points)

input group "--- Signal ---"
input double   InpBuyZone          = 0.30;     // Buy if pos < this % of daily range
input double   InpSellZone         = 0.65;     // Sell if pos > this % of daily range
input double   InpMaxSpreadPct     = 0.15;     // Max spread % to allow trading

input group "--- Trail ---"
input double   InpTrailBETrigger   = 0.7;      // BE at this % of SL distance
input double   InpTrailTrigger     = 1.2;      // Trail at this % of SL distance
input double   InpTrailPct         = 0.5;      // Lock this % of profit when trailing

input group "--- Risk ---"
input double   InpLotSize          = 0.01;     // Lot size per order
input double   InpMaxGlobalDD      = 15.0;     // Max DD from peak equity %
input ulong    InpMagicNumber      = 770077;   // Magic number
input int      InpUpdateSec        = 5;        // Signal recalc interval (seconds)

//+------------------------------------------------------------------+
//| Globals                                                            |
//+------------------------------------------------------------------+
CTrade         m_trade;
CPositionInfo  m_position;
COrderInfo     m_order;
CAccountInfo   m_account;

double         m_start_balance;
double         m_peak_equity;
int            m_day = -1;
bool           m_halted;
datetime       m_last_signal;
double         m_bid, m_ask, m_spread;
ENUM_ORDER_TYPE_FILLING m_fill_policy;

// Trend state
int            m_trend;          // TREND_UP / DOWN / FLAT
double         m_ema50_value;
double         m_adx_value;

// Signal state
string         m_signal;
int            m_sl_pts;
int            m_tp_pts;
int            m_buy_orders;
int            m_sell_orders;

// ATR value (recomputed per signal cycle)
double         m_atr_pts;
double         m_grid_base_pts;   // grid base from daily range

//+------------------------------------------------------------------+
int OnInit()
{
   m_trade.SetExpertMagicNumber(InpMagicNumber);
   m_start_balance = m_account.Balance();
   m_peak_equity   = m_account.Equity();
   m_halted = false;
   m_last_signal = 0;
   m_day = -1;
   m_trend = TREND_FLAT;
   m_signal = "HOLD";
   m_sl_pts = 300;
   m_tp_pts = 500;
   m_atr_pts = 200;
   m_buy_orders = 0;
   m_sell_orders = 0;

   m_fill_policy = ORDER_FILLING_RETURN;
   m_trade.SetTypeFilling(m_fill_policy);

   Print("FibATR Trend v1.00 | Balance: $", DoubleToString(m_start_balance,2),
         " | Magic: ", InpMagicNumber, " | TF: ", EnumToString(Period()),
         " | DD Limit: ", InpMaxGlobalDD, "%");
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int r) { Comment(""); }

//+------------------------------------------------------------------+
//| MAIN TICK                                                         |
//+------------------------------------------------------------------+
void OnTick()
{
   if(!RefreshRates()) return;
   if(CheckGlobalDD()) return;
   if(!DailyReset()) return;

   // Periodic signal + trend recalculation
   if((int)(TimeCurrent() - m_last_signal) >= InpUpdateSec)
   {
      CalcTrend();
      CalcATR();
      CalcSignal();
      m_last_signal = TimeCurrent();
   }

   ManageGrid();
   TrailPositions();
   Dashboard();
}

//+------------------------------------------------------------------+
bool RefreshRates()
{
   m_bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   m_ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   m_spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(m_bid <= 0 || m_ask <= 0) return false;
   return true;
}

//+------------------------------------------------------------------+
//| Global Drawdown Circuit Breaker                                    |
//+------------------------------------------------------------------+
bool CheckGlobalDD()
{
   if(m_halted) return true;

   double eq = m_account.Equity();
   if(eq > m_peak_equity) m_peak_equity = eq;

   if(m_peak_equity <= 0) return false;

   double dd_pct = ((m_peak_equity - eq) / m_peak_equity) * 100.0;
   if(dd_pct >= InpMaxGlobalDD)
   {
      CloseAll();
      PurgeAll();
      m_halted = true;
      Print("!!! GLOBAL DD BREAKER: DD=", DoubleToString(dd_pct,1),
            "% (limit=", InpMaxGlobalDD, "%) Peak=$", DoubleToString(m_peak_equity,2),
            " Eq=$", DoubleToString(eq,2));
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
bool DailyReset()
{
   MqlDateTime dt; TimeCurrent(dt);
   if(dt.day_of_year != m_day)
   {
      m_day = dt.day_of_year;
      m_start_balance = m_account.Balance();
      m_peak_equity   = m_account.Equity();
      m_halted = false;
   }
   return !m_halted;
}

//+------------------------------------------------------------------+
//| Compute D1 EMA(50) slope + ADX(14) for trend filtering            |
//+------------------------------------------------------------------+
void CalcTrend()
{
   double d1_close[], d1_high[], d1_low[];
   ArraySetAsSeries(d1_close, true);
   ArraySetAsSeries(d1_high, true);
   ArraySetAsSeries(d1_low, true);

   int copied = CopyClose(_Symbol, PERIOD_D1, 0, InpTrendLookback, d1_close);
   if(copied < InpTrendEMAPeriod + 10) { m_trend = TREND_FLAT; m_ema50_value = 0; m_adx_value = 0; return; }

   CopyHigh(_Symbol, PERIOD_D1, 0, InpTrendLookback, d1_high);
   CopyLow(_Symbol, PERIOD_D1, 0, InpTrendLookback, d1_low);

   // --- EMA(50) on D1 close ---
   double alpha = 2.0 / (InpTrendEMAPeriod + 1.0);
   double ema = d1_close[copied - 1];  // start with oldest close
   for(int j = copied - 2; j >= 0; j--)
      ema = d1_close[j] * alpha + ema * (1.0 - alpha);
   m_ema50_value = ema;

    // EMA slope over 3 bars (approximate as ema - ema_3)
    double slope = 0;
    double ema3 = d1_close[copied - 1];
    int slope_end = (int)MathMax(0, copied - 4);
    for(int s = copied - 2; s >= slope_end; s--)
       ema3 = d1_close[s] * alpha + ema3 * (1.0 - alpha);
    slope = (ema - ema3) / 3.0;

   // --- ADX(14) on D1 ---
   m_adx_value = ComputeADX(d1_high, d1_low, d1_close, copied);

   // --- Trend decision ---
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double slope_pts = slope / point;
   bool trending = (m_adx_value > InpADXThreshold);
   bool above_ema = (d1_close[0] > m_ema50_value);

   if(trending && above_ema && slope_pts > InpTrendSlopeMin)
      m_trend = TREND_UP;
   else if(trending && !above_ema && slope_pts < -InpTrendSlopeMin)
      m_trend = TREND_DOWN;
   else
      m_trend = TREND_FLAT;
}

//+------------------------------------------------------------------+
double ComputeADX(double &high[], double &low[], double &close[], int count)
{
   if(count < InpADXPeriod + 2) return 20.0;

   double tr_sum = 0, plus_dm_sum = 0, minus_dm_sum = 0;
   double prev_high = high[count - 1], prev_low = low[count - 1], prev_close = close[count - 1];

   // Seed: first InpADXPeriod bars
   for(int j = count - 2; j >= count - 1 - InpADXPeriod && j >= 0; j--)
   {
      double tr = MathMax(high[j] - low[j],
                   MathMax(MathAbs(high[j] - prev_close),
                           MathAbs(low[j] - prev_close)));
      tr_sum += tr;
      double up_move = high[j] - prev_high;
      double dn_move = prev_low - low[j];
      double plus_dm = (up_move > dn_move && up_move > 0) ? up_move : 0;
      double minus_dm = (dn_move > up_move && dn_move > 0) ? dn_move : 0;
      plus_dm_sum += plus_dm;
      minus_dm_sum += minus_dm;
      prev_high = high[j];
      prev_low = low[j];
      prev_close = close[j];
   }

   double atr14 = tr_sum / InpADXPeriod;
   if(atr14 <= 0) return 20.0;

   double di_plus = 100.0 * (plus_dm_sum / InpADXPeriod) / atr14;
   double di_minus = 100.0 * (minus_dm_sum / InpADXPeriod) / atr14;
   double di_sum = di_plus + di_minus;
   if(di_sum <= 0) return 20.0;

   double dx = 100.0 * MathAbs(di_plus - di_minus) / di_sum;

   // Smooth DX with SMA again
   double adx = dx;
   int smooth_count = 1;
   for(int j = count - 1 - InpADXPeriod; j >= count - 1 - InpADXPeriod * 2 && j >= 0; j--)
   {
      double tr2 = MathMax(high[j] - low[j],
                    MathMax(MathAbs(high[j] - prev_close),
                            MathAbs(low[j] - prev_close)));
      double up2 = high[j] - prev_high;
      double dn2 = prev_low - low[j];
      double pdm = (up2 > dn2 && up2 > 0) ? up2 : 0;
      double mdm = (dn2 > up2 && dn2 > 0) ? dn2 : 0;
      prev_high = high[j]; prev_low = low[j]; prev_close = close[j];

      double atr_s = (tr_sum - tr_sum/InpADXPeriod + tr2) / InpADXPeriod;
      tr_sum = tr_sum - tr_sum/InpADXPeriod + tr2;
      if(atr_s > 0)
      {
         double plus_s = (plus_dm_sum - plus_dm_sum/InpADXPeriod + pdm) / InpADXPeriod;
         double minus_s = (minus_dm_sum - minus_dm_sum/InpADXPeriod + mdm) / InpADXPeriod;
         plus_dm_sum = plus_dm_sum - plus_dm_sum/InpADXPeriod + pdm;
         minus_dm_sum = minus_dm_sum - minus_dm_sum/InpADXPeriod + mdm;
         double di_p = 100.0 * plus_s / atr_s;
         double di_m = 100.0 * minus_s / atr_s;
         double ds = di_p + di_m;
         if(ds > 0)
         {
            double dx2 = 100.0 * MathAbs(di_p - di_m) / ds;
            adx = adx * smooth_count / (smooth_count + 1) + dx2 / (smooth_count + 1);
            smooth_count++;
         }
      }
   }

   // Fallback: simple estimate if not enough data
   if(smooth_count < 2) adx = 25.0;  // default to slightly trending

   return adx;
}

//+------------------------------------------------------------------+
//| Compute ATR(14) on M5 from bar data (no indicator handles)        |
//+------------------------------------------------------------------+
void CalcATR()
{
   double high[], low[], close_arr[];
   ArraySetAsSeries(high, true);
   ArraySetAsSeries(low, true);
   ArraySetAsSeries(close_arr, true);

   int copied = CopyHigh(_Symbol, PERIOD_M5, 0, InpAtrLookback, high);
   if(copied < InpAtrPeriod + 2) { m_atr_pts = 200; return; }

   CopyLow(_Symbol, PERIOD_M5, 0, InpAtrLookback, low);
   CopyClose(_Symbol, PERIOD_M5, 0, InpAtrLookback, close_arr);

   double tr_sum = 0;
   double prev_close = close_arr[InpAtrPeriod];  // bar at index = ATR period back

   for(int j = 0; j < InpAtrPeriod && j < copied - 1; j++)
   {
      double tr = MathMax(high[j] - low[j],
                   MathMax(MathAbs(high[j] - prev_close),
                           MathAbs(low[j] - prev_close)));
      tr_sum += tr;
      prev_close = close_arr[j];
   }

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double atr_price = tr_sum / InpAtrPeriod;
   m_atr_pts = atr_price / point;

   // Floor/ceiling
   if(m_atr_pts < 50) m_atr_pts = 50;
   if(m_atr_pts > 800) m_atr_pts = 800;
}

//+------------------------------------------------------------------+
//| Signal generation with trend filter                                |
//+------------------------------------------------------------------+
void CalcSignal()
{
   double mid = (m_bid + m_ask) / 2;
   if(mid <= 0) mid = 1.0;
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);

   // Daily range position
   double dhigh = iHigh(_Symbol, PERIOD_D1, 0);
   double dlow  = iLow(_Symbol, PERIOD_D1, 0);
   double daily_range = dhigh - dlow;
   if(daily_range <= 0) daily_range = mid * 0.005;

   double pos_in_range = (mid - dlow) / daily_range;
   if(pos_in_range < 0) pos_in_range = 0;
   if(pos_in_range > 1) pos_in_range = 1;

   // Spread check
   double spread_pct = (m_spread * point) / mid * 100.0;

   // Base signal (optimized M5 logic)
   string base_signal = "HOLD";
   if(spread_pct < InpMaxSpreadPct)
   {
      if(pos_in_range < InpBuyZone)      base_signal = "BUY";
      else if(pos_in_range > InpSellZone) base_signal = "SELL";
      else                                base_signal = "HOLD";
   }

   // --- APPLY TREND FILTER ---
   m_signal = base_signal;
   if(m_trend == TREND_UP && base_signal == "SELL")
      m_signal = "HOLD";   // suppress counter-trend sells
   else if(m_trend == TREND_DOWN && base_signal == "BUY")
      m_signal = "HOLD";   // suppress counter-trend buys

    // --- SL/TP (optimized M5 formula: daily_range * 0.432 / point) ---
    m_sl_pts = (int)MathRound(daily_range * 0.432 / point);
    m_tp_pts = (int)MathRound(m_sl_pts * 1.4);

   if(m_sl_pts < InpSlMin) m_sl_pts = InpSlMin;
   if(m_sl_pts > InpSlMax) m_sl_pts = InpSlMax;
   if(m_tp_pts < InpTpMin) m_tp_pts = InpTpMin;
   if(m_tp_pts > InpTpMax) m_tp_pts = InpTpMax;

    // R:R floor after clamping (1.4 like optimized)
    int min_tp = (int)MathRound(m_sl_pts * 1.4);
   if(m_tp_pts < min_tp) m_tp_pts = min_tp;
   if(m_tp_pts > InpTpMax) m_tp_pts = InpTpMax;

   // Max SL as % of price
   double max_sl_price = mid * 0.02;
   int max_sl_pts = (int)MathRound(max_sl_price / point);
   if(m_sl_pts > max_sl_pts) m_sl_pts = max_sl_pts;

   // Grid base from daily range (same as optimized atr*0.3)
   m_grid_base_pts = daily_range * 0.018 / point;
   if(m_grid_base_pts < InpMinGridPts) m_grid_base_pts = InpMinGridPts;
   if(m_grid_base_pts > InpMaxGridPts) m_grid_base_pts = InpMaxGridPts;

   // Directional orders
   if(m_signal == "BUY")
   {
      m_buy_orders = MathMin(InpMaxOrders, FIB_RATIOS_COUNT);
      m_sell_orders = 0;
   }
   else if(m_signal == "SELL")
   {
      m_buy_orders = 0;
      m_sell_orders = MathMin(InpMaxOrders, FIB_RATIOS_COUNT);
   }
   else
   {
      m_buy_orders = 0;
      m_sell_orders = 0;
   }
}

//+------------------------------------------------------------------+
void ManageGrid()
{
   int live = CountPositions();
   int pend = CountOrders();

   // Cancel opposite orders when positions exist
   if(live > 0 && pend > 0) CancelOpposite();

   // Cancel stale pending orders if signal direction changed
   if(live == 0 && pend > 0 && m_signal != "HOLD")
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

   if(m_signal == "HOLD") return;
   if(m_buy_orders == 0 && m_sell_orders == 0) return;

   // Rate limit by bar count (cooldown)
   static datetime s_last_grid_time = 0;
   int bars_since = iBars(_Symbol, Period()) - iBarShift(_Symbol, Period(), s_last_grid_time, false);
   if(bars_since < InpCooldownBars && s_last_grid_time > 0) return;

   if(live == 0 && pend == 0)
   {
      s_last_grid_time = TimeCurrent();
      BuildFibATRGrid();
   }
   else if(live == 0 && pend > 0)
   {
      double mid = (m_bid + m_ask) / 2;
      double nearest = GetNearestOrder();
      double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
      if(nearest > 0 && MathAbs(nearest - mid) > m_atr_pts * 2.5 * point)
      {
         PurgeAll();
         s_last_grid_time = TimeCurrent();
         BuildFibATRGrid();
      }
   }
}

//+------------------------------------------------------------------+
//| Margin-aware lot sizing                                            |
//+------------------------------------------------------------------+
double CalcLotByMargin()
{
   double free_margin = m_account.FreeMargin();
   double min_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double lot_step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   int max_total = m_buy_orders + m_sell_orders;
   if(max_total < 1) max_total = 1;

   double margin_1lot = 0;
   if(!OrderCalcMargin(ORDER_TYPE_BUY, _Symbol, min_lot, m_ask, margin_1lot))
   {
      long leverage = m_account.Leverage();
      double contract = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE);
      if(leverage <= 0) leverage = 100;
      margin_1lot = (m_ask * min_lot * contract) / leverage;
   }
   if(margin_1lot <= 0) return min_lot;

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
//| Build Fibonacci ATR Grid                                           |
//+------------------------------------------------------------------+
void BuildFibATRGrid()
{
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double lot = CalcLotByMargin();

   if(lot < 0.01 || m_sl_pts < 10 || m_tp_pts < 10)
   {
      Print("SKIP FIB ATR GRID: lot=", DoubleToString(lot,2),
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

   // --- Buy Limits (adaptive Fibonacci spacing below bid) ---
   for(int i = 0; i < m_buy_orders && i < FIB_RATIOS_COUNT; i++)
   {
      int fib_dist = (int)MathRound(m_grid_base_pts * FibRatios[i]);
      if(fib_dist < InpMinGridPts) fib_dist = InpMinGridPts;
      if(fib_dist > InpMaxGridPts) fib_dist = InpMaxGridPts;

      double entry = NormalizeDouble(m_bid - fib_dist * point, _Digits);
      double sl = NormalizeDouble(entry - m_sl_pts * point, _Digits);
      double tp = NormalizeDouble(entry + m_tp_pts * point, _Digits);

      if((m_bid - entry) / point < min_dist) continue;

      double margin_needed = 0;
      if(!OrderCalcMargin(ORDER_TYPE_BUY, _Symbol, lot, entry, margin_needed))
         margin_needed = lot * m_ask * 100 / m_account.Leverage();
      if(margin_needed > m_account.FreeMargin() * 0.90)
      {
         Print("BUY Fib ", DoubleToString(FibRatios[i],3), " SKIP: need $",
               DoubleToString(margin_needed,2), " free=$", DoubleToString(m_account.FreeMargin(),2));
         continue;
      }

      bool ok = m_trade.BuyLimit(lot, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, m_fill_policy);
      if(!ok)
      {
         ulong err = m_trade.ResultRetcode();
         Print("BUY Fib ", DoubleToString(FibRatios[i],3), " FAIL retcode=", err, " [", m_trade.ResultComment(), "]");
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
      if(ok) placed++;
      else
      {
         failed++;
         if(m_trade.ResultRetcode() == 10017)
         { Print("!!! PENDING BLOCKED (10017) - enable algo trading"); return; }
      }
      Sleep(50);
   }

   // --- Sell Limits (adaptive Fibonacci spacing above ask) ---
   for(int i = 0; i < m_sell_orders && i < FIB_RATIOS_COUNT; i++)
   {
      int fib_dist = (int)MathRound(m_grid_base_pts * FibRatios[i]);
      if(fib_dist < InpMinGridPts) fib_dist = InpMinGridPts;
      if(fib_dist > InpMaxGridPts) fib_dist = InpMaxGridPts;

      double entry = NormalizeDouble(m_ask + fib_dist * point, _Digits);
      double sl = NormalizeDouble(entry + m_sl_pts * point, _Digits);
      double tp = NormalizeDouble(entry - m_tp_pts * point, _Digits);

      if((entry - m_ask) / point < min_dist) continue;

      double margin_needed = 0;
      if(!OrderCalcMargin(ORDER_TYPE_SELL, _Symbol, lot, entry, margin_needed))
         margin_needed = lot * m_ask * 100 / m_account.Leverage();
      if(margin_needed > m_account.FreeMargin() * 0.90)
      {
         Print("SELL Fib ", DoubleToString(FibRatios[i],3), " SKIP: need $",
               DoubleToString(margin_needed,2), " free=$", DoubleToString(m_account.FreeMargin(),2));
         continue;
      }

      bool ok = m_trade.SellLimit(lot, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, m_fill_policy);
      if(!ok)
      {
         ulong err = m_trade.ResultRetcode();
         Print("SELL Fib ", DoubleToString(FibRatios[i],3), " FAIL retcode=", err, " [", m_trade.ResultComment(), "]");
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
      if(ok) placed++;
      else
      {
         failed++;
         if(m_trade.ResultRetcode() == 10017)
         { Print("!!! PENDING BLOCKED (10017) - enable algo trading"); return; }
      }
      Sleep(50);
   }

   Print("FIB ATR GRID: trend=", (m_trend == TREND_UP ? "UP" : (m_trend == TREND_DOWN ? "DOWN" : "FLAT")),
         " sig=", m_signal, " placed=", placed, " failed=", failed,
         " lot=", DoubleToString(lot,2),
         " ATR=", DoubleToString(m_atr_pts,0),
         " SL=", m_sl_pts, " TP=", m_tp_pts,
         " free=$", DoubleToString(m_account.FreeMargin(),2));
}

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
void Dashboard()
{
   double eq = m_account.Equity();
   double bal = m_account.Balance();
   double pnl = eq - m_start_balance;
   double pnl_pct = (m_start_balance > 0) ? (pnl / m_start_balance) * 100 : 0;

   string trend_str = (m_trend == TREND_UP) ? "UP" : ((m_trend == TREND_DOWN) ? "DOWN" : "FLAT");

   Comment(
      "========================================\n",
      " FibATR Trend v1.00", m_halted ? " [HALTED]" : "", "\n",
      "========================================\n",
      " Bal: $", DoubleToString(bal,2),
      " | P/L: $", DoubleToString(pnl,2), " (", DoubleToString(pnl_pct,1), "%)\n",
      " Peak: $", DoubleToString(m_peak_equity,2),
      " | Trend: ", trend_str, " ADX:", DoubleToString(m_adx_value,1), "\n",
      " Pos: ", CountPositions(),
      " | Orders: ", CountOrders(),
      " | Sig: ", m_signal, "\n",
      " ATR: ", DoubleToString(m_atr_pts,0), "pts",
      " SL:", IntegerToString(m_sl_pts),
      " TP:", IntegerToString(m_tp_pts),
      " R:R=", DoubleToString((double)m_tp_pts / MathMax(1, m_sl_pts), 2), "\n",
      " BuyLim:", IntegerToString(m_buy_orders),
      " SellLim:", IntegerToString(m_sell_orders), "\n",
      "========================================"
   );
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
