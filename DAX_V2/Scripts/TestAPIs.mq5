//+------------------------------------------------------------------+
//|                                                    TestAPIs.mq5  |
//|                          API Connection Test Script               |
//|                         Version 2.0 - DAX V2 System               |
//+------------------------------------------------------------------+
#property copyright "DAX V2 AI Trading System"
#property link      ""
#property version   "2.00"
#property strict

#include "..\Include\NewsAPI.mqh"
#include "..\Include\DeepSeekAI.mqh"

//+------------------------------------------------------------------+
//| Script program start function                                      |
//+------------------------------------------------------------------+
void OnStart()
{
   Print("========================================");
   Print(" DAX V2 API CONNECTION TEST");
   Print("========================================");
   
   // Test NewsAPI
   TestNewsAPI();
   
   // Test DeepSeek AI
   TestDeepSeekAI();
   
   Print("========================================");
   Print(" TEST COMPLETE");
   Print("========================================");
}

//+------------------------------------------------------------------+
//| Test NewsAPI connection                                            |
//+------------------------------------------------------------------+
void TestNewsAPI()
{
   Print("\n--- Testing NewsAPI ---");
   
   CNewsAPI news_api;
   
   // Test with dummy key first
   if(news_api.Initialize("test_key"))
   {
      Print("NewsAPI: Module loaded successfully");
      
      // Test offline mode
      NewsEvent events[];
      if(news_api.GenerateOfflineNews(events))
      {
         Print("NewsAPI: Offline mode working");
         Print("  Generated ", ArraySize(events), " test events");
         
         for(int i = 0; i < ArraySize(events); i++)
         {
            Print("  Event ", i+1, ": ", events[i].event_title);
         }
      }
      
      // Test sentiment analysis
      double sentiment = news_api.AnalyzeNewsSentiment("EURUSD", 4);
      Print("NewsAPI: Sentiment test - Score: ", DoubleToString(sentiment, 2));
      
      // Test high impact check
      bool high_impact = news_api.IsHighImpactNewsImminent("EURUSD", 30);
      Print("NewsAPI: High impact imminent: ", high_impact ? "YES" : "NO");
      
      // Test news summary
      string summary = news_api.GetNewsSummary("EURUSD", 24);
      Print("NewsAPI: Summary generated (", StringLen(summary), " chars)");
      
      Print("NewsAPI: PASSED");
   }
   else
   {
      Print("NewsAPI: FAILED to initialize");
   }
}

//+------------------------------------------------------------------+
//| Test DeepSeek AI connection                                        |
//+------------------------------------------------------------------+
void TestDeepSeekAI()
{
   Print("\n--- Testing DeepSeek AI ---");
   
   CDeepSeekAI ai;
   
   // Test initialization
   if(ai.Initialize(""))
   {
      Print("DeepSeekAI: Module loaded successfully");
      
      // Test built-in analysis
      MarketDataForAI market_data;
      market_data.symbol = "EURUSD";
      market_data.current_price = 1.08500;
      market_data.bid = 1.08495;
      market_data.ask = 1.08505;
      market_data.spread = 10;
      market_data.atr_14 = 0.00850;
      market_data.rsi_14 = 55.0;
      market_data.macd_signal = 0.00020;
      market_data.ema_20 = 1.08400;
      market_data.ema_50 = 1.08200;
      market_data.ema_200 = 1.07800;
      market_data.volume = 1500;
      market_data.daily_high = 1.08700;
      market_data.daily_low = 1.08100;
      market_data.daily_open = 1.08200;
      market_data.news_summary = "Test news summary";
      market_data.news_sentiment = 0.15;
      
      AIAnalysisResult result = ai.AnalyzeMarket(market_data);
      
      Print("DeepSeekAI: Built-in analysis test");
      Print("  Signal: ", GetSignalName(result.signal));
      Print("  Risk Score: ", DoubleToString(result.risk_score * 100, 1), "%");
      Print("  Confidence: ", DoubleToString(result.confidence * 100, 1), "%");
      Print("  Reasoning: ", result.reasoning);
      
      // Test position sizing
      double lot_size = ai.CalculateRiskAdjustedSize(
         1000.0,  // balance
         0.00150, // stop distance
         1.08500, // current price
         result.risk_score
      );
      Print("DeepSeekAI: Position sizing test - Lot: ", DoubleToString(lot_size, 2));
      
      // Test dynamic stop loss
      double sl = ai.CalculateDynamicStopLoss(
         1.08500, true, market_data.atr_14, result.risk_score
      );
      Print("DeepSeekAI: Dynamic SL test - SL: ", DoubleToString(sl, 5));
      
      // Test take profit
      double tp = ai.CalculateTakeProfit(1.08500, sl, true);
      Print("DeepSeekAI: Dynamic TP test - TP: ", DoubleToString(tp, 5));
      
      // Test market regime
      string regime = ai.GetMarketRegime(market_data);
      Print("DeepSeekAI: Market regime: ", regime);
      
      Print("DeepSeekAI: PASSED");
   }
   else
   {
      Print("DeepSeekAI: FAILED to initialize");
   }
}

//+------------------------------------------------------------------+
//| Get signal name                                                    |
//+------------------------------------------------------------------+
string GetSignalName(ENUM_SIGNAL_TYPE signal)
{
   switch(signal)
   {
      case SIGNAL_BUY: return "BUY";
      case SIGNAL_SELL: return "SELL";
      case SIGNAL_HOLD: return "HOLD";
      default: return "NONE";
   }
}
