# DAX V2 AI Trading Backend - FastAPI Application
# Provides REST API for MQ5 Expert Advisor

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging
from datetime import datetime

from app.core.config import settings
from app.core.database import DatabaseManager
from app.routers import trading, analysis, news, risk, scalp
from app.services.scheduler import SchedulerService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Global services
database = DatabaseManager()
scheduler = SchedulerService()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    logger.info("Starting DAX V2 AI Trading Backend...")
    
    # Initialize database
    await database.initialize()
    
    # Start background scheduler
    scheduler.start()
    
    logger.info("Backend ready. Waiting for MQ5 connections...")
    
    yield
    
    # Shutdown
    logger.info("Shutting down...")
    scheduler.stop()
    await database.close()

app = FastAPI(
    title="DAX V2 AI Trading Backend",
    description="FastAPI backend for AI-enhanced grid trading with DeepSeek and News API",
    version="2.0.0",
    lifespan=lifespan
)

# CORS for MQ5 WebRequest
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(trading.router, prefix="/api/v1/trading", tags=["Trading"])
app.include_router(analysis.router, prefix="/api/v1/analysis", tags=["Analysis"])
app.include_router(news.router, prefix="/api/v1/news", tags=["News"])
app.include_router(risk.router, prefix="/api/v1/risk", tags=["Risk Management"])
app.include_router(scalp.router, prefix="/api/v1/scalp", tags=["Scalping Engine"])

@app.get("/")
async def root():
    return {
        "service": "DAX V2 AI Trading Backend",
        "version": "2.0.0",
        "status": "running",
        "timestamp": datetime.utcnow().isoformat()
    }

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "database": await database.health_check(),
        "scheduler": scheduler.is_running
    }

@app.post("/api/v1/mq5/connect")
async def mq5_connect():
    """MQ5 EA registration endpoint"""
    logger.info("MQ5 Expert Advisor connected")
    return {"status": "connected", "message": "Backend ready for trading"}
