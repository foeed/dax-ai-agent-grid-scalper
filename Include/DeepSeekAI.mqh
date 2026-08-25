//+------------------------------------------------------------------+
//|                                                DeepSeekAI.mqh    |
//|                          DeepSeek AI Integration Module           |
//|                         Version 2.0 - DAX V2 System               |
//+------------------------------------------------------------------+
#property copyright "DAX V2 AI Trading System"
#property link      ""
#property version   "2.00"

#include <Trade\Trade.mqh>

//+------------------------------------------------------------------+
//| AI Analysis Result Structure                                       |
//+------------------------------------------------------------------+
struct AIAnalysisResult
{
   double       risk_score;        // 0.0 (low risk) to 1.0 (high risk)
   double       confidence;        // AI confidence level 0.0 to 1.0
   ENUM_SIGNAL_TYPE signal;        // Recommended action
   string       reasoning;         // AI reasoning text
   double       suggested_sl;      // Suggested stop loss
   double       suggested_tp;      // Suggested take profit
   double       position_size;     // Recommended position size
   datetime     analysis_time;     // When analysis was performed
};

//+------------------------------------------------------------------+
//| Signal Type Enum                                                   |
//+------------------------------------------------------------------+
enum ENUM_SIGNAL_TYPE
{
   SIGNAL_NONE = 0,        // No Signal
   SIGNAL_BUY = 1,         // Buy Signal
   SIGNAL_SELL = 2,        // Sell Signal
   SIGNAL_HOLD = 3         // Hold/Wait Signal
};

//+------------------------------------------------------------------+
//| Market Data Structure for AI                                       |
//+------------------------------------------------------------------+
struct MarketDataForAI
{
   string       symbol;
   double       current_price;
   double       bid;
   double       ask;
   double       spread;
   double       atr_14;
   double       rsi_14;
   double       macd_signal;
   double       ema_20;
   double       ema_50;
   double       ema_200;
   double       volume;
   double       daily_high;
   double       daily_low;
   double       daily_open;
   string       news_summary;
   double       news_sentiment;
};

//+------------------------------------------------------------------+
//| DeepSeek AI Handler                                                |
//+------------------------------------------------------------------+
class CDeepSeekAI
{
private:
   string         m_api_key;
   string         m_base_url;
   string         m_model;
   bool           m_is_connected;
   int            m_max_tokens;
   double         m_temperature;
   
   // Analysis cache
   AIAnalysisResult m_last_analysis;
   datetime       m_last_analysis_time;
   int            m_analysis_cache_seconds;
   
   // Risk parameters
   double         m_max_risk_per_trade;
   double         m_max_daily_risk;
   double         m_risk_reward_ratio;
   
public:
   //+------------------------------------------------------------------+
   //| Constructor                                                        |
   //+------------------------------------------------------------------+
   CDeepSeekAI()
   {
      m_api_key = "";
      m_base_url = "https://api.deepseek.com/v1/";
      m_model = "deepseek-chat";
      m_is_connected = false;
      m_max_tokens = 1000;
      m_temperature = 0.3; // Low temperature for consistent analysis
      
      m_last_analysis_time = 0;
      m_analysis_cache_seconds = 300; // 5 minute cache
      
      // Default risk parameters
      m_max_risk_per_trade = 0.02;  // 2% max risk per trade
      m_max_daily_risk = 0.05;      // 5% max daily risk
      m_risk_reward_ratio = 2.0;    // 2:1 risk-reward ratio
   }
   
   //+------------------------------------------------------------------+
   //| Initialize with API key                                            |
   //+------------------------------------------------------------------+
   bool Initialize(string api_key)
   {
      m_api_key = api_key;
      
      if(api_key == "" || api_key == "YOUR_API_KEY_HERE")
      {
         Print("DeepSeekAI: No API key provided - using built-in analysis");
         m_is_connected = false;
         return true; // Allow to continue without API
      }
      
      // Test connection
      if(TestConnection())
      {
         m_is_connected = true;
         Print("DeepSeekAI: Connected successfully");
         return true;
      }
      
      Print("DeepSeekAI: Connection failed - using built-in analysis");
      return true; // Allow to continue without API
   }
   
