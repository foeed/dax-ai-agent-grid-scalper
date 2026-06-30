# Scalping Engine Router - Full AI Pipeline
# Unified endpoint: /api/v1/scalp/plan
# Returns ALL parameters: lot, sl, tp, grid distance, max orders, risk level

from fastapi import APIRouter, HTTPException
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel

from app.models.schemas import MarketDataRequest, PositionInfo

router = APIRouter()

class ScalpPlanRequest(BaseModel):
    symbol: str
    bid: float
    ask: float
    spread: float
    volume: int = 0
    daily_high: float = 0.0
    daily_low: float = 0.0
    daily_open: float = 0.0
    account_balance: float = 40.0
    account_equity: float = 40.0
    timeframe: str = "M5"  # M1, M5, M15, H1
    open_positions: int = 0

class ScalpPlanResponse(BaseModel):
    signal: str  # BUY, SELL, HOLD
    lot_size: float
    sl_distance_pts: float
    tp_distance_pts: float
    grid_spacing_pts: int
    buy_orders: int
    sell_orders: int
    risk_score: float
    confidence: float
    risk_level: str
    news_caution: bool
    atr: float
    volatility: float
    reasoning: str

# Timeframe multipliers for volatility/scaling
# HFT Timeline multipliers - tight, fast, many
HFT = {
    "M1":  {"grid_factor": 0.25, "sl_ratio": 0.3, "tp_ratio": 0.4, "max_orders": 8},
    "M5":  {"grid_factor": 0.30, "sl_ratio": 0.4, "tp_ratio": 0.5, "max_orders": 7},
    "M15": {"grid_factor": 0.40, "sl_ratio": 0.5, "tp_ratio": 0.7, "max_orders": 6},
    "H1":  {"grid_factor": 0.50, "sl_ratio": 0.7, "tp_ratio": 1.0, "max_orders": 5},
}

