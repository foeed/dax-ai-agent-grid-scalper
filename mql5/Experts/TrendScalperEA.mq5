#property copyright ""
#property link      ""
#property version   "1.10"
#property strict

#include <Trade/Trade.mqh>

CTrade trade;

// --- Static inputs (connection details, unlikely to change at runtime) ---
input string SignalUrl = "http://127.0.0.1:8766/signal";
input string RuntimeSettingsUrl = "http://127.0.0.1:8766/api/runtime-settings";
input string SignalToken = "change-me-long-random-token";
input ENUM_TIMEFRAMES SignalTimeframe = PERIOD_M1;

// --- Runtime defaults (overridden by dashboard) ---
input bool DryRun = true;
input double Lots = 0.01;
input int MaxPositions = 1;
input int MaxSpreadPoints = 0;
input double MaxSpreadPercent = 0.5;
input int MagicNumber = 260618;
input int DeviationPoints = 20;
input bool OneTradePerBar = true;
input int CooldownSeconds = 180;
input int BarsToSend = 300;
input int RequestTimeoutMs = 30000;
input int RequestRetries = 1;
input int RetryDelayMs = 750;

// --- Runtime settings refresh interval (seconds) ---
input int SettingsRefreshSeconds = 30;

// --- Runtime settings cache (fetched from dashboard) ---
struct RuntimeConfig {
   bool dry_run;
   double lots;
   int max_positions;
   int max_spread_points;
   double max_spread_percent;
   int cooldown_seconds;
   int magic_number;
   int deviation_points;
   bool one_trade_per_bar;
   int bars_to_send;
   int request_timeout_ms;
   int request_retries;
   int retry_delay_ms;
   bool use_llm;
   bool llm_fail_closed;
};

RuntimeConfig g_runtime;

datetime last_bar_time = 0;
datetime last_trade_time = 0;
datetime last_settings_fetch = 0;
bool g_runtime_loaded = false;

int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(DeviationPoints);

   // Set defaults from inputs (fallback if API unreachable)
   g_runtime.dry_run = DryRun;
   g_runtime.lots = Lots;
   g_runtime.max_positions = MaxPositions;
   g_runtime.max_spread_points = MaxSpreadPoints;
   g_runtime.max_spread_percent = MaxSpreadPercent;
   g_runtime.cooldown_seconds = CooldownSeconds;
   g_runtime.magic_number = MagicNumber;
   g_runtime.deviation_points = DeviationPoints;
   g_runtime.one_trade_per_bar = OneTradePerBar;
   g_runtime.bars_to_send = BarsToSend;
   g_runtime.request_timeout_ms = RequestTimeoutMs;
   g_runtime.request_retries = RequestRetries;
   g_runtime.retry_delay_ms = RetryDelayMs;
   g_runtime.use_llm = false;
   g_runtime.llm_fail_closed = true;

   // Fetch runtime settings from dashboard immediately
   FetchRuntimeSettings();
   g_runtime_loaded = true;

   PrintFormat(
      "TrendScalperEA v1.10 initialized. SignalUrl=%s RuntimeSettingsUrl=%s DryRun=%s Lots=%.2f MaxPos=%d RefreshEvery=%ds",
      SignalUrl,
      RuntimeSettingsUrl,
      g_runtime.dry_run ? "true" : "false",
      g_runtime.lots,
      g_runtime.max_positions,
      SettingsRefreshSeconds
   );
   Print("Allow WebRequest URLs in MT5: http://127.0.0.1:8766");
   return INIT_SUCCEEDED;
}