   //+------------------------------------------------------------------+
   //| Analyze market conditions with AI                                  |
   //+------------------------------------------------------------------+
   AIAnalysisResult AnalyzeMarket(MarketDataForAI &market_data)
   {
      // Check cache first
      if(IsAnalysisCached())
      {
         return m_last_analysis;
      }
      
      AIAnalysisResult result;
      
      if(m_is_connected)
      {
         // Use DeepSeek API for advanced analysis
         result = AnalyzeWithDeepSeek(market_data);
      }
      else
      {
         // Use built-in technical analysis
         result = AnalyzeWithBuiltInLogic(market_data);
      }
      
      // Cache the result
      m_last_analysis = result;
      m_last_analysis_time = TimeCurrent();
      
      return result;
   }
   
   //+------------------------------------------------------------------+
   //| Get risk-adjusted position size                                    |
   //+------------------------------------------------------------------+
   double CalculateRiskAdjustedSize(double account_balance, double stop_loss_distance, 
                                   double current_price, double risk_score)
   {
      // Base risk calculation
      double risk_amount = account_balance * m_max_risk_per_trade;
      
      // Adjust risk based on AI risk score
      double adjusted_risk = risk_amount * (1.0 - (risk_score * 0.5)); // Reduce risk by up to 50%
      
      // Calculate position size based on stop loss distance
      double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
      double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
      
      if(tick_value == 0 || tick_size == 0 || stop_loss_distance == 0)
      {
         return 0.01; // Minimum lot size
      }
      
      double sl_ticks = stop_loss_distance / tick_size;
      double lot_size = adjusted_risk / (sl_ticks * tick_value);
      
      // Normalize to broker requirements
      double min_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
      double max_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
      double lot_step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
      
      if(lot_step > 0)
      {
         lot_size = MathFloor(lot_size / lot_step) * lot_step;
      }
      
      lot_size = MathMax(min_lot, MathMin(max_lot, lot_size));
      
      // Additional safety: never risk more than calculated
      if(risk_score > 0.7)
      {
         lot_size = min_lot; // Minimum size for high risk
      }
      
      return NormalizeDouble(lot_size, 2);
   }
   
   //+------------------------------------------------------------------+
   //| Calculate dynamic stop loss based on volatility                    |
   //+------------------------------------------------------------------+
   double CalculateDynamicStopLoss(double entry_price, bool is_buy, 
                                   double atr, double risk_score)
   {
      // Base stop loss from ATR
      double atr_multiplier = 1.5;
      
      // Adjust multiplier based on risk score
      if(risk_score > 0.7)
      {
         atr_multiplier = 2.0; // Wider stops for high risk
      }
      else if(risk_score < 0.3)
      {
         atr_multiplier = 1.0; // Tighter stops for low risk
      }
      
      double stop_distance = atr * atr_multiplier;
      
      // Apply minimum/maximum bounds
      double min_stop = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * 
                       SymbolInfoDouble(_Symbol, SYMBOL_POINT);
      double max_stop = entry_price * 0.02; // Max 2% stop loss
      
      stop_distance = MathMax(min_stop, MathMin(max_stop, stop_distance));
      
      // Calculate stop loss price
      double stop_loss;
      if(is_buy)
      {
         stop_loss = entry_price - stop_distance;
      }
      else
      {
         stop_loss = entry_price + stop_distance;
      }
      
      return NormalizeDouble(stop_loss, _Digits);
   }
   
