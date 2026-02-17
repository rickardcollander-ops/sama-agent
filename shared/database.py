"""
Database connection and initialization
Uses Supabase REST API for cloud-hosted PostgreSQL
"""

import logging
from typing import Dict, Any, List, Optional
from supabase import create_client, Client

from .config import settings

logger = logging.getLogger(__name__)

# Supabase client (initialized lazily)
_supabase_client: Optional[Client] = None


def get_supabase() -> Client:
    """Get or create Supabase client"""
    global _supabase_client
    if _supabase_client is None:
        if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
            raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set in .env.local")
        _supabase_client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        logger.info("✅ Supabase client initialized")
    return _supabase_client


# Backwards compatibility aliases
Base = None
AsyncSessionLocal = None


async def init_db():
    """Initialize database tables via Supabase"""
    sb = get_supabase()
    
    # Create tables using Supabase SQL editor (RPC)
    # Tables are created via SQL in Supabase dashboard or via RPC
    tables_sql = [
        """
        CREATE TABLE IF NOT EXISTS keywords (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            keyword VARCHAR(255) NOT NULL,
            intent VARCHAR(50),
            priority VARCHAR(10),
            target_page VARCHAR(500),
            current_position INTEGER,
            current_clicks INTEGER DEFAULT 0,
            current_impressions INTEGER DEFAULT 0,
            current_ctr FLOAT DEFAULT 0.0,
            position_history JSONB DEFAULT '[]'::jsonb,
            added_at TIMESTAMPTZ DEFAULT NOW(),
            last_checked_at TIMESTAMPTZ,
            auto_discovered BOOLEAN DEFAULT FALSE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS seo_audits (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            audit_date TIMESTAMPTZ DEFAULT NOW(),
            critical_issues JSONB DEFAULT '[]'::jsonb,
            high_issues JSONB DEFAULT '[]'::jsonb,
            medium_issues JSONB DEFAULT '[]'::jsonb,
            low_issues JSONB DEFAULT '[]'::jsonb,
            lcp_score FLOAT,
            inp_score FLOAT,
            cls_score FLOAT,
            total_pages INTEGER,
            pages_with_issues INTEGER,
            broken_links INTEGER,
            duplicate_content INTEGER,
            auto_fixed JSONB DEFAULT '[]'::jsonb,
            summary TEXT,
            recommendations JSONB DEFAULT '[]'::jsonb
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS content_pieces (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            title VARCHAR(500) NOT NULL,
            content_type VARCHAR(50),
            content TEXT,
            meta_title VARCHAR(200),
            meta_description VARCHAR(500),
            target_keyword VARCHAR(255),
            target_url VARCHAR(500),
            word_count INTEGER,
            status VARCHAR(50) DEFAULT 'draft',
            published_at TIMESTAMPTZ,
            impressions_30d INTEGER DEFAULT 0,
            clicks_30d INTEGER DEFAULT 0,
            avg_position FLOAT,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            created_by VARCHAR(50) DEFAULT 'sama_content',
            approved_by VARCHAR(100)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS backlink_profiles (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            checked_at TIMESTAMPTZ DEFAULT NOW(),
            total_backlinks INTEGER,
            referring_domains INTEGER,
            domain_rating FLOAT,
            toxic_links JSONB DEFAULT '[]'::jsonb,
            new_links JSONB DEFAULT '[]'::jsonb,
            lost_links JSONB DEFAULT '[]'::jsonb,
            top_domains JSONB DEFAULT '[]'::jsonb
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS competitor_analyses (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            competitor VARCHAR(100) NOT NULL,
            analyzed_at TIMESTAMPTZ DEFAULT NOW(),
            keyword_gaps JSONB DEFAULT '[]'::jsonb,
            keyword_wins JSONB DEFAULT '[]'::jsonb,
            estimated_traffic INTEGER,
            domain_rating FLOAT,
            content_opportunities JSONB DEFAULT '[]'::jsonb
        )
        """
    ]
    
    for sql in tables_sql:
        try:
            sb.rpc("exec_sql", {"query": sql.strip()}).execute()
        except Exception as e:
            # If RPC doesn't exist, try postgrest approach
            logger.warning(f"RPC exec_sql not available, tables must be created via Supabase SQL Editor: {e}")
            break
    
    logger.info("✅ Database initialized via Supabase")


class SupabaseDB:
    """Helper class for common database operations"""
    
    @staticmethod
    def client() -> Client:
        return get_supabase()
    
    @staticmethod
    async def insert(table: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Insert a row"""
        result = get_supabase().table(table).insert(data).execute()
        return result.data[0] if result.data else {}
    
    @staticmethod
    async def select(table: str, columns: str = "*", filters: Optional[Dict] = None, limit: int = 100) -> List[Dict]:
        """Select rows"""
        query = get_supabase().table(table).select(columns).limit(limit)
        if filters:
            for key, value in filters.items():
                query = query.eq(key, value)
        result = query.execute()
        return result.data or []
    
    @staticmethod
    async def update(table: str, filters: Dict[str, Any], data: Dict[str, Any]) -> List[Dict]:
        """Update rows"""
        query = get_supabase().table(table).update(data)
        for key, value in filters.items():
            query = query.eq(key, value)
        result = query.execute()
        return result.data or []
    
    @staticmethod
    async def delete(table: str, filters: Dict[str, Any]) -> bool:
        """Delete rows"""
        query = get_supabase().table(table).delete()
        for key, value in filters.items():
            query = query.eq(key, value)
        query.execute()
        return True
    
    @staticmethod
    async def upsert(table: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Upsert a row"""
        result = get_supabase().table(table).upsert(data).execute()
        return result.data[0] if result.data else {}


# Global DB helper
db = SupabaseDB()