void OnTick()
{
   // Periodically refresh runtime settings from dashboard
   if (TimeCurrent() - last_settings_fetch >= SettingsRefreshSeconds)
   {
      FetchRuntimeSettings();
      last_settings_fetch = TimeCurrent();
   }

   // Apply latest magic number and deviation (may have changed via dashboard)
   trade.SetExpertMagicNumber(g_runtime.magic_number);
   trade.SetDeviationInPoints(g_runtime.deviation_points);

   if (g_runtime.one_trade_per_bar)
   {
      datetime current_bar = iTime(_Symbol, SignalTimeframe, 0);
      if (current_bar == last_bar_time)
         return;
      last_bar_time = current_bar;
   }

   if (TimeCurrent() - last_trade_time < g_runtime.cooldown_seconds)
      return;

   long spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double spread_price = spread * _Point;
   double spread_pct = ask > 0 ? (spread_price / ask) * 100.0 : 0.0;

   // Absolute spread check (0 = disabled)
   if (g_runtime.max_spread_points > 0 && spread > g_runtime.max_spread_points)
   {
      PrintFormat("Spread blocked trade: %d points > %d points", spread, (int)g_runtime.max_spread_points);
      return;
   }

   // Smart spread check: use dashboard override if set, otherwise auto-detect by symbol type
   string sym_type = DetectSymbolType();
   double auto_pct = GetAutoMaxSpreadPercent(sym_type);
   double effective_pct = (g_runtime.max_spread_percent > 0) ? g_runtime.max_spread_percent : auto_pct;
   string pct_source = (g_runtime.max_spread_percent > 0) ? "dashboard" : "auto";

   if (effective_pct > 0 && spread_pct > effective_pct)
   {
      PrintFormat("Spread blocked trade: %.3f%% > %.3f%% (%s, %s type, %d points on %s ask=%.5f)",
                  spread_pct, effective_pct, pct_source, sym_type, spread, _Symbol, ask);
      return;
   }

   int positions_count = CountOwnPositions();
   if (positions_count >= g_runtime.max_positions)
   {
      PrintFormat("Position cap blocked trade: %d >= %d", positions_count, g_runtime.max_positions);
      return;
   }

   MqlRates rates[];
   int copied = CopyRates(_Symbol, SignalTimeframe, 0, g_runtime.bars_to_send, rates);
   if (copied <= 0)
   {
      PrintFormat("CopyRates failed: %d", GetLastError());
      return;
   }

   string request_body = BuildSignalRequest(rates, copied, spread, positions_count);
   string response = "";
   int status = PostJson(SignalUrl, request_body, response);
   if (status < 200 || status >= 300)
   {
      PrintFormat("Signal service HTTP status=%d response=%s", status, response);
      return;
   }

   string action = JsonString(response, "action");
   string reason = JsonString(response, "reason");
   double confidence = JsonDouble(response, "confidence", 0.0);
   int sl_points = (int)JsonDouble(response, "sl_points", 0.0);
   int tp_points = (int)JsonDouble(response, "tp_points", 0.0);

   PrintFormat("Signal action=%s confidence=%.3f reason=%s", action, confidence, reason);

   if (action == "")
   {
      PrintFormat("Signal JSON parse failed response=%s", response);
      return;
   }

   if (action != "BUY" && action != "SELL")
      return;
   if (sl_points <= 0 || tp_points <= 0)
   {
      Print("Signal missing valid SL/TP points; refusing trade");
      return;
   }

   bool success = ExecuteSignal(action, sl_points, tp_points);
   if (g_runtime.dry_run)
      return;

   if (success)
      last_trade_time = TimeCurrent();

   NotifyTradeResult(action, success, reason);
}