   //+------------------------------------------------------------------+
   //| Calculate take profit based on risk-reward ratio                   |
   //+------------------------------------------------------------------+
   double CalculateTakeProfit(double entry_price, double stop_loss, bool is_buy)
   {
      double risk = MathAbs(entry_price - stop_loss);
      double reward = risk * m_risk_reward_ratio;
      
      double take_profit;
      if(is_buy)
      {
         take_profit = entry_price + reward;
      }
      else
      {
         take_profit = entry_price - reward;
      }
      
      return NormalizeDouble(take_profit, _Digits);
   }
   
   //+------------------------------------------------------------------+
   //| Get market regime description                                      |
   //+------------------------------------------------------------------+
   string GetMarketRegime(MarketDataForAI &data)
   {
      // Determine market regime based on indicators
      bool trending = false;
      bool volatile_market = false;
      bool ranging = false;
      
      // Check if price is above/below EMAs
      if(data.current_price > data.ema_200)
      {
         trending = (data.ema_20 > data.ema_50) && (data.ema_50 > data.ema_200);
      }
      else
      {
         trending = (data.ema_20 < data.ema_50) && (data.ema_50 < data.ema_200);
      }
      
      // Check volatility
      volatile_market = data.atr_14 > (data.current_price * 0.01); // ATR > 1%
      
      // Check if ranging
      ranging = !trending && !volatile_market;
      
      if(trending && volatile_market) return "TRENDING_VOLATILE";
      if(trending) return "TRENDING";
      if(volatile_market) return "VOLATILE_RANGING";
      if(ranging) return "RANGING";
      
      return "UNKNOWN";
   }
   
private:
   //+------------------------------------------------------------------+
   //| Test API connection                                                |
   //+------------------------------------------------------------------+
   bool TestConnection()
   {
      string url = m_base_url + "models";
      
      char post_data[];
      char result[];
      string headers = "Authorization: Bearer " + m_api_key + "\r\n" +
                      "Content-Type: application/json\r\n";
      
      int timeout = 10000;
      bool res = WebRequest("GET", url, headers, timeout, post_data, result, headers);
      
      return res && ArraySize(result) > 0;
   }
   
   //+------------------------------------------------------------------+
   //| Analyze with DeepSeek API                                          |
   //+------------------------------------------------------------------+
   AIAnalysisResult AnalyzeWithDeepSeek(MarketDataForAI &market_data)
   {
      AIAnalysisResult result;
      
      // Build prompt for DeepSeek
      string prompt = BuildAnalysisPrompt(market_data);
      
      // Make API call
      string response = CallDeepSeekAPI(prompt);
      
      // Parse response
      result = ParseAIResponse(response);
      result.analysis_time = TimeCurrent();
      
      return result;
   }
   
   //+------------------------------------------------------------------+
   //| Build analysis prompt for AI                                        |
   //+------------------------------------------------------------------+
   string BuildAnalysisPrompt(MarketDataForAI &data)
   {
      string prompt = "Analyze the following forex market data and provide trading recommendations:\n\n";
      
      prompt += "SYMBOL: " + data.symbol + "\n";
      prompt += "CURRENT PRICE: " + DoubleToString(data.current_price, 5) + "\n";
      prompt += "BID/ASK: " + DoubleToString(data.bid, 5) + "/" + DoubleToString(data.ask, 5) + "\n";
      prompt += "SPREAD: " + DoubleToString(data.spread, 1) + " points\n\n";
      
      prompt += "TECHNICAL INDICATORS:\n";
      prompt += "- ATR(14): " + DoubleToString(data.atr_14, 5) + "\n";
      prompt += "- RSI(14): " + DoubleToString(data.rsi_14, 2) + "\n";
      prompt += "- MACD Signal: " + DoubleToString(data.macd_signal, 5) + "\n";
      prompt += "- EMA(20): " + DoubleToString(data.ema_20, 5) + "\n";
      prompt += "- EMA(50): " + DoubleToString(data.ema_50, 5) + "\n";
      prompt += "- EMA(200): " + DoubleToString(data.ema_200, 5) + "\n\n";
      
      prompt += "PRICE ACTION:\n";
      prompt += "- Daily High: " + DoubleToString(data.daily_high, 5) + "\n";
      prompt += "- Daily Low: " + DoubleToString(data.daily_low, 5) + "\n";
      prompt += "- Daily Open: " + DoubleToString(data.daily_open, 5) + "\n";
      prompt += "- Volume: " + DoubleToString(data.volume, 0) + "\n\n";
      
      prompt += "NEWS CONTEXT:\n" + data.news_summary + "\n";
      prompt += "NEWS SENTIMENT: " + DoubleToString(data.news_sentiment, 2) + "\n\n";
      
      prompt += "Provide analysis in JSON format:\n";
      prompt += "{\n";
      prompt += "  \"signal\": \"BUY/SELL/HOLD\",\n";
      prompt += "  \"risk_score\": 0.0-1.0,\n";
      prompt += "  \"confidence\": 0.0-1.0,\n";
      prompt += "  \"reasoning\": \"brief explanation\",\n";
      prompt += "  \"suggested_sl\": price,\n";
      prompt += "  \"suggested_tp\": price\n";
      prompt += "}\n";
      
      return prompt;
   }
   
