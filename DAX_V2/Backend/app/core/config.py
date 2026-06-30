# Configuration settings for DAX V2 Backend

from pydantic_settings import BaseSettings
from typing import Optional
import os
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseSettings):
    # Application
    APP_NAME: str = "DAX V2 AI Trading Backend"
    APP_VERSION: str = "2.0.0"
    DEBUG: bool = False
    
    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    
    # API Keys
    DEEPSEEK_API_KEY: str = ""
    NEWS_API_KEY: str = ""
    
    # DeepSeek Settings
    DEEPSEEK_MODEL: str = "deepseek-chat"
    DEEPSEEK_MAX_TOKENS: int = 1000
    DEEPSEEK_TEMPERATURE: float = 0.3
    
    # News API Settings
    NEWS_API_ENABLED: bool = True
    NEWS_FETCH_INTERVAL: int = 300
    
    # Trading Defaults
    DEFAULT_SYMBOL: str = "EURUSD"
    MAX_RISK_PER_TRADE: float = 0.02
    MAX_DAILY_LOSS: float = 0.10
    RISK_REWARD_RATIO: float = 2.0
    
    # Security
    API_SECRET_KEY: str = "your-secret-key-change-in-production"
    
    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()