//+------------------------------------------------------------------+
//| Fetch runtime settings from the signal service dashboard         |
//+------------------------------------------------------------------+
void FetchRuntimeSettings()
{
   string response = "";
   int status = GetJson(RuntimeSettingsUrl, response);

   if (status < 200 || status >= 300)
   {
      PrintFormat("Runtime settings fetch failed status=%d response=%s (using cached/input defaults)", status, response);
      return;
   }

   // Parse each field from the JSON response; keep input defaults on parse failure
   g_runtime.dry_run = JsonBool(response, "dry_run", g_runtime.dry_run);
   g_runtime.lots = JsonDouble(response, "lots", g_runtime.lots);
   g_runtime.max_positions = (int)JsonDouble(response, "max_positions", g_runtime.max_positions);
   g_runtime.max_spread_points = (int)JsonDouble(response, "max_spread_points", g_runtime.max_spread_points);
   g_runtime.max_spread_percent = JsonDouble(response, "max_spread_percent", g_runtime.max_spread_percent);
   g_runtime.cooldown_seconds = (int)JsonDouble(response, "cooldown_seconds", g_runtime.cooldown_seconds);
   g_runtime.magic_number = (int)JsonDouble(response, "magic_number", g_runtime.magic_number);
   g_runtime.deviation_points = (int)JsonDouble(response, "deviation_points", g_runtime.deviation_points);
   g_runtime.one_trade_per_bar = JsonBool(response, "one_trade_per_bar", g_runtime.one_trade_per_bar);
   g_runtime.bars_to_send = (int)JsonDouble(response, "bars_to_send", g_runtime.bars_to_send);
   g_runtime.request_timeout_ms = (int)JsonDouble(response, "request_timeout_ms", g_runtime.request_timeout_ms);
   g_runtime.request_retries = (int)JsonDouble(response, "request_retries", g_runtime.request_retries);
   g_runtime.retry_delay_ms = (int)JsonDouble(response, "retry_delay_ms", g_runtime.retry_delay_ms);
   g_runtime.use_llm = JsonBool(response, "use_llm", g_runtime.use_llm);
   g_runtime.llm_fail_closed = JsonBool(response, "llm_fail_closed", g_runtime.llm_fail_closed);

   PrintFormat(
      "Runtime settings refreshed: dry_run=%s lots=%.2f max_pos=%d max_spread=%d max_spread_pct=%.2f%% cooldown=%d magic=%d dev=%d one_bar=%s bars=%d timeout=%d retries=%d use_llm=%s",
      g_runtime.dry_run ? "true" : "false",
      g_runtime.lots,
      g_runtime.max_positions,
      g_runtime.max_spread_points,
      g_runtime.max_spread_percent,
      g_runtime.cooldown_seconds,
      g_runtime.magic_number,
      g_runtime.deviation_points,
      g_runtime.one_trade_per_bar ? "true" : "false",
      g_runtime.bars_to_send,
      g_runtime.request_timeout_ms,
      g_runtime.request_retries,
      g_runtime.use_llm ? "true" : "false"
   );
}

//+------------------------------------------------------------------+
//| HTTP GET request                                                  |
//+------------------------------------------------------------------+
int GetJson(string url, string &response)
{
   char result[];
   char empty_data[];
   ArrayResize(empty_data, 0);
   string result_headers = "";
   response = "";

   string headers = "Content-Type: application/json\r\n";
   if (SignalToken != "")
      headers += "Authorization: Bearer " + SignalToken + "\r\n";

   int max_attempts = MathMax(1, g_runtime.request_retries + 1);
   int status = -1;
   int error = 0;

   for (int attempt = 1; attempt <= max_attempts; attempt++)
   {
      ArrayResize(result, 0);
      result_headers = "";
      response = "";

      ResetLastError();
      status = WebRequest("GET", url, headers, g_runtime.request_timeout_ms, empty_data, result, result_headers);
      error = GetLastError();
      response = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);

      if (status >= 200 && status < 300)
         return status;

      bool retryable = (status == -1 || status == 1003 || error == 5203);
      string details = "status=" + IntegerToString(status)
                     + " error=" + IntegerToString(error)
                     + " response=" + response
                     + " headers=" + result_headers;

      if (retryable && attempt < max_attempts)
      {
         PrintFormat("WebRequest GET attempt %d/%d failed; retrying. %s", attempt, max_attempts, details);
         Sleep(MathMax(0, g_runtime.retry_delay_ms));
         continue;
      }

      response = details;
      return status;
   }

   response = "status=" + IntegerToString(status) + " error=" + IntegerToString(error);
   return status;
}

