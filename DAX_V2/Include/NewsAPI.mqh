//+------------------------------------------------------------------+
//|                                                    NewsAPI.mqh   |
//|                          Free News API Integration Module         |
//|                         Version 2.0 - DAX V2 System               |
//+------------------------------------------------------------------+
#property copyright "DAX V2 AI Trading System"
#property link      ""
#property version   "2.00"

#include <Trade\Trade.mqh>
#include <Arrays\ArrayString.mqh>

//+------------------------------------------------------------------+
//| News Impact Enum                                                  |
//+------------------------------------------------------------------+
enum ENUM_NEWS_IMPACT
{
   NEWS_IMPACT_LOW = 0,      // Low Impact
   NEWS_IMPACT_MEDIUM = 1,   // Medium Impact
   NEWS_IMPACT_HIGH = 2,     // High Impact
   NEWS_IMPACT_CRITICAL = 3  // Critical Impact
};

//+------------------------------------------------------------------+
//| News Event Structure                                              |
//+------------------------------------------------------------------+
struct NewsEvent
{
   datetime   event_time;
   string     event_title;
   string     event_currency;
   ENUM_NEWS_IMPACT impact;
   string     forecast;
   string     previous;
   string     actual;
   bool       is_processed;
};

//+------------------------------------------------------------------+
//| Free News API Handler                                             |
//+------------------------------------------------------------------+
class CNewsAPI
{
private:
   string         m_api_key;
   string         m_base_url;
   CArrayString   m_news_cache;
   datetime       m_last_fetch;
   int            m_fetch_interval;
   bool           m_is_connected;
   
   // News impact keywords
   string         m_high_impact_words[];
   string         m_medium_impact_words[];
   
public:
   //+------------------------------------------------------------------+
   //| Constructor                                                        |
   //+------------------------------------------------------------------+
   CNewsAPI()
   {
      m_api_key = "";
      m_base_url = "https://api.newsapi.org/v2/";
      m_last_fetch = 0;
      m_fetch_interval = 300; // 5 minutes
      m_is_connected = false;
      
      InitializeImpactKeywords();
   }
   
   //+------------------------------------------------------------------+
   //| Initialize with API key                                            |
   //+------------------------------------------------------------------+
   bool Initialize(string api_key)
   {
      m_api_key = api_key;
      
      // Test connection with a simple request
      if(TestConnection())
      {
         m_is_connected = true;
         Print("NewsAPI: Connected successfully");
         return true;
      }
      
      Print("NewsAPI: Connection failed - using offline mode");
      m_is_connected = false;
      return false;
   }
   
   //+------------------------------------------------------------------+
   //| Test API connection                                                |
   //+------------------------------------------------------------------+
   bool TestConnection()
   {
      string url = m_base_url + "top-headlines?country=us&apiKey=" + m_api_key + "&pageSize=1";
      
      char post_data[];
      char result[];
      string headers = "Content-Type: application/json\r\n";
      
      int timeout = 5000;
      bool res = WebRequest("GET", url, headers, timeout, post_data, result, headers);
      
      if(res && ArraySize(result) > 0)
      {
         string response = CharArrayToString(result);
         return StringFind(response, "\"status\":\"ok\"") >= 0;
      }
      
      return false;
   }
   
   //+------------------------------------------------------------------+
   //| Fetch forex news for specific currency pair                        |
   //+------------------------------------------------------------------+
   bool FetchForexNews(string symbol, NewsEvent &events[])
   {
      if(!m_is_connected)
      {
         return GenerateOfflineNews(events);
      }
      
      // Extract currencies from symbol (e.g., "EURUSD" -> "EUR", "USD")
      string base_currency = StringSubstr(symbol, 0, 3);
      string quote_currency = StringSubstr(symbol, 3, 3);
      
      string url = m_base_url + "everything?language=en&sortBy=publishedAt&apiKey=" + m_api_key;
      url += "&q=" + base_currency + "+OR+" + quote_currency + "+forex+market";
      url += "&pageSize=10";
      
      char post_data[];
      char result[];
      string headers = "Content-Type: application/json\r\n";
      
      int timeout = 10000;
      bool res = WebRequest("GET", url, headers, timeout, post_data, result, headers);
      
      if(res && ArraySize(result) > 0)
      {
         string response = CharArrayToString(result);
         return ParseNewsResponse(response, events);
      }
      
      return false;
   }
   
