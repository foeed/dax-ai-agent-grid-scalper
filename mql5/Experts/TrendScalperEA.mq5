#property copyright ""
#property link      ""
#property version   "1.00"
#property strict

#include <Trade/Trade.mqh>

CTrade trade;

input string SignalUrl = "http://127.0.0.1:8766/signal";
input string SignalToken = "change-me-long-random-token";
input ENUM_TIMEFRAMES SignalTimeframe = PERIOD_M1;
input int BarsToSend = 300;
input double Lots = 0.01;
input int MaxPositions = 1;
input int MaxSpreadPoints = 0;
input int MagicNumber = 260618;
input int DeviationPoints = 20;
input bool DryRun = true;
input bool OneTradePerBar = true;
input int CooldownSeconds = 180;
input int RequestTimeoutMs = 30000;
input int RequestRetries = 1;
input int RetryDelayMs = 750;

datetime last_bar_time = 0;
datetime last_trade_time = 0;

int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(DeviationPoints);
   PrintFormat(
      "TrendScalperEA initialized. Url=%s DryRun=%s RequestTimeoutMs=%d RequestRetries=%d. Allow WebRequest URL in MT5: http://127.0.0.1:8766",
      SignalUrl,
      DryRun ? "true" : "false",
      RequestTimeoutMs,
      RequestRetries
   );
   return INIT_SUCCEEDED;
}

void OnTick()
{
   if(OneTradePerBar)
   {
      datetime current_bar = iTime(_Symbol, SignalTimeframe, 0);
      if(current_bar == last_bar_time)
         return;
      last_bar_time = current_bar;
   }

   if(TimeCurrent() - last_trade_time < CooldownSeconds)
      return;

   long spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(MaxSpreadPoints > 0 && spread > MaxSpreadPoints)
   {
      PrintFormat("Spread blocked trade: %d > %d", spread, MaxSpreadPoints);
      return;
   }

   int positions_count = CountOwnPositions();
   if(positions_count >= MaxPositions)
   {
      PrintFormat("Position cap blocked trade: %d >= %d", positions_count, MaxPositions);
      return;
   }

   MqlRates rates[];
   int copied = CopyRates(_Symbol, SignalTimeframe, 0, BarsToSend, rates);
   if(copied <= 0)
   {
      PrintFormat("CopyRates failed: %d", GetLastError());
      return;
   }

   string request_body = BuildSignalRequest(rates, copied, spread, positions_count);
   string response = "";
   int status = PostJson(SignalUrl, request_body, response);
   if(status < 200 || status >= 300)
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

   if(action == "")
   {
      PrintFormat("Signal JSON parse failed response=%s", response);
      return;
   }

   if(action != "BUY" && action != "SELL")
      return;
   if(sl_points <= 0 || tp_points <= 0)
   {
      Print("Signal missing valid SL/TP points; refusing trade");
      return;
   }

   bool success = ExecuteSignal(action, sl_points, tp_points);
   if(DryRun)
      return;

   if(success)
      last_trade_time = TimeCurrent();

   NotifyTradeResult(action, success, reason);
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
   for(int i = copied - 1; i >= 0; i--)
   {
      if(!first)
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
   double volume = NormalizeVolume(Lots);
   bool is_buy = (action == "BUY");
   double price = is_buy ? ask : bid;
   double sl = is_buy ? price - sl_points * _Point : price + sl_points * _Point;
   double tp = is_buy ? price + tp_points * _Point : price - tp_points * _Point;

   sl = NormalizeDouble(sl, _Digits);
   tp = NormalizeDouble(tp, _Digits);

   if(DryRun)
   {
      PrintFormat("DRY_RUN would execute %s %.2f %s sl=%s tp=%s",
                  action, volume, _Symbol, DoubleToString(sl, _Digits), DoubleToString(tp, _Digits));
      return false;
   }

   bool sent = false;
   if(is_buy)
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

   if(step <= 0.0)
      step = 0.01;
   if(min_volume <= 0.0)
      min_volume = step;
   if(max_volume <= 0.0)
      max_volume = requested;

   double volume = MathMax(min_volume, MathMin(max_volume, requested));
   volume = MathFloor(volume / step) * step;
   volume = MathMax(min_volume, volume);

   int digits = 0;
   double cursor = step;
   while(cursor < 1.0 && digits < 8)
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
   if(status < 200 || status >= 300)
      PrintFormat("Trade-result notification failed status=%d response=%s", status, response);
}

int PostJson(string url, string body, string &response)
{
   char data[];
   int bytes = StringToCharArray(body, data, 0, WHOLE_ARRAY, CP_UTF8);
   if(bytes > 0)
      ArrayResize(data, bytes - 1);

   char result[];
   string result_headers = "";
   string headers = "Content-Type: application/json\r\n";
   if(SignalToken != "")
      headers += "Authorization: Bearer " + SignalToken + "\r\n";

   int max_attempts = MathMax(1, RequestRetries + 1);
   int status = -1;
   int error = 0;

   for(int attempt = 1; attempt <= max_attempts; attempt++)
   {
      ArrayResize(result, 0);
      result_headers = "";
      response = "";

      ResetLastError();
      status = WebRequest("POST", url, headers, RequestTimeoutMs, data, result, result_headers);
      error = GetLastError();
      response = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);

      if(status >= 200 && status < 300)
         return status;

      bool retryable = (status == -1 || status == 1003 || error == 5203);
      string details = "status=" + IntegerToString(status)
                     + " error=" + IntegerToString(error)
                     + " response=" + response
                     + " headers=" + result_headers;

      if(retryable && attempt < max_attempts)
      {
         PrintFormat("WebRequest attempt %d/%d failed; retrying. %s", attempt, max_attempts, details);
         Sleep(MathMax(0, RetryDelayMs));
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
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol
         && PositionGetInteger(POSITION_MAGIC) == MagicNumber)
      {
         count++;
      }
   }
   return count;
}

