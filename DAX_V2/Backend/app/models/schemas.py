# Pydantic models for DAX V2 API

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum

# Enums
class SignalType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    NONE = "NONE"

class MarketRegime(str, Enum):
    TRENDING = "TRENDING"
    RANGING = "RANGING"
    VOLATILE = "VOLATILE"
    UNKNOWN = "UNKNOWN"

class NewsImpact(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

# Request Models
class MarketDataRequest(BaseModel):
    symbol: str = Field(..., description="Trading symbol (e.g., EURUSD)")
    bid: float = Field(..., description="Current bid price")
    ask: float = Field(..., description="Current ask price")
    spread: float = Field(..., description="Current spread in points")
    volume: int = Field(0, description="Current volume")
    daily_high: float = Field(0.0, description="Daily high")
    daily_low: float = Field(0.0, description="Daily low")
    daily_open: float = Field(0.0, description="Daily open")

class PositionInfo(BaseModel):
    ticket: int
    symbol: str
    type: str  # "BUY" or "SELL"
    volume: float
    open_price: float
    current_price: float
    stop_loss: float
    take_profit: float
    profit: float

class TradeRequest(BaseModel):
    symbol: str
    action: SignalType
    volume: float
    stop_loss: float
    take_profit: float
    comment: Optional[str] = ""

# Response Models
class AIAnalysisResponse(BaseModel):
    signal: SignalType
    risk_score: float = Field(..., ge=0.0, le=1.0, description="Risk score 0-1")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence 0-1")
    reasoning: str
    suggested_sl: float = 0.0
    suggested_tp: float = 0.0
    suggested_volume: float = 0.01
    market_regime: MarketRegime = MarketRegime.UNKNOWN
    timestamp: datetime

class RiskAssessment(BaseModel):
    risk_score: float
    risk_level: str  # "LOW", "MEDIUM", "HIGH", "EXTREME"
    max_allowed_volume: float
    recommended_sl_distance: float
    news_risk: float = 0.0
    technical_risk: float = 0.0
    volatility_risk: float = 0.0
    warnings: List[str] = []
    timestamp: datetime

class NewsItem(BaseModel):
    title: str
    currency: str
    impact: NewsImpact
    sentiment: float = Field(0.0, ge=-1.0, le=1.0)
    time: datetime
    is_relevant: bool = True

class NewsResponse(BaseModel):
    symbol: str
    news_count: int
    high_impact_count: int
    sentiment_score: float
    news_caution: bool
    upcoming_events: List[NewsItem]
    timestamp: datetime

class PositionSizingResponse(BaseModel):
    symbol: str
    account_balance: float
    risk_percent: float
    risk_amount: float
    stop_loss_distance: float
    recommended_volume: float
    max_volume: float
    timestamp: datetime

class DashboardResponse(BaseModel):
    balance: float
    equity: float
    profit_today: float
    open_positions: int
    pending_orders: int
    spread: int
    ai_status: str
    news_status: str
    risk_level: str
    timestamp: datetime

class HealthResponse(BaseModel):
    status: str
    database: bool
    scheduler: bool
    uptime: float
    timestamp: datetime