   //+------------------------------------------------------------------+
   //| Call DeepSeek API                                                  |
   //+------------------------------------------------------------------+
   string CallDeepSeekAPI(string prompt)
   {
      string url = m_base_url + "chat/completions";
      
      // Build JSON payload
      string payload = "{";
      payload += "\"model\":\"" + m_model + "\",";
      payload += "\"messages\":[";
      payload += "{\"role\":\"system\",\"content\":\"You are a professional forex analyst. Provide concise trading analysis in JSON format.\"},";
      payload += "{\"role\":\"user\",\"content\":\"" + prompt + "\"}";
      payload += "],";
      payload += "\"max_tokens\":" + IntegerToString(m_max_tokens) + ",";
      payload += "\"temperature\":" + DoubleToString(m_temperature, 2);
      payload += "}";
      
      char post_data[];
      StringToCharArray(payload, post_data, 0, StringLen(payload));
      
      char result[];
      string headers = "Authorization: Bearer " + m_api_key + "\r\n" +
                      "Content-Type: application/json\r\n";
      
      int timeout = 30000;
      bool res = WebRequest("POST", url, headers, timeout, post_data, result, headers);
      
      if(res && ArraySize(result) > 0)
      {
         return CharArrayToString(result);
      }
      
      return "";
   }
   
   //+------------------------------------------------------------------+
   //| Parse AI response                                                  |
   //+------------------------------------------------------------------+
   AIAnalysisResult ParseAIResponse(string response)
   {
      AIAnalysisResult result;
      
      // Default values
      result.risk_score = 0.5;
      result.confidence = 0.5;
      result.signal = SIGNAL_NONE;
      result.reasoning = "Unable to parse AI response";
      result.suggested_sl = 0;
      result.suggested_tp = 0;
      result.position_size = 0.01;
      
      if(response == "") return result;
      
      // Simple JSON parsing
      result.signal = ExtractSignal(response);
      result.risk_score = ExtractDouble(response, "\"risk_score\":");
      result.confidence = ExtractDouble(response, "\"confidence\":");
      result.reasoning = ExtractString(response, "\"reasoning\":\"");
      
      return result;
   }
   
