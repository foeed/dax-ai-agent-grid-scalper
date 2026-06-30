# News API Service for Market Intelligence

import httpx
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import re

from app.core.config import settings
from app.models.schemas import NewsItem, NewsResponse, NewsImpact

logger = logging.getLogger(__name__)

class NewsService:
    """Handles news fetching and sentiment analysis"""
    
    def __init__(self):
        self.api_key = settings.NEWS_API_KEY
        self.base_url = "https://newsapi.org/v2"
        
        # Impact keywords
        self.high_impact_keywords = [
            "fed", "ecb", "interest rate", "inflation", "gdp",
            "employment", "non-farm", "retail sales", "pmi",
            "central bank", "monetary policy", "rate decision"
        ]
        
        self.medium_impact_keywords = [
            "trade balance", "consumer confidence", "housing",
            "industrial production", "capacity utilization",
            "business climate", "sentiment", "manufacturing"
        ]
        
        # Positive/negative sentiment words
        self.positive_words = [
            "rise", "gain", "surge", "rally", "bull", "growth",
            "increase", "improve", "strong", "positive", "up"
        ]
        
        self.negative_words = [
            "fall", "drop", "decline", "bear", "loss", "weak",
            "decrease", "negative", "down", "recession", "crisis"
        ]
        
        # Cache
        self._cache: Dict[str, NewsResponse] = {}
        self._cache_ttl = 300  # 5 minutes
    
    async def get_forex_news(
        self, 
        symbol: str, 
        hours_lookback: int = 24
    ) -> NewsResponse:
        """Get forex news for a currency pair"""
        
        # Check cache
        cache_key = f"{symbol}_{hours_lookback}"
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if (datetime.utcnow() - cached.timestamp).seconds < self._cache_ttl:
                return cached
        
        # Extract currencies
        base_currency = symbol[:3]
        quote_currency = symbol[3:]
        
        # Fetch news
        if self.api_key and self.api_key != "YOUR_NEWS_API_KEY":
            try:
                articles = await self._fetch_news_api(base_currency, quote_currency)
            except Exception as e:
                logger.error(f"NewsAPI error: {e}")
                articles = self._generate_sample_news(base_currency, quote_currency)
        else:
            logger.info("Using sample news (no API key)")
            articles = self._generate_sample_news(base_currency, quote_currency)
        
        # Process articles
        news_items = []
        high_impact_count = 0
        sentiment_sum = 0.0
        
        for article in articles:
            # Determine impact
            impact = self._determine_impact(article.get("title", ""))
            
            # Analyze sentiment
            sentiment = self._analyze_sentiment(article.get("title", ""))
            
            # Check relevance
            is_relevant = self._is_relevant(article.get("title", ""), base_currency, quote_currency)
            
            news_item = NewsItem(
                title=article.get("title", "Unknown"),
                currency=self._extract_currency(article, base_currency, quote_currency),
                impact=impact,
                sentiment=sentiment,
                time=self._parse_time(article.get("publishedAt", "")),
                is_relevant=is_relevant
            )
            
            if is_relevant:
                news_items.append(news_item)
                if impact in [NewsImpact.HIGH, NewsImpact.CRITICAL]:
                    high_impact_count += 1
                sentiment_sum += sentiment
        
        # Calculate overall sentiment
        avg_sentiment = sentiment_sum / len(news_items) if news_items else 0.0
        
        # Determine news caution
        news_caution = high_impact_count > 0
        
        response = NewsResponse(
            symbol=symbol,
            news_count=len(news_items),
            high_impact_count=high_impact_count,
            sentiment_score=avg_sentiment,
            news_caution=news_caution,
            upcoming_events=news_items[:10],  # Top 10
            timestamp=datetime.utcnow()
        )
        
        # Cache
        self._cache[cache_key] = response
        
        return response
    
    async def _fetch_news_api(
        self, 
        base_currency: str, 
        quote_currency: str
    ) -> List[Dict[str, Any]]:
        """Fetch from NewsAPI"""
        
        query = f"{base_currency} OR {quote_currency} OR forex"
        
        params = {
            "q": query,
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": 20,
            "apiKey": self.api_key
        }
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{self.base_url}/everything",
                params=params
            )
            response.raise_for_status()
            
            data = response.json()
            return data.get("articles", [])
    
    def _generate_sample_news(
        self, 
        base_currency: str, 
        quote_currency: str
    ) -> List[Dict[str, Any]]:
        """Generate sample news for testing"""
        
        now = datetime.utcnow()
        
        sample_news = [
            {
                "title": f"{base_currency} Central Bank Maintains Interest Rates",
                "publishedAt": (now - timedelta(hours=2)).isoformat() + "Z"
            },
            {
                "title": f"{quote_currency} Employment Data Shows Strong Growth",
                "publishedAt": (now - timedelta(hours=4)).isoformat() + "Z"
            },
            {
                "title": f"Global Markets Rally on Positive Economic Outlook",
                "publishedAt": (now - timedelta(hours=6)).isoformat() + "Z"
            },
            {
                "title": f"{base_currency}/{quote_currency} Pair Volatility Increases",
                "publishedAt": (now - timedelta(hours=8)).isoformat() + "Z"
            },
            {
                "title": f"Forex Market Analysis: {base_currency} Under Pressure",
                "publishedAt": (now - timedelta(hours=10)).isoformat() + "Z"
            }
        ]
        
        return sample_news
    
    def _determine_impact(self, title: str) -> NewsImpact:
        """Determine news impact level"""
        
        title_lower = title.lower()
        
        # Check high impact
        for keyword in self.high_impact_keywords:
            if keyword in title_lower:
                return NewsImpact.HIGH
        
        # Check medium impact
        for keyword in self.medium_impact_keywords:
            if keyword in title_lower:
                return NewsImpact.MEDIUM
        
        return NewsImpact.LOW
    
    def _analyze_sentiment(self, title: str) -> float:
        """Analyze sentiment (-1.0 to 1.0)"""
        
        title_lower = title.lower()
        
        positive_count = sum(1 for word in self.positive_words if word in title_lower)
        negative_count = sum(1 for word in self.negative_words if word in title_lower)
        
        total = positive_count + negative_count
        
        if total == 0:
            return 0.0
        
        return (positive_count - negative_count) / total
    
    def _is_relevant(self, title: str, base: str, quote: str) -> bool:
        """Check if news is relevant to the pair"""
        
        title_upper = title.upper()
        return base in title_upper or quote in title_upper or "FOREX" in title_upper
    
    def _extract_currency(
        self, 
        article: Dict[str, Any], 
        base: str, 
        quote: str
    ) -> str:
        """Extract currency from article"""
        
        title = article.get("title", "").upper()
        
        if base in title:
            return base
        elif quote in title:
            return quote
        return "FOREX"
    
    def _parse_time(self, time_str: str) -> datetime:
        """Parse ISO time string"""
        
        try:
            # Remove Z and parse
            time_str = time_str.replace("Z", "+00:00")
            return datetime.fromisoformat(time_str)
        except:
            return datetime.utcnow()

# Singleton instance
news_service = NewsService()