   //+------------------------------------------------------------------+
   //| Fetch economic calendar events                                     |
   //+------------------------------------------------------------------+
   bool FetchEconomicCalendar(string symbol, NewsEvent &events[])
   {
      // For free alternatives, we can use alternative APIs or cached data
      string base_currency = StringSubstr(symbol, 0, 3);
      string quote_currency = StringSubstr(symbol, 3, 3);
      
      // Try multiple free sources
      string url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json";
      
      char post_data[];
      char result[];
      string headers = "Content-Type: application/json\r\n";
      
      int timeout = 10000;
      bool res = WebRequest("GET", url, headers, timeout, post_data, result, headers);
      
      if(res && ArraySize(result) > 0)
      {
         string response = CharArrayToString(result);
         return ParseEconomicCalendar(response, events, base_currency, quote_currency);
      }
      
      return GenerateDefaultCalendar(events, base_currency, quote_currency);
   }
   
   //+------------------------------------------------------------------+
   //| Analyze news sentiment for symbol                                  |
   //+------------------------------------------------------------------+
   double AnalyzeNewsSentiment(string symbol, int hours_ahead = 4)
   {
      NewsEvent events[];
      
      if(!FetchForexNews(symbol, events))
      {
         return 0.0; // Neutral if no data
      }
      
      double sentiment_score = 0.0;
      int event_count = 0;
      
      datetime current_time = TimeCurrent();
      datetime future_time = current_time + (hours_ahead * 3600);
      
      for(int i = 0; i < ArraySize(events); i++)
      {
         if(events[i].event_time >= current_time && events[i].event_time <= future_time)
         {
            // Analyze sentiment based on impact and content
            double impact_factor = 1.0;
            
            switch(events[i].impact)
            {
               case NEWS_IMPACT_CRITICAL: impact_factor = 2.0; break;
               case NEWS_IMPACT_HIGH: impact_factor = 1.5; break;
               case NEWS_IMPACT_MEDIUM: impact_factor = 1.0; break;
               case NEWS_IMPACT_LOW: impact_factor = 0.5; break;
            }
            
            // Simple keyword-based sentiment (enhanced with DeepSeek in main EA)
            string title_lower = StringToLower(events[i].event_title);
            
            if(StringFind(title_lower, "bull") >= 0 || StringFind(title_lower, "rally") >= 0 ||
               StringFind(title_lower, "surge") >= 0 || StringFind(title_lower, "gain") >= 0)
            {
               sentiment_score += impact_factor * 0.3;
            }
            else if(StringFind(title_lower, "bear") >= 0 || StringFind(title_lower, "fall") >= 0 ||
                    StringFind(title_lower, "drop") >= 0 || StringFind(title_lower, "decline") >= 0)
            {
               sentiment_score -= impact_factor * 0.3;
            }
            
            event_count++;
         }
      }
      
      // Normalize sentiment to -1.0 to 1.0 range
      if(event_count > 0)
      {
         sentiment_score = MathMax(-1.0, MathMin(1.0, sentiment_score / event_count));
      }
      
      return sentiment_score;
   }
   
   //+------------------------------------------------------------------+
   //| Check if high impact news is imminent                              |
   //+------------------------------------------------------------------+
   bool IsHighImpactNewsImminent(string symbol, int minutes_threshold = 30)
   {
      NewsEvent events[];
      
      if(!FetchEconomicCalendar(symbol, events))
      {
         return false;
      }
      
      datetime current_time = TimeCurrent();
      datetime threshold_time = current_time + (minutes_threshold * 60);
      
      for(int i = 0; i < ArraySize(events); i++)
      {
         if(events[i].event_time >= current_time && events[i].event_time <= threshold_time)
         {
            if(events[i].impact >= NEWS_IMPACT_HIGH)
            {
               return true;
            }
         }
      }
      
      return false;
   }
   
