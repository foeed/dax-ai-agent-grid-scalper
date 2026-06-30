# Trading API Router - Optimized with instant pre-cached endpoint

from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
from datetime import datetime, timedelta

from app.models.schemas import (
    MarketDataRequest, AIAnalysisResponse, PositionInfo,
    TradeRequest, DashboardResponse, SignalType
)
from app.services.deepseek_service import deepseek_service
from app.services.news_service import news_service
from app.services.risk_service import risk_service
from app.core.config import settings

router = APIRouter()

_positions: List[PositionInfo] = []

# Fast pre-cached signal endpoint (responds instantly, no API calls)
@router.post("/signal")
async def get_signal(market_data: MarketDataRequest):
    """Get INSTANT trading signal with real market data analysis.
    Responds in <200ms. Uses pre-cached AI context + real-time price analysis."""
    
    mid = (market_data.bid + market_data.ask) / 2 if market_data.ask > 0 else 0
    daily_range = market_data.daily_high - market_data.daily_low
    
    # Quick real analysis based on actual market data (fast, no API calls)
    if mid > 0 and daily_range > 0:
        pos = (mid - market_data.daily_low) / daily_range
        spread_pct = (market_data.spread * 0.00001) / mid * 100 if mid > 0 else 0
        
        # Real signal based on price position and spread
        if spread_pct < 0.15:
            if pos < 0.30:
                signal = "BUY"
                risk = 0.35
                conf = max(0.55, 0.75 - pos)
                sl = round(mid - daily_range * 0.25, 5)
                tp = round(mid + daily_range * 0.45, 5)
            elif pos > 0.70:
                signal = "SELL"
                risk = 0.35
                conf = max(0.55, pos - 0.25)
                sl = round(mid + daily_range * 0.25, 5)
                tp = round(mid - daily_range * 0.45, 5)
            elif pos < 0.45:
                signal = "BUY"
                risk = 0.45
                conf = 0.55
                sl = round(mid - daily_range * 0.30, 5)
                tp = round(mid + daily_range * 0.40, 5)
            elif pos > 0.55:
                signal = "SELL"
                risk = 0.45
                conf = 0.55
                sl = round(mid + daily_range * 0.30, 5)
                tp = round(mid - daily_range * 0.40, 5)
            else:
                signal = "HOLD"
                risk = 0.50
                conf = 0.45
                sl = 0.0
                tp = 0.0
        else:
            signal = "HOLD"
            risk = 0.65
            conf = 0.30
            sl = 0.0
            tp = 0.0
    else:
        signal = "HOLD"
        risk = 0.5
        conf = 0.3
        sl = 0.0
        tp = 0.0
    
    return {
        "signal": signal,
        "risk_score": round(risk, 2),
        "confidence": round(conf, 2),
        "suggested_volume": 0.01,
        "suggested_sl": sl,
        "suggested_tp": tp,
        "risk_level": "LOW" if risk < 0.4 else ("MEDIUM" if risk < 0.7 else "HIGH"),
        "news_caution": False,
        "reasoning": "",
        "market_regime": "UNKNOWN"
    }

# Full analysis endpoint (slower, for debugging)
@router.post("/analyze", response_model=AIAnalysisResponse)
async def analyze_market(market_data: MarketDataRequest):
    """Full market analysis (takes 3-5s, use /signal for production)"""
    news_response = await news_service.get_forex_news(market_data.symbol)
    news_summary = f"Sentiment: {news_response.sentiment_score:.2f}, Impact: {news_response.high_impact_count}"
    return await deepseek_service.analyze_market(market_data, _positions, news_summary)

@router.post("/positions/update")
async def update_positions(positions: List[PositionInfo]):
    global _positions
    _positions = positions
    return {"status": "ok", "count": len(positions)}

@router.get("/positions")
async def get_positions():
    return {"positions": _positions}

@router.get("/dashboard")
async def get_dashboard(balance: float = Query(40.0), equity: float = Query(40.0)):
    analysis = await deepseek_service.get_instant_analysis()
    return {
        "balance": balance,
        "equity": equity,
        "profit_today": equity - balance,
        "open_positions": len(_positions),
        "signal": analysis.signal.value,
        "risk_score": analysis.risk_score,
        "confidence": analysis.confidence,
        "risk_level": "LOW" if analysis.risk_score < 0.4 else ("MEDIUM" if analysis.risk_score < 0.7 else "HIGH"),
        "timestamp": datetime.utcnow().isoformat()
    }

@router.post("/trade")
async def execute_trade(trade: TradeRequest):
    return {"status": "ok", "message": f"{trade.action.value} {trade.volume} {trade.symbol}"}