string JsonString(string json, string key)
{
   int start = JsonValueStart(json, key);
   if(start < 0)
      return "";

   if(start >= StringLen(json) || StringGetCharacter(json, start) != '"')
      return "";

   start++;
   int end = start;
   while(end < StringLen(json))
   {
      if(StringGetCharacter(json, end) == '"' && StringGetCharacter(json, end - 1) != '\\')
         break;
      end++;
   }
   return StringSubstr(json, start, end - start);
}

double JsonDouble(string json, string key, double fallback)
{
   int start = JsonValueStart(json, key);
   if(start < 0)
      return fallback;

   int end = start;
   while(end < StringLen(json))
   {
      int ch = StringGetCharacter(json, end);
      if((ch < '0' || ch > '9') && ch != '.' && ch != '-')
         break;
      end++;
   }
   string value = StringSubstr(json, start, end - start);
   if(value == "")
      return fallback;
   return StringToDouble(value);
}

int JsonValueStart(string json, string key)
{
   string marker = "\"" + key + "\"";
   int start = StringFind(json, marker);
   if(start < 0)
      return -1;

   start += StringLen(marker);
   int length = StringLen(json);
   while(start < length && IsJsonWhitespace(StringGetCharacter(json, start)))
      start++;

   if(start >= length || StringGetCharacter(json, start) != ':')
      return -1;

   start++;
   while(start < length && IsJsonWhitespace(StringGetCharacter(json, start)))
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
   if(timeframe == PERIOD_M1) return "M1";
   if(timeframe == PERIOD_M2) return "M2";
   if(timeframe == PERIOD_M3) return "M3";
   if(timeframe == PERIOD_M4) return "M4";
   if(timeframe == PERIOD_M5) return "M5";
   if(timeframe == PERIOD_M10) return "M10";
   if(timeframe == PERIOD_M15) return "M15";
   if(timeframe == PERIOD_M30) return "M30";
   if(timeframe == PERIOD_H1) return "H1";
   if(timeframe == PERIOD_H4) return "H4";
   if(timeframe == PERIOD_D1) return "D1";
   return IntegerToString((int)timeframe);
}
