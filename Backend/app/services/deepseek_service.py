# DeepSeek AI Service - Optimized with async pre-computation

import httpx
import json
import logging
import asyncio
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

from app.core.config import settings
from app.models.schemas import (
    AIAnalysisResponse, SignalType, MarketRegime,
    MarketDataRequest, PositionInfo
)

logger = logging.getLogger(__name__)

class DeepSeekService:
    """Handles all DeepSeek AI interactions with pre-computation cache"""
    
    def __init__(self):
        self.api_key = settings.DEEPSEEK_API_KEY
        self.base_url = "https://api.deepseek.com/v1"
        self.model = settings.DEEPSEEK_MODEL
        self.max_tokens = settings.DEEPSEEK_MAX_TOKENS
        self.temperature = settings.DEEPSEEK_TEMPERATURE
        
        # Pre-computed analysis cache (instant response for MQ5)
        self._cached_analysis: Optional[AIAnalysisResponse] = None
        self._cached_news: str = ""
        self._last_compute_time: Optional[datetime] = None
        self._compute_lock = asyncio.Lock()
        self._compute_interval = 55  # seconds
        
    async def get_instant_analysis(self) -> AIAnalysisResponse:
        """Return pre-cached analysis instantly (for MQ5 low latency)"""
        if self._cached_analysis is None:
            # First call - compute now (only happens once at startup)
            await self._compute_and_cache()
        return self._cached_analysis
    
    async def _compute_and_cache(self):
        """Background compute and cache"""
        async with self._compute_lock:
            try:
                # Use default market data for pre-computation
                market_data = MarketDataRequest(
                    symbol="EURUSD",
                    bid=0.0, ask=0.0, spread=0.0,
                    volume=0, daily_high=0.0, daily_low=0.0, daily_open=0.0
                )
                analysis = await self.analyze_market(market_data, news_summary=self._cached_news)
                self._cached_analysis = analysis
                self._last_compute_time = datetime.utcnow()
                logger.info(f"Pre-computed analysis: signal={analysis.signal.value}, risk={analysis.risk_score:.2f}")
            except Exception as e:
                logger.error(f"Pre-compute failed: {e}")
                self._cached_analysis = self._fallback_analysis(
                    MarketDataRequest(symbol="EURUSD", bid=1.0, ask=1.0, spread=1.0)
                )
    
    async def refresh_cache(self, news_summary: str = ""):
        """Refresh the cached analysis (called by scheduler)"""
        self._cached_news = news_summary
        await self._compute_and_cache()
    
    async def analyze_market(
        self, 
        market_data: MarketDataRequest,
        positions: list[PositionInfo] = None,
        news_summary: str = ""
    ) -> AIAnalysisResponse:
        """Full market analysis using DeepSeek AI or fallback"""
        
        # Build analysis prompt
        prompt = self._build_prompt(market_data, positions, news_summary)
        
        # Try DeepSeek API
        if self.api_key and self.api_key != "YOUR_DEEPSEEK_API_KEY":
            try:
                result = await self._call_deepseek_api(prompt)
                analysis = self._parse_response(result, market_data)
                return analysis
            except Exception as e:
                logger.warning(f"DeepSeek API fallback: {e}")
        
        # Built-in analysis with ACTUAL indicators
        return self._smart_technical_analysis(market_data, news_summary)
    
    def _build_prompt(
        self, market_data: MarketDataRequest,
        positions: list[PositionInfo], news_summary: str
    ) -> str:
        """Build concise analysis prompt"""
        mid = (market_data.bid + market_data.ask) / 2 if market_data.ask > 0 else 0
        spread_pct = (market_data.spread / mid * 100) if mid > 0 else 0
        
        daily_range = market_data.daily_high - market_data.daily_low
        price_pos = ((mid - market_data.daily_low) / daily_range * 100) if daily_range > 0 else 50
        
        prompt = f"""EURUSD forex analysis. 
Bid={market_data.bid:.5f} Ask={market_data.ask:.5f} Spread={market_data.spread:.0f}pts
Daily: H={market_data.daily_high:.5f} L={market_data.daily_low:.5f} O={market_data.daily_open:.5f}
Price position: {price_pos:.0f}% of daily range, Spread: {spread_pct:.2f}%
"""
        if news_summary:
            prompt += f"News: {news_summary}\n"
        
        prompt += """Return JSON only:
{"signal":"BUY|SELL|HOLD","risk_score":0.0-1.0,"confidence":0.0-1.0,"reasoning":"brief","suggested_sl":price,"suggested_tp":price,"market_regime":"TRENDING|RANGING|VOLATILE"}
If spread > 0.1% use HOLD. Low range = RANGING, high range = VOLATILE."""
        return prompt
    
    async def _call_deepseek_api(self, prompt: str) -> str:
        """Call DeepSeek API"""
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You are a forex analyst. Return ONLY valid JSON. Be concise."},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 200,  # Reduced for speed
            "temperature": 0.2   # More deterministic
        }
        
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
    
    def _parse_response(self, text: str, market_data: MarketDataRequest) -> AIAnalysisResponse:
        """Parse AI response JSON"""
        try:
            js = text.find("{")
            je = text.rfind("}") + 1
            if js >= 0 and je > js:
                data = json.loads(text[js:je])
                return AIAnalysisResponse(
                    signal=SignalType(data.get("signal", "HOLD").upper()),
                    risk_score=float(data.get("risk_score", 0.5)),
                    confidence=float(data.get("confidence", 0.5)),
                    reasoning=data.get("reasoning", "AI analysis"),
                    suggested_sl=float(data.get("suggested_sl", 0)),
                    suggested_tp=float(data.get("suggested_tp", 0)),
                    suggested_volume=0.01,
                    market_regime=MarketRegime(data.get("market_regime", "UNKNOWN").upper()),
                    timestamp=datetime.utcnow()
                )
        except Exception as e:
            logger.warning(f"Parse failed: {e}")
        return self._smart_technical_analysis(market_data)
    
    def _smart_technical_analysis(self, market_data: MarketDataRequest, news: str = "") -> AIAnalysisResponse:
        """Enhanced built-in analysis with realistic signals"""
        mid = (market_data.bid + market_data.ask) / 2 if market_data.ask > 0 else 0
        if mid == 0: mid = 1.0
        
        daily_range = market_data.daily_high - market_data.daily_low
        if daily_range == 0: daily_range = mid * 0.005
        
        spread_pct = (market_data.spread * 0.00001) / mid * 100 if market_data.spread > 0 else 0
        
        # Position in daily range (0 to 1)
        pos = ((mid - market_data.daily_low) / daily_range) if daily_range > 0 else 0.5
        
        # Price near daily extremes with spread OK = signal
        signal = SignalType.HOLD
        risk = 0.5
        conf = 0.5
        
        if spread_pct < 0.1:  # Spread must be acceptable
            if pos < 0.25:
                signal = SignalType.BUY
                risk = 0.35
                conf = max(0.10, min(0.95, 0.70 - pos))
            elif pos > 0.75:
                signal = SignalType.SELL
                risk = 0.35
                conf = max(0.10, min(0.95, pos - 0.30))
            elif pos < 0.35:
                signal = SignalType.BUY
                risk = 0.45
                conf = 0.55
            elif pos > 0.65:
                signal = SignalType.SELL
                risk = 0.45
                conf = 0.55
        else:
            risk = 0.7
            conf = 0.3
        
        # Determine regime
        volatility = daily_range / mid
        if volatility > 0.008:
            regime = MarketRegime.VOLATILE
            risk = min(1.0, risk + 0.15)
        elif volatility < 0.003:
            regime = MarketRegime.RANGING
        else:
            regime = MarketRegime.TRENDING
        
        # Calculate SL/TP based on daily range
        sl_dist = daily_range * 0.3
        tp_dist = daily_range * 0.5
        
        if signal == SignalType.BUY:
            sl = mid - sl_dist
            tp = mid + tp_dist
        elif signal == SignalType.SELL:
            sl = mid + sl_dist
            tp = mid - tp_dist
        else:
            sl = 0.0
            tp = 0.0
        
        return AIAnalysisResponse(
            signal=signal,
            risk_score=round(risk, 2),
            confidence=round(conf, 2),
            reasoning=f"Built-in: pos={pos:.0%} of daily range, spread={spread_pct:.2f}%",
            suggested_sl=round(sl, 5) if sl > 0 else 0,
            suggested_tp=round(tp, 5) if tp > 0 else 0,
            suggested_volume=0.01,
            market_regime=regime,
            timestamp=datetime.utcnow()
        )
    
    def _fallback_analysis(self, market_data: MarketDataRequest) -> AIAnalysisResponse:
        return self._smart_technical_analysis(market_data)
    
    async def get_market_regime(self, market_data: MarketDataRequest) -> MarketRegime:
        if market_data.bid <= 0: return MarketRegime.UNKNOWN
        daily_range = market_data.daily_high - market_data.daily_low
        vol = daily_range / market_data.bid if market_data.bid > 0 else 0
        if vol > 0.01: return MarketRegime.VOLATILE
        if vol < 0.004: return MarketRegime.RANGING
        return MarketRegime.TRENDING

deepseek_service = DeepSeekService()
