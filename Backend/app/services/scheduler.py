# Background Scheduler Service - Pre-computes AI analysis

import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

class SchedulerService:
    """Handles background tasks including AI pre-computation"""
    
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.is_running = False
    
    def start(self):
        """Start scheduler with all jobs"""
        
        # Pre-compute AI analysis every 55 seconds
        self.scheduler.add_job(
            self.precompute_analysis,
            IntervalTrigger(seconds=55),
            id="precompute_ai",
            max_instances=1
        )
        
        # Health check logging every 5 minutes
        self.scheduler.add_job(
            self.health_check,
            IntervalTrigger(minutes=5),
            id="health_check"
        )
        
        self.scheduler.start()
        self.is_running = True
        logger.info("Scheduler started - AI pre-computation every 55s")
    
    def stop(self):
        """Stop scheduler"""
        if self.is_running:
            self.scheduler.shutdown()
            self.is_running = False
            logger.info("Scheduler stopped")
    
    async def precompute_analysis(self):
        """Pre-compute AI analysis in background for instant MQ5 response"""
        try:
            from app.services.deepseek_service import deepseek_service
            from app.services.news_service import news_service
            
            # Get latest news sentiment
            news = await news_service.get_forex_news("EURUSD", 24)
            news_summary = f"News sentiment: {news.sentiment_score:.2f}"
            
            # Compute fresh analysis
            await deepseek_service.refresh_cache(news_summary)
            
            logger.debug(f"AI analysis refreshed at {datetime.utcnow().isoformat()}")
        except Exception as e:
            logger.error(f"Pre-compute failed: {e}")
    
    async def health_check(self):
        logger.debug(f"System healthy at {datetime.utcnow().isoformat()}")