@router.post("/plan", response_model=ScalpPlanResponse)
async def get_scalp_plan(request: ScalpPlanRequest):
    """
    Unified AI scalping plan endpoint.
    
    Pipeline:
    1. News check → impact multiplier
    2. Technical analysis → regime, volatility, ATR
    3. Risk engine → lot size, SL, TP
    4. Grid calculator → spacing, order count
    5. Return complete plan for MQ5 EA (zero EA-side computation)
    """
    
    # === STEP 1: NEWS ANALYSIS ===
    news_caution = False
    news_multiplier = 1.0
    news_sentiment = 0.0
    
    try:
        from app.services.news_service import news_service
        news = await news_service.get_forex_news(request.symbol, 4)
        news_caution = news.high_impact_count > 0
        news_sentiment = news.sentiment_score
        if news_caution:
            news_multiplier = 0.5  # Half risk during high impact
        elif news.sentiment_score > 0.3:
            news_multiplier = 1.2  # Boost during positive sentiment
        elif news.sentiment_score < -0.3:
            news_multiplier = 0.8  # Reduce during negative sentiment
    except Exception:
        pass
    
    # === STEP 2: TECHNICAL ANALYSIS ===
    mid = (request.bid + request.ask) / 2 if request.ask > 0 else 0
    if mid <= 0: mid = 1.0
    
    daily_range = request.daily_high - request.daily_low
    if daily_range <= 0: daily_range = mid * 0.005
    
    volatility = daily_range / mid if mid > 0 else 0.005
    spread_pct = (request.spread * 0.00001) / mid * 100 if mid > 0 else 0
    
    # Price position in daily range (0=bottom, 1=top)
    pos_in_range = (mid - request.daily_low) / daily_range if daily_range > 0 else 0.5
    
    # Get HFT multiplier
    tf = HFT.get(request.timeframe, HFT["M5"])
    
    # Estimate ATR for the timeframe
    atr_estimate = daily_range * 0.15 * tf["grid_factor"]
    if request.timeframe == "M1":  atr_estimate = daily_range * 0.03
    elif request.timeframe == "M5":  atr_estimate = daily_range * 0.06
    elif request.timeframe == "M15": atr_estimate = daily_range * 0.10
    elif request.timeframe == "H1":  atr_estimate = daily_range * 0.18
    
    # === STEP 3: SIGNAL GENERATION (HFT - always trade) ===
    signal = "BUY"  # Default: always deploy grid
    confidence = 0.6
    risk_score = 0.30
    
    # Adjust bias based on price position
    if spread_pct < 0.15:
        if pos_in_range < 0.35:
            signal = "BUY"
            confidence = max(0.55, 0.85 - abs(pos_in_range - 0.15) * 2)
        elif pos_in_range > 0.65:
            signal = "SELL"  
            confidence = max(0.55, 0.85 - abs(pos_in_range - 0.85) * 2)
        else:
            signal = "BUY"  # HFT: always trade, even middle
            confidence = 0.55
            risk_score = 0.25
    else:
        risk_score = 0.60  # Wider spread = higher risk but still trade
    
    # Adjust with news sentiment
    if signal == "BUY" and news_sentiment > 0.2:    confidence = min(0.95, confidence + 0.1)
    if signal == "SELL" and news_sentiment < -0.2:  confidence = min(0.95, confidence + 0.1)
    if signal == "BUY" and news_sentiment < -0.3:   signal = "HOLD"
    if signal == "SELL" and news_sentiment > 0.3:   signal = "HOLD"
    
    # Clamp
    confidence = max(0.10, min(0.95, confidence))
    risk_score = max(0.10, min(0.95, risk_score))
    
    # === STEP 4: RISK ENGINE (HFT: tight stops, fast profit) ===
    # Dynamic SL based on ATR - tight for HFT
    vol_mult = 1.0 + volatility * 15
    sl_distance_price = atr_estimate * tf["sl_ratio"] * vol_mult
    min_sl = mid * 0.0003  # 0.03% minimum (tighter for HFT)
    sl_distance_price = max(min_sl, sl_distance_price)
    
    # Dynamic TP - even tighter for fast scalping
    tp_multiplier = tf["tp_ratio"]  # Already very tight for HFT
    tp_distance_price = sl_distance_price * tp_multiplier
    
    # Convert to points for MQ5
    point = 0.00001 if "EUR" in request.symbol.upper() or "GBP" in request.symbol.upper() else 0.01
    sl_pts = sl_distance_price / point
    tp_pts = tp_distance_price / point
    
    # HFT: clamp for safety
    sl_pts = max(20, min(200, sl_pts))
    tp_pts = max(15, min(150, tp_pts))
    
    # === STEP 5: LOT SIZE CALCULATION ===
    # HFT mode: always 0.01 per position, many positions simultaneously
    # Risk managed by stop loss per position, not total account%
    
    # Fixed micro lot for HFT (profit per trade is small, volume makes it up)
    target_lot = 0.01
    
    # Safety cap
    if request.account_balance <= 100:  target_lot = 0.01
    elif request.account_balance <= 500: target_lot = 0.02
    elif request.account_balance <= 2000: target_lot = 0.03
    else: target_lot = 0.05
    
    # === STEP 5: GRID CALCULATOR (HFT: many tight orders) ===
    # Grid spacing = ATR × tiny factor for HFT
    grid_spacing_price = atr_estimate * tf["grid_factor"]
    grid_spacing_pts = int(grid_spacing_price / point)
    grid_spacing_pts = max(10, min(200, grid_spacing_pts))
    
    # Many orders per side (HFT grid)
    max_orders = tf["max_orders"]
    base_orders = max(3, max_orders - 2)  # Minimum 3 per side
    if volatility > 0.006: base_orders = max_orders  # Max orders in high vol
    if news_caution: base_orders = max(2, base_orders - 3)  # Reduce during news
    
    buy_orders = base_orders
    sell_orders = base_orders
    
    # Bias based on signal direction
    if signal == "BUY":
        buy_orders = min(max_orders, base_orders + 2)
        sell_orders = max(2, base_orders - 1)
    elif signal == "SELL":
        sell_orders = min(max_orders, base_orders + 2)
        buy_orders = max(2, base_orders - 1)
    
    # Risk level label
    if risk_score < 0.35: risk_level = "LOW"
    elif risk_score < 0.60: risk_level = "MEDIUM"
    elif risk_score < 0.80: risk_level = "HIGH"
    else: risk_level = "EXTREME"
    
    return ScalpPlanResponse(
        signal=signal,
        lot_size=target_lot,
        sl_distance_pts=round(sl_pts, 0),
        tp_distance_pts=round(tp_pts, 0),
        grid_spacing_pts=grid_spacing_pts,
        buy_orders=buy_orders,
        sell_orders=sell_orders,
        risk_score=round(risk_score, 2),
        confidence=round(confidence, 2),
        risk_level=risk_level,
        news_caution=news_caution,
        atr=round(atr_estimate, 5),
        volatility=round(volatility, 4),
        reasoning=f"pos={pos_in_range:.1%} vol={volatility:.3%} news={'CAUTION' if news_caution else 'OK'} spread={spread_pct:.2f}%"
    )