string BuildSignalRequest(MqlRates &rates[], int copied, long spread, int positions_count)
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   string currency = AccountInfoString(ACCOUNT_CURRENCY);

   string json = "{";
   json += "\"symbol\":\"" + JsonEscape(_Symbol) + "\",";
   json += "\"timeframe\":\"" + TimeframeName(SignalTimeframe) + "\",";
   json += "\"point\":" + DoubleToString(_Point, _Digits + 2) + ",";
   json += "\"spread_points\":" + IntegerToString((int)spread) + ",";
   json += "\"positions_count\":" + IntegerToString(positions_count) + ",";
   json += "\"account\":{\"balance\":" + DoubleToString(balance, 2)
        + ",\"equity\":" + DoubleToString(equity, 2)
        + ",\"currency\":\"" + JsonEscape(currency) + "\"},";
   json += "\"rates\":[";

   bool first = true;
   for (int i = copied - 1; i >= 0; i--)
   {
      if (!first)
         json += ",";
      first = false;
      json += "{";
      json += "\"time\":\"" + TimeToString(rates[i].time, TIME_DATE | TIME_SECONDS) + "\",";
      json += "\"open\":" + DoubleToString(rates[i].open, _Digits) + ",";
      json += "\"high\":" + DoubleToString(rates[i].high, _Digits) + ",";
      json += "\"low\":" + DoubleToString(rates[i].low, _Digits) + ",";
      json += "\"close\":" + DoubleToString(rates[i].close, _Digits) + ",";
      json += "\"tick_volume\":" + IntegerToString((int)rates[i].tick_volume);
      json += "}";
   }

   json += "]}";
   return json;
}

bool ExecuteSignal(string action, int sl_points, int tp_points)
{
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double volume = NormalizeVolume(g_runtime.lots);
   bool is_buy = (action == "BUY");
   double price = is_buy ? ask : bid;
   double sl = is_buy ? price - sl_points * _Point : price + sl_points * _Point;
   double tp = is_buy ? price + tp_points * _Point : price - tp_points * _Point;

   sl = NormalizeDouble(sl, _Digits);
   tp = NormalizeDouble(tp, _Digits);

   if (g_runtime.dry_run)
   {
      PrintFormat("DRY_RUN would execute %s %.2f %s sl=%s tp=%s",
                  action, volume, _Symbol, DoubleToString(sl, _Digits), DoubleToString(tp, _Digits));
      return false;
   }

   bool sent = false;
   if (is_buy)
      sent = trade.Buy(volume, _Symbol, 0.0, sl, tp, "trend-scalper-ai");
   else
      sent = trade.Sell(volume, _Symbol, 0.0, sl, tp, "trend-scalper-ai");

   PrintFormat("Order result sent=%s retcode=%d deal=%I64u comment=%s",
               sent ? "true" : "false",
               trade.ResultRetcode(),
               trade.ResultDeal(),
               trade.ResultComment());

   return sent && trade.ResultDeal() > 0;
}

double NormalizeVolume(double requested)
{
   double min_volume = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_volume = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   if (step <= 0.0)
      step = 0.01;
   if (min_volume <= 0.0)
      min_volume = step;
   if (max_volume <= 0.0)
      max_volume = requested;

   double volume = MathMax(min_volume, MathMin(max_volume, requested));
   volume = MathFloor(volume / step) * step;
   volume = MathMax(min_volume, volume);

   int digits = 0;
   double cursor = step;
   while (cursor < 1.0 && digits < 8)
   {
      cursor *= 10.0;
      digits++;
   }

   return NormalizeDouble(volume, digits);
}

void NotifyTradeResult(string action, bool success, string reason)
{
   string result_url = SignalUrl;
   StringReplace(result_url, "/signal", "/trade-result");

   string body = "{";
   body += "\"action\":\"" + JsonEscape(action) + "\",";
   body += "\"success\":" + (success ? "true" : "false") + ",";
   body += "\"reason\":\"" + JsonEscape(reason) + "\",";
   body += "\"retcode\":" + IntegerToString((int)trade.ResultRetcode()) + ",";
   body += "\"account\":{\"balance\":" + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2)
        + ",\"equity\":" + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2)
        + ",\"currency\":\"" + JsonEscape(AccountInfoString(ACCOUNT_CURRENCY)) + "\"}";
   body += "}";

   string response = "";
   int status = PostJson(result_url, body, response);
   if (status < 200 || status >= 300)
      PrintFormat("Trade-result notification failed status=%d response=%s", status, response);
}

