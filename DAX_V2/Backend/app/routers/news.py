# News API Router

from fastapi import APIRouter, HTTPException
from typing import Optional

from app.models.schemas import NewsResponse
from app.services.news_service import news_service

router = APIRouter()

@router.get("/{symbol}", response_model=NewsResponse)
async def get_news(symbol: str, hours: int = 24):
    """Get forex news for a symbol"""
    
    if len(symbol) < 6:
        raise HTTPException(status_code=400, detail="Invalid symbol format")
    
    response = await news_service.get_forex_news(symbol, hours)
    return response

@router.get("/{symbol}/sentiment")
async def get_sentiment(symbol: str):
    """Get news sentiment score"""
    
    response = await news_service.get_forex_news(symbol, 24)
    
    return {
        "symbol": symbol,
        "sentiment": response.sentiment_score,
        "news_count": response.news_count,
        "high_impact_count": response.high_impact_count,
        "news_caution": response.news_caution
    }

@router.get("/{symbol}/high-impact")
async def check_high_impact(symbol: str, minutes: int = 60):
    """Check if high impact news is imminent"""
    
    response = await news_service.get_forex_news(symbol, 1)
    
    from datetime import datetime, timedelta
    threshold = datetime.utcnow() + timedelta(minutes=minutes)
    
    imminent = [
        e for e in response.upcoming_events
        if e.time <= threshold and e.impact.value in ["HIGH", "CRITICAL"]
    ]
    
    return {
        "symbol": symbol,
        "minutes_threshold": minutes,
        "high_impact_imminent": len(imminent) > 0,
        "events": imminent
    }
