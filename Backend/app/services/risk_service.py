# Risk Management Service - Fixed position sizing

import logging
from typing import List, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass

from app.core.config import settings
from app.models.schemas import (
    RiskAssessment, PositionInfo, MarketDataRequest,
    AIAnalysisResponse, SignalType
)

logger = logging.getLogger(__name__)

@dataclass
class TradingState:
    start_day_balance: float = 0.0
    current_day: int = -1
    daily_pnl: float = 0.0
    max_drawdown: float = 0.0
    peak_balance: float = 0.0
    trades_today: int = 0
    system_halted: bool = False

class RiskService:
    """Manages all risk-related calculations"""
    
    def __init__(self):
        self.state = TradingState()
        self.max_risk_per_trade = settings.MAX_RISK_PER_TRADE
        self.max_daily_loss = settings.MAX_DAILY_LOSS
        self.risk_reward_ratio = settings.RISK_REWARD_RATIO
    
    def calculate_position_size(
        self,
        account_balance: float,
        stop_loss_distance: float,
        risk_score: float = 0.5
    ) -> float:
        """Calculate safe position size for MT5 grid trading.
        For $40 standard account with 0.01 min lot.
        
        Returns lot size (capped at 0.01 for micro accounts)."""
        
        MIN_LOT = 0.01
        
        # For small accounts ($40-500), always use minimum lot
        if account_balance <= 500:
            return MIN_LOT
        
        # Risk per trade = 2% of balance
        risk_percent = self.max_risk_per_trade * (1.0 - risk_score * 0.3)
        risk_amount = account_balance * risk_percent
        
        # Calculate lot size based on stop loss distance
        if stop_loss_distance <= 0:
            return MIN_LOT
        
        # EURUSD: 1 pip = $1 per standard lot (100k)
        # 0.01 lot = $0.01 per pip
        # Stop loss in pips
        sl_pips = stop_loss_distance / 0.0001
        
        if sl_pips <= 0:
            return MIN_LOT
        
        pip_value_per_lot = 1.0  # $1 per pip for standard lot
        pip_value_01 = 0.01  # $0.01 per pip for 0.01 lot
        
        # How many 0.01 lots can we afford?
        max_01_lots = risk_amount / (sl_pips * pip_value_01) if sl_pips > 0 else 1
        
        # Convert to standard lots
        lots = max_01_lots / 100.0
        
        # Cap at 0.01 for small accounts
        if account_balance < 1000:
            lots = MIN_LOT
        
        return max(MIN_LOT, round(lots, 2))
    
    def assess_risk(
        self, account_balance: float, account_equity: float,
        market_data: MarketDataRequest, analysis: AIAnalysisResponse,
        positions: List[PositionInfo]
    ) -> RiskAssessment:
        """Comprehensive risk assessment"""
        self._update_daily_state(account_balance)
        
        risk_score = analysis.risk_score
        
        # Risk level
        if risk_score < 0.3:
            level = "LOW"
        elif risk_score < 0.6:
            level = "MEDIUM"
        elif risk_score < 0.8:
            level = "HIGH"
        else:
            level = "EXTREME"
        
        # Calculate stop loss distance
        mid = (market_data.bid + market_data.ask) / 2 if market_data.ask > 0 else 0
        if analysis.suggested_sl > 0 and mid > 0:
            sl_distance = abs(mid - analysis.suggested_sl)
        else:
            sl_distance = 0.00150  # Default 150 points
        
        # Calculate max volume
        max_volume = self.calculate_position_size(account_balance, sl_distance, risk_score)
        
        # Circuit breaker check
        if self.state.daily_pnl < -(account_balance * self.max_daily_loss):
            max_volume = 0
            level = "EXTREME"
        
        return RiskAssessment(
            risk_score=risk_score,
            risk_level=level,
            max_allowed_volume=max_volume,
            recommended_sl_distance=sl_distance,
            warnings=[],
            timestamp=datetime.utcnow()
        )
    
    def calculate_dynamic_sl(self, entry_price: float, is_buy: bool,
                            market_data: MarketDataRequest, risk_score: float) -> float:
        daily_range = market_data.daily_high - market_data.daily_low
        if daily_range <= 0: daily_range = entry_price * 0.01
        
        multiplier = 2.0 if risk_score > 0.7 else (1.0 if risk_score < 0.3 else 1.5)
        sl_distance = daily_range * 0.4 * multiplier
        sl_distance = max(0.00050, min(entry_price * 0.02, sl_distance))
        
        if is_buy:
            return entry_price - sl_distance
        return entry_price + sl_distance
    
    def calculate_dynamic_tp(self, entry_price: float, stop_loss: float, is_buy: bool) -> float:
        risk = abs(entry_price - stop_loss)
        reward = risk * self.risk_reward_ratio
        if is_buy:
            return entry_price + reward
        return entry_price - reward
    
    def check_circuit_breakers(self, balance: float, equity: float) -> dict:
        self._update_daily_state(balance)
        daily_loss_pct = abs(self.state.daily_pnl / self.state.start_day_balance * 100) if self.state.start_day_balance > 0 else 0
        dd_pct = ((self.state.peak_balance - equity) / self.state.peak_balance * 100) if self.state.peak_balance > 0 else 0
        
        return {
            "daily_loss_triggered": daily_loss_pct >= self.max_daily_loss * 100,
            "daily_loss_pct": daily_loss_pct,
            "drawdown_triggered": dd_pct >= 15,
            "drawdown_pct": dd_pct,
            "should_halt": daily_loss_pct >= self.max_daily_loss * 100 or dd_pct >= 15
        }
    
    def _update_daily_state(self, balance: float):
        today = datetime.utcnow().day
        if today != self.state.current_day:
            self.state.current_day = today
            self.state.start_day_balance = balance
            self.state.daily_pnl = 0
            self.state.trades_today = 0
            self.state.system_halted = False
        if balance > self.state.peak_balance:
            self.state.peak_balance = balance

risk_service = RiskService()