int PostJson(string url, string body, string &response)
{
   char data[];
   int bytes = StringToCharArray(body, data, 0, WHOLE_ARRAY, CP_UTF8);
   if (bytes > 0)
      ArrayResize(data, bytes - 1);

   char result[];
   string result_headers = "";
   string headers = "Content-Type: application/json\r\n";
   if (SignalToken != "")
      headers += "Authorization: Bearer " + SignalToken + "\r\n";

   int max_attempts = MathMax(1, g_runtime.request_retries + 1);
   int status = -1;
   int error = 0;

   for (int attempt = 1; attempt <= max_attempts; attempt++)
   {
      ArrayResize(result, 0);
      result_headers = "";
      response = "";

      ResetLastError();
      status = WebRequest("POST", url, headers, g_runtime.request_timeout_ms, data, result, result_headers);
      error = GetLastError();
      response = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);

      if (status >= 200 && status < 300)
         return status;

      bool retryable = (status == -1 || status == 1003 || error == 5203);
      string details = "status=" + IntegerToString(status)
                     + " error=" + IntegerToString(error)
                     + " response=" + response
                     + " headers=" + result_headers;

      if (retryable && attempt < max_attempts)
      {
         PrintFormat("WebRequest attempt %d/%d failed; retrying. %s", attempt, max_attempts, details);
         Sleep(MathMax(0, g_runtime.retry_delay_ms));
         continue;
      }

      response = details;
      return status;
   }

   response = "status=" + IntegerToString(status) + " error=" + IntegerToString(error);
   return status;
}

int CountOwnPositions()
{
   int count = 0;
   for (int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if (ticket == 0)
         continue;
      if (PositionGetString(POSITION_SYMBOL) == _Symbol
         && PositionGetInteger(POSITION_MAGIC) == g_runtime.magic_number)
      {
         count++;
      }
   }
   return count;
}

//+------------------------------------------------------------------+
//| Auto-detect symbol type for smart spread limits                   |
//+------------------------------------------------------------------+
string DetectSymbolType()
{
   string upper = _Symbol;
   StringToUpper(upper);

   // Gold / metals
   if (StringFind(upper, "XAU") >= 0 || StringFind(upper, "XAG") >= 0)
      return "gold";
   if (StringFind(upper, "XPD") >= 0 || StringFind(upper, "XPT") >= 0)
      return "gold";

   // Known crypto prefixes (most common MT5 crypto suffixes)
   string cryptos[] = {
      "SOL", "BTC", "ETH", "BNB", "XRP", "ADA", "DOGE", "DOT",
      "LTC", "MATIC", "AVAX", "LINK", "UNI", "ATOM", "FIL",
      "APT", "ARB", "OP", "SUI", "TRX", "TON", "NEAR", "ICP",
      "BCH", "EOS", "ETC", "VET", "AAVE", "ALGO", "MANA", "SAND",
      "AXS", "EGLD", "RUNE", "FTM", "FLOW", "GRT", "IMX", "SNX",
      "XTZ", "THETA", "ZEC", "DASH", "NEO", "QTUM", "OMG", "BAT",
      "ZRX", "ENJ", "CHZ", "CELO", "COMP", "MKR", "YFI", "CRV"
   };
   for (int i = 0; i < ArraySize(cryptos); i++)
   {
      if (StringFind(upper, cryptos[i]) == 0)
         return "crypto";
   }

   // Fallback: high point value with high price = crypto
   if (_Point >= 0.01)
   {
      double price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      if (price > 1.0)
         return "crypto";
   }

   // Forex: small point values like EURUSD=0.00001, USDJPY=0.001
   if (_Point <= 0.001 && _Point >= 0.000001)
      return "forex";

   // Indices / other
   return "other";
}

