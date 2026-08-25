# Risk Management API Router

from fastapi import APIRouter
from typing import List

from app.models.schemas import (
    RiskAssessment, PositionInfo, MarketDataRequest,
    AIAnalysisResponse, PositionSizingResponse
)
from app.services.risk_service import risk_service
from app.services.deepseek_service import deepseek_service

router = APIRouter()

@router.post("/assess", response_model=RiskAssessment)
async def assess_risk(
    balance: float,
    equity: float,
    market_data: MarketDataRequest,
    positions: List[PositionInfo] = []
):
    """Perform comprehensive risk assessment"""
    
    # Get AI analysis
    analysis = await deepseek_service.analyze_market(market_data, positions)
    
    # Assess risk
    risk = risk_service.assess_risk(
        account_balance=balance,
        account_equity=equity,
        market_data=market_data,
        analysis=analysis,
        positions=positions
    )
    
    return risk

@router.post("/position-size", response_model=PositionSizingResponse)
async def calculate_position_size(
    symbol: str,
    balance: float,
    stop_loss_distance: float,
    risk_score: float = 0.5
):
    """Calculate optimal position size"""
    
    result = risk_service.calculate_position_size(
        symbol=symbol,
        account_balance=balance,
        stop_loss_distance=stop_loss_distance,
        risk_score=risk_score
    )
    
    return PositionSizingResponse(
        symbol=symbol,
        account_balance=balance,
        risk_percent=result["risk_percent"],
        risk_amount=result["risk_amount"],
        stop_loss_distance=stop_loss_distance,
        recommended_volume=result["volume"],
        max_volume=result["volume"] * 1.5,
        timestamp=__import__("datetime").datetime.utcnow()
    )

@router.post("/dynamic-sl")
async def calculate_dynamic_stop_loss(
    entry_price: float,
    is_buy: bool,
    market_data: MarketDataRequest,
    risk_score: float = 0.5
):
    """Calculate dynamic stop loss"""
    
    sl = risk_service.calculate_dynamic_sl(
        entry_price=entry_price,
        is_buy=is_buy,
        market_data=market_data,
        risk_score=risk_score
    )
    
    return {
        "entry_price": entry_price,
        "is_buy": is_buy,
        "stop_loss": sl,
        "distance": abs(entry_price - sl)
    }

@router.post("/dynamic-tp")
async def calculate_dynamic_take_profit(
    entry_price: float,
    stop_loss: float,
    is_buy: bool
):
    """Calculate take profit based on risk-reward ratio"""
    
    tp = risk_service.calculate_dynamic_tp(
        entry_price=entry_price,
        stop_loss=stop_loss,
        is_buy=is_buy
    )
    
    return {
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profit": tp,
        "risk": abs(entry_price - stop_loss),
        "reward": abs(tp - entry_price),
        "ratio": risk_service.risk_reward_ratio
    }

@router.post("/circuit-breakers")
async def check_circuit_breakers(balance: float, equity: float):
    """Check circuit breaker status"""
    
    return risk_service.check_circuit_breakers(balance, equity)