   //+------------------------------------------------------------------+
   //| Get news summary for AI analysis                                   |
   //+------------------------------------------------------------------+
   string GetNewsSummary(string symbol, int hours_lookback = 24)
   {
      NewsEvent events[];
      string summary = "";
      
      if(FetchForexNews(symbol, events))
      {
         datetime current_time = TimeCurrent();
         datetime lookback_time = current_time - (hours_lookback * 3600);
         
         summary += "NEWS SUMMARY FOR " + symbol + ":\n";
         
         int high_count = 0, medium_count = 0, low_count = 0;
         
         for(int i = 0; i < ArraySize(events); i++)
         {
            if(events[i].event_time >= lookback_time)
            {
               switch(events[i].impact)
               {
                  case NEWS_IMPACT_CRITICAL:
                  case NEWS_IMPACT_HIGH: high_count++; break;
                  case NEWS_IMPACT_MEDIUM: medium_count++; break;
                  case NEWS_IMPACT_LOW: low_count++; break;
               }
               
               summary += "- " + events[i].event_title + " [" + 
                         ImpactToString(events[i].impact) + "]\n";
            }
         }
         
         summary += "\nIMPACT COUNT: High=" + IntegerToString(high_count) + 
                   " Medium=" + IntegerToString(medium_count) + 
                   " Low=" + IntegerToString(low_count) + "\n";
      }
      
      return summary;
   }
   
private:
   //+------------------------------------------------------------------+
   //| Initialize impact keywords                                         |
   //+------------------------------------------------------------------+
   void InitializeImpactKeywords()
   {
      ArrayResize(m_high_impact_words, 10);
      m_high_impact_words[0] = "fed";
      m_high_impact_words[1] = "ecb";
      m_high_impact_words[2] = "interest rate";
      m_high_impact_words[3] = "inflation";
      m_high_impact_words[4] = "gdp";
      m_high_impact_words[5] = "employment";
      m_high_impact_words[6] = "non-farm";
      m_high_impact_words[7] = "retail sales";
      m_high_impact_words[8] = "pmi";
      m_high_impact_words[9] = "central bank";
      
      ArrayResize(m_medium_impact_words, 8);
      m_medium_impact_words[0] = "trade balance";
      m_medium_impact_words[1] = "consumer confidence";
      m_medium_impact_words[2] = "housing";
      m_medium_impact_words[3] = "industrial production";
      m_medium_impact_words[4] = "capacity utilization";
      m_medium_impact_words[5] = "business climate";
      m_medium_impact_words[6] = "sentiment";
      m_medium_impact_words[7] = "manufacturing";
   }
   
   //+------------------------------------------------------------------+
   //| Parse news API response                                            |
   //+------------------------------------------------------------------+
   bool ParseNewsResponse(string response, NewsEvent &events[])
   {
      // Simple JSON parsing for news articles
      int article_count = 0;
      int pos = 0;
      
      // Count articles
      while((pos = StringFind(response, "\"title\":", pos)) >= 0)
      {
         article_count++;
         pos += 8;
      }
      
      if(article_count == 0) return false;
      
      ArrayResize(events, MathMin(article_count, 10));
      
      pos = 0;
      for(int i = 0; i < ArraySize(events) && i < 10; i++)
      {
         // Extract title
         int title_start = StringFind(response, "\"title\":\"", pos);
         if(title_start < 0) break;
         title_start += 9;
         
         int title_end = StringFind(response, "\"", title_start);
         if(title_end < 0) break;
         
         events[i].event_title = StringSubstr(response, title_start, title_end - title_start);
         
         // Extract published time
         int time_start = StringFind(response, "\"publishedAt\":\"", title_end);
         if(time_start >= 0)
         {
            time_start += 15;
            int time_end = StringFind(response, "\"", time_start);
            if(time_end > time_start)
            {
               string time_str = StringSubstr(response, time_start, time_end - time_start);
               events[i].event_time = ParseDateTime(time_str);
            }
         }
         else
         {
            events[i].event_time = TimeCurrent();
         }
         
         // Determine impact based on keywords
         events[i].impact = DetermineImpact(events[i].event_title);
         events[i].event_currency = "MULTI";
         events[i].is_processed = false;
         
         pos = title_end + 1;
      }
      
      return ArraySize(events) > 0;
   }
   