   //+------------------------------------------------------------------+
   //| Built-in technical analysis (fallback)                             |
   //+------------------------------------------------------------------+
   AIAnalysisResult AnalyzeWithBuiltInLogic(MarketDataForAI &data)
   {
      AIAnalysisResult result;
      
      // Calculate signals based on technical indicators
      double buy_score = 0;
      double sell_score = 0;
      
      // RSI Analysis
      if(data.rsi_14 < 30) buy_score += 0.3;
      else if(data.rsi_14 > 70) sell_score += 0.3;
      
      // MACD Analysis
      if(data.macd_signal > 0) buy_score += 0.2;
      else sell_score += 0.2;
      
      // EMA Alignment
      if(data.ema_20 > data.ema_50 && data.ema_50 > data.ema_200)
      {
         buy_score += 0.3;
      }
      else if(data.ema_20 < data.ema_50 && data.ema_50 < data.ema_200)
      {
         sell_score += 0.3;
      }
      
      // Price vs EMAs
      if(data.current_price > data.ema_200) buy_score += 0.1;
      else sell_score += 0.1;
      
      // News sentiment
      buy_score += data.news_sentiment * 0.1;
      sell_score -= data.news_sentiment * 0.1;
      
      // Determine signal
      double threshold = 0.4;
      if(buy_score > sell_score && buy_score > threshold)
      {
         result.signal = SIGNAL_BUY;
         result.risk_score = 1.0 - buy_score;
      }
      else if(sell_score > buy_score && sell_score > threshold)
      {
         result.signal = SIGNAL_SELL;
         result.risk_score = 1.0 - sell_score;
      }
      else
      {
         result.signal = SIGNAL_HOLD;
         result.risk_score = 0.5;
      }
      
      result.confidence = MathAbs(buy_score - sell_score);
      result.reasoning = "Built-in analysis: Buy=" + DoubleToString(buy_score, 2) + 
                        " Sell=" + DoubleToString(sell_score, 2);
      
      // Calculate suggested levels
      double atr = data.atr_14;
      if(result.signal == SIGNAL_BUY)
      {
         result.suggested_sl = data.current_price - (atr * 1.5);
         result.suggested_tp = data.current_price + (atr * 3.0);
      }
      else if(result.signal == SIGNAL_SELL)
      {
         result.suggested_sl = data.current_price + (atr * 1.5);
         result.suggested_tp = data.current_price - (atr * 3.0);
      }
      
      result.position_size = 0.01;
      
      return result;
   }
   
   //+------------------------------------------------------------------+
   //| Check if analysis is cached                                        |
   //+------------------------------------------------------------------+
   bool IsAnalysisCached()
   {
      if(m_last_analysis_time == 0) return false;
      
      int elapsed = (int)(TimeCurrent() - m_last_analysis_time);
      return elapsed < m_analysis_cache_seconds;
   }
   
   //+------------------------------------------------------------------+
   //| Extract signal from JSON response                                  |
   //+------------------------------------------------------------------+
   ENUM_SIGNAL_TYPE ExtractSignal(string json)
   {
      if(StringFind(json, "\"signal\":\"BUY\"") >= 0) return SIGNAL_BUY;
      if(StringFind(json, "\"signal\":\"SELL\"") >= 0) return SIGNAL_SELL;
      if(StringFind(json, "\"signal\":\"HOLD\"") >= 0) return SIGNAL_HOLD;
      return SIGNAL_NONE;
   }
   
   //+------------------------------------------------------------------+
   //| Extract double value from JSON                                     |
   //+------------------------------------------------------------------+
   double ExtractDouble(string json, string key)
   {
      int pos = StringFind(json, key);
      if(pos < 0) return 0.0;
      
      pos += StringLen(key);
      while(pos < StringLen(json) && json[pos] == ' ') pos++;
      
      string value = "";
      while(pos < StringLen(json) && json[pos] != ',' && json[pos] != '}')
      {
         value += json[pos];
         pos++;
      }
      
      return StringToDouble(value);
   }
   
   //+------------------------------------------------------------------+
   //| Extract string value from JSON                                     |
   //+------------------------------------------------------------------+
   string ExtractString(string json, string key)
   {
      int pos = StringFind(json, key);
      if(pos < 0) return "";
      
      pos += StringLen(key);
      string value = "";
      
      while(pos < StringLen(json) && json[pos] != '"')
      {
         value += json[pos];
         pos++;
      }
      
      return value;
   }
};