//+------------------------------------------------------------------+
//| Get auto max spread percent by symbol type                        |
//+------------------------------------------------------------------+
double GetAutoMaxSpreadPercent(string type)
{
   if (type == "gold")   return 0.15;   // XAUUSD typically 0.01-0.03%
   if (type == "forex")  return 0.30;   // EURUSD typically 0.002-0.01%
   if (type == "crypto") return 3.00;   // SOL 1.6%, BNB 0.68%, BTC 0.05%
   return 0.50;                          // other/indices
}

//+------------------------------------------------------------------+
//| JSON parsing helpers                                              |
//+------------------------------------------------------------------+
string JsonString(string json, string key)
{
   int start = JsonValueStart(json, key);
   if (start < 0)
      return "";

   if (start >= StringLen(json) || StringGetCharacter(json, start) != '"')
      return "";

   start++;
   int end = start;
   while (end < StringLen(json))
   {
      if (StringGetCharacter(json, end) == '"' && StringGetCharacter(json, end - 1) != '\\')
         break;
      end++;
   }
   return StringSubstr(json, start, end - start);
}

double JsonDouble(string json, string key, double fallback)
{
   int start = JsonValueStart(json, key);
   if (start < 0)
      return fallback;

   int end = start;
   while (end < StringLen(json))
   {
      int ch = StringGetCharacter(json, end);
      if ((ch < '0' || ch > '9') && ch != '.' && ch != '-')
         break;
      end++;
   }
   string value = StringSubstr(json, start, end - start);
   if (value == "")
      return fallback;
   return StringToDouble(value);
}

bool JsonBool(string json, string key, bool fallback)
{
   int start = JsonValueStart(json, key);
   if (start < 0)
      return fallback;

   int length = StringLen(json);
   if (start + 4 <= length && StringSubstr(json, start, 4) == "true")
      return true;
   if (start + 5 <= length && StringSubstr(json, start, 5) == "false")
      return false;

   // Try parsing as numeric (0 or 1)
   double num = JsonDouble(json, key, -1.0);
   if (num >= 0.0)
      return num != 0.0;

   return fallback;
}

int JsonValueStart(string json, string key)
{
   string marker = "\"" + key + "\"";
   int start = StringFind(json, marker);
   if (start < 0)
      return -1;

   start += StringLen(marker);
   int length = StringLen(json);
   while (start < length && IsJsonWhitespace(StringGetCharacter(json, start)))
      start++;

   if (start >= length || StringGetCharacter(json, start) != ':')
      return -1;

   start++;
   while (start < length && IsJsonWhitespace(StringGetCharacter(json, start)))
      start++;

   return start;
}

bool IsJsonWhitespace(int ch)
{
   return ch == ' ' || ch == '\t' || ch == '\r' || ch == '\n';
}

string JsonEscape(string value)
{
   string output = value;
   StringReplace(output, "\\", "\\\\");
   StringReplace(output, "\"", "\\\"");
   StringReplace(output, "\r", "\\r");
   StringReplace(output, "\n", "\\n");
   return output;
}

string TimeframeName(ENUM_TIMEFRAMES timeframe)
{
   if (timeframe == PERIOD_M1) return "M1";
   if (timeframe == PERIOD_M2) return "M2";
   if (timeframe == PERIOD_M3) return "M3";
   if (timeframe == PERIOD_M4) return "M4";
   if (timeframe == PERIOD_M5) return "M5";
   if (timeframe == PERIOD_M10) return "M10";
   if (timeframe == PERIOD_M15) return "M15";
   if (timeframe == PERIOD_M30) return "M30";
   if (timeframe == PERIOD_H1) return "H1";
   if (timeframe == PERIOD_H4) return "H4";
   if (timeframe == PERIOD_D1) return "D1";
   return IntegerToString((int)timeframe);
}