   //+------------------------------------------------------------------+
   //| Parse economic calendar response                                   |
   //+------------------------------------------------------------------+
   bool ParseEconomicCalendar(string response, NewsEvent &events[], 
                              string base_currency, string quote_currency)
   {
      // Parse faireconomy format
      int event_count = 0;
      int pos = 0;
      
      while((pos = StringFind(response, "\"title\":", pos)) >= 0)
      {
         event_count++;
         pos += 8;
      }
      
      if(event_count == 0) return false;
      
      ArrayResize(events, MathMin(event_count, 20));
      
      pos = 0;
      int event_index = 0;
      
      for(int i = 0; i < ArraySize(events) && event_index < 20; i++)
      {
         // Extract title
         int title_start = StringFind(response, "\"title\":\"", pos);
         if(title_start < 0) break;
         title_start += 9;
         
         int title_end = StringFind(response, "\"", title_start);
         if(title_end < 0) break;
         
         string title = StringSubstr(response, title_start, title_end - title_start);
         
         // Extract currency
         int curr_start = StringFind(response, "\"currency\":\"", title_end);
         string currency = "";
         if(curr_start >= 0)
         {
            curr_start += 12;
            int curr_end = StringFind(response, "\"", curr_start);
            if(curr_end > curr_start)
            {
               currency = StringSubstr(response, curr_start, curr_end - curr_start);
            }
         }
         
         // Filter for relevant currencies
         if(currency == base_currency || currency == quote_currency || currency == "USD" || currency == "EUR")
         {
            events[event_index].event_title = title;
            events[event_index].event_currency = currency;
            events[event_index].impact = DetermineImpact(title);
            events[event_index].is_processed = false;
            
            // Extract time
            int time_start = StringFind(response, "\"date\":\"", title_end);
            if(time_start >= 0)
            {
               time_start += 8;
               int time_end = StringFind(response, "\"", time_start);
               if(time_end > time_start)
               {
                  string time_str = StringSubstr(response, time_start, time_end - time_start);
                  events[event_index].event_time = ParseDateTime(time_str);
               }
            }
            
            event_index++;
         }
         
         pos = title_end + 1;
      }
      
      ArrayResize(events, event_index);
      return event_index > 0;
   }
   
   //+------------------------------------------------------------------+
   //| Generate offline news for testing                                  |
   //+------------------------------------------------------------------+
   bool GenerateOfflineNews(NewsEvent &events[])
   {
      ArrayResize(events, 5);
      
      datetime current_time = TimeCurrent();
      
      events[0].event_time = current_time - 3600;
      events[0].event_title = "ECB President Speaks on Monetary Policy";
      events[0].event_currency = "EUR";
      events[0].impact = NEWS_IMPACT_HIGH;
      events[0].is_processed = false;
      
      events[1].event_time = current_time + 1800;
      events[1].event_title = "US Initial Jobless Claims";
      events[1].event_currency = "USD";
      events[1].impact = NEWS_IMPACT_MEDIUM;
      events[1].is_processed = false;
      
      events[2].event_time = current_time + 3600;
      events[2].event_title = "German Consumer Price Index";
      events[2].event_currency = "EUR";
      events[2].impact = NEWS_IMPACT_HIGH;
      events[2].is_processed = false;
      
      events[3].event_time = current_time + 7200;
      events[3].event_title = "FOMC Meeting Minutes";
      events[3].event_currency = "USD";
      events[3].impact = NEWS_IMPACT_CRITICAL;
      events[3].is_processed = false;
      
      events[4].event_time = current_time + 14400;
      events[4].event_title = "Eurozone Industrial Production";
      events[4].event_currency = "EUR";
      events[4].impact = NEWS_IMPACT_LOW;
      events[4].is_processed = false;
      
      return true;
   }
   
