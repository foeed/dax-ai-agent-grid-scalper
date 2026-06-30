# Analysis API Router

from fastapi import APIRouter
from app.models.schemas import MarketDataRequest
from app.services.deepseek_service import deepseek_service

router = APIRouter()

@router.post("/market")
async def analyze_market(market_data: MarketDataRequest):
    """Full market analysis with AI"""
    
    analysis = await deepseek_service.analyze_market(market_data)
    
    return {
        "signal": analysis.signal.value,
        "risk_score": analysis.risk_score,
        "confidence": analysis.confidence,
        "reasoning": analysis.reasoning,
        "market_regime": analysis.market_regime.value,
        "suggested_sl": analysis.suggested_sl,
        "suggested_tp": analysis.suggested_tp,
        "timestamp": analysis.timestamp.isoformat()
    }

@router.post("/regime")
async def get_market_regime(market_data: MarketDataRequest):
    """Get current market regime"""
    
    regime = await deepseek_service.get_market_regime(market_data)
    
    return {
        "symbol": market_data.symbol,
        "regime": regime.value,
        "description": {
            "TRENDING": "Market is trending - follow direction",
            "RANGING": "Market is ranging - use mean reversion",
            "VOLATILE": "High volatility - reduce position size",
            "UNKNOWN": "Unable to determine regime"
        }.get(regime.value, "Unknown")
    }
