"""
SAMA 2.0 Configuration
Centralized settings management using Pydantic
"""

from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    """Application settings"""
    
    # Environment
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    
    # Anthropic
    ANTHROPIC_API_KEY: str = ""
    
    # Google APIs
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_ADS_DEVELOPER_TOKEN: str = ""
    GOOGLE_ADS_CLIENT_ID: str = ""
    GOOGLE_ADS_CLIENT_SECRET: str = ""
    GOOGLE_ADS_REFRESH_TOKEN: str = ""
    GOOGLE_ADS_CUSTOMER_ID: str = ""
    
    # SEO APIs
    SEMRUSH_API_KEY: str = ""
    AHREFS_API_KEY: str = ""
    
    # Social Media
    TWITTER_API_KEY: str = ""
    TWITTER_API_SECRET: str = ""
    TWITTER_ACCESS_TOKEN: str = ""
    TWITTER_ACCESS_SECRET: str = ""
    TWITTER_BEARER_TOKEN: str = ""
    
    # Database
    DATABASE_URL: str = "postgresql://localhost:5432/sama"
    PGVECTOR_ENABLED: bool = True
    
    # Supabase
    SUPABASE_URL: str = ""
    SUPABASE_KEY: str = ""
    
    # Vector Store
    PINECONE_API_KEY: str = ""
    PINECONE_ENVIRONMENT: str = ""
    PINECONE_INDEX_NAME: str = "sama-memory"
    
    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    
    # Temporal
    TEMPORAL_HOST: str = "localhost:7233"
    TEMPORAL_NAMESPACE: str = "sama"
    
    # Monitoring
    SENTRY_DSN: str = ""
    DATADOG_API_KEY: str = ""
    
    # Application
    SUCCESSIFIER_DOMAIN: str = "successifier.com"
    SUCCESSIFIER_CMS_API_URL: str = "https://successifier.com/api"
    SUCCESSIFIER_CMS_API_KEY: str = ""
    
    # LinkedIn Agent Integration
    LINKEDIN_AGENT_EVENT_BUS_ENABLED: bool = True
    LINKEDIN_AGENT_API_URL: str = "http://localhost:3003/api"
    
    # Human-in-the-Loop
    AUTO_PUBLISH_BLOG_POSTS: bool = False
    AUTO_PUBLISH_LANDING_PAGES: bool = False
    AUTO_PUBLISH_SOCIAL_POSTS: bool = True
    AUTO_RESPOND_REVIEWS_POSITIVE: bool = True
    AUTO_RESPOND_REVIEWS_NEGATIVE: bool = False
    BUDGET_CHANGE_APPROVAL_THRESHOLD: float = 0.30
    
    # CORS
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:3003",
        "https://app.successifier.com"
    ]
    
    class Config:
        env_file = ".env.local"
        case_sensitive = True
        extra = "ignore"


settings = Settings()