   //+------------------------------------------------------------------+
   //| Generate default calendar                                          |
   //+------------------------------------------------------------------+
   bool GenerateDefaultCalendar(NewsEvent &events[], string base, string quote)
   {
      ArrayResize(events, 3);
      datetime current_time = TimeCurrent();
      
      events[0].event_time = current_time + 3600;
      events[0].event_title = base + " Central Bank Rate Decision";
      events[0].event_currency = base;
      events[0].impact = NEWS_IMPACT_CRITICAL;
      events[0].is_processed = false;
      
      events[1].event_time = current_time + 7200;
      events[1].event_title = quote + " GDP Growth Rate";
      events[1].event_currency = quote;
      events[1].impact = NEWS_IMPACT_HIGH;
      events[1].is_processed = false;
      
      events[2].event_time = current_time + 10800;
      events[2].event_title = "Trade Balance " + base + "/" + quote;
      events[2].event_currency = "MULTI";
      events[2].impact = NEWS_IMPACT_MEDIUM;
      events[2].is_processed = false;
      
      return true;
   }
   
   //+------------------------------------------------------------------+
   //| Determine news impact level                                        |
   //+------------------------------------------------------------------+
   ENUM_NEWS_IMPACT DetermineImpact(string title)
   {
      string title_lower = StringToLower(title);
      
      // Check high impact first
      for(int i = 0; i < ArraySize(m_high_impact_words); i++)
      {
         if(StringFind(title_lower, m_high_impact_words[i]) >= 0)
         {
            return NEWS_IMPACT_HIGH;
         }
      }
      
      // Check medium impact
      for(int i = 0; i < ArraySize(m_medium_impact_words); i++)
      {
         if(StringFind(title_lower, m_medium_impact_words[i]) >= 0)
         {
            return NEWS_IMPACT_MEDIUM;
         }
      }
      
      return NEWS_IMPACT_LOW;
   }
   
   //+------------------------------------------------------------------+
   //| Parse datetime string                                              |
   //+------------------------------------------------------------------+
   datetime ParseDateTime(string dt_str)
   {
      // Simple parsing for ISO format
      MqlDateTime dt;
      TimeCurrent(dt);
      
      int year = StringToInteger(StringSubstr(dt_str, 0, 4));
      int month = StringToInteger(StringSubstr(dt_str, 5, 2));
      int day = StringToInteger(StringSubstr(dt_str, 8, 2));
      int hour = StringToInteger(StringSubstr(dt_str, 11, 2));
      int min = StringToInteger(StringSubstr(dt_str, 14, 2));
      int sec = StringToInteger(StringSubstr(dt_str, 17, 2));
      
      dt.year = year;
      dt.mon = month;
      dt.day = day;
      dt.hour = hour;
      dt.min = min;
      dt.sec = sec;
      
      return StructToTime(dt);
   }
   
   //+------------------------------------------------------------------+
   //| Convert impact to string                                           |
   //+------------------------------------------------------------------+
   string ImpactToString(ENUM_NEWS_IMPACT impact)
   {
      switch(impact)
      {
         case NEWS_IMPACT_CRITICAL: return "CRITICAL";
         case NEWS_IMPACT_HIGH: return "HIGH";
         case NEWS_IMPACT_MEDIUM: return "MEDIUM";
         case NEWS_IMPACT_LOW: return "LOW";
         default: return "UNKNOWN";
      }
   }
};
