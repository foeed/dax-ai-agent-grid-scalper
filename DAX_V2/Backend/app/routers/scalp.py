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
TF_MULTIPLIERS = {
    "M1":  {"grid": 0.3, "sl": 0.5,  "tp": 0.8},
    "M5":  {"grid": 0.5, "sl": 0.8,  "tp": 1.0},
    "M15": {"grid": 0.7, "sl": 1.0,  "tp": 1.3},
    "H1":  {"grid": 1.0, "sl": 1.5,  "tp": 2.0},
    "H4":  {"grid": 1.5, "sl": 2.0,  "tp": 3.0},
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
    
    # Get TF multiplier
    tf = TF_MULTIPLIERS.get(request.timeframe, TF_MULTIPLIERS["M5"])
    
    # Estimate ATR for the timeframe
    atr_estimate = daily_range * 0.15 * tf["grid"]
    if request.timeframe == "M1":  atr_estimate = daily_range * 0.03
    elif request.timeframe == "M5":  atr_estimate = daily_range * 0.08
    elif request.timeframe == "M15": atr_estimate = daily_range * 0.12
    elif request.timeframe == "H1":  atr_estimate = daily_range * 0.20
    
    # === STEP 3: SIGNAL GENERATION ===
    signal = "HOLD"
    confidence = 0.5
    risk_score = 0.5
    
    if spread_pct < 0.15:  # Spread must be reasonable
        if pos_in_range < 0.30:
            signal = "BUY"
            confidence = max(0.50, 0.80 - pos_in_range - volatility * 10)
            risk_score = 0.25 + volatility * 15
        elif pos_in_range > 0.70:
            signal = "SELL"
            confidence = max(0.50, pos_in_range - 0.20 - volatility * 10)
            risk_score = 0.25 + volatility * 15
        elif pos_in_range < 0.45:
            signal = "BUY"
            confidence = 0.55
            risk_score = 0.35
        elif pos_in_range > 0.55:
            signal = "SELL"
            confidence = 0.55
            risk_score = 0.35
    else:
        risk_score = 0.70
        confidence = 0.25
    
    # Adjust with news sentiment
    if signal == "BUY" and news_sentiment > 0.2:    confidence = min(0.95, confidence + 0.1)
    if signal == "SELL" and news_sentiment < -0.2:  confidence = min(0.95, confidence + 0.1)
    if signal == "BUY" and news_sentiment < -0.3:   signal = "HOLD"
    if signal == "SELL" and news_sentiment > 0.3:   signal = "HOLD"
    
    # Clamp
    confidence = max(0.10, min(0.95, confidence))
    risk_score = max(0.10, min(0.95, risk_score))
    
    # === STEP 4: RISK ENGINE ===
    BASE_RISK_PCT = 0.02  # 2% base risk per trade
    RISK_REWARD = 2.0
    
    # Risk per trade in dollars
    risk_amount = request.account_balance * BASE_RISK_PCT * news_multiplier
    
    # Dynamic SL distance (in price)
    # SL = ATR × volatility_mult × news_mult
    vol_mult = 1.0 + volatility * 30  # Higher vol → wider stops
    sl_distance_price = atr_estimate * tf["sl"] * vol_mult
    
    # Ensure minimum SL distance (broker safe)
    min_sl = mid * 0.0005  # 0.05% minimum
    sl_distance_price = max(min_sl, sl_distance_price)
    
    # Dynamic TP distance (in price)
    tp_distance_price = sl_distance_price * RISK_REWARD * tf["tp"]
    
    # Convert to points for MQ5
    point = 0.00001 if "EUR" in request.symbol.upper() or "GBP" in request.symbol.upper() else 0.01
    sl_pts = sl_distance_price / point
    tp_pts = tp_distance_price / point
    
    # === STEP 5: LOT SIZE CALCULATION ===
    # pip_value: EURUSD 0.01 lot = $0.01 per pip (point*10)
    pip_size = point * 10
    sl_pips = sl_distance_price / pip_size
    
    if sl_pips > 0:
        pip_value_per_01_lot = 0.01  # $0.01 per pip for 0.01 lot
        target_lot = (risk_amount / sl_pips) / pip_value_per_01_lot * 0.01
    else:
        target_lot = 0.01
    
    target_lot = max(0.01, min(0.15, round(target_lot / 0.01) * 0.01))
    
    # Safety cap based on account size (margin protection)
    if request.account_balance <= 100:  target_lot = min(target_lot, 0.02)
    elif request.account_balance <= 500: target_lot = min(target_lot, 0.05)
    elif request.account_balance <= 2000: target_lot = min(target_lot, 0.10)
    
    # === STEP 6: GRID CALCULATOR ===
    # Grid spacing = ATR × 0.5 for M1/M5, ATR × 0.8 for M15, ATR × 1.0 for H1
    grid_mult = 0.4 + 0.2 * (["M1","M5","M15","H1"].index(request.timeframe) if request.timeframe in ["M1","M5","M15","H1"] else 1)
    grid_spacing_price = atr_estimate * grid_mult
    grid_spacing_pts = int(grid_spacing_price / point)
    grid_spacing_pts = max(15, min(500, grid_spacing_pts))
    
    # Order count based on volatility and confidence
    base_orders = 2
    if volatility > 0.008: base_orders = 3  # More orders in high vol
    if volatility < 0.003: base_orders = 1  # Fewer in low vol
    if confidence > 0.7:   base_orders += 1  # More when confident
    if news_caution:       base_orders = max(1, base_orders - 1)  # Fewer during news
    
    buy_orders = base_orders
    sell_orders = base_orders
    
    # Bias based on signal
    if signal == "BUY":
        buy_orders = min(5, base_orders + 1)
        sell_orders = max(1, base_orders - 1)
    elif signal == "SELL":
        sell_orders = min(5, base_orders + 1)
        buy_orders = max(1, base_orders - 1)
    
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
