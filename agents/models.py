"""
Database models for SAMA 2.0
Pydantic models for Supabase REST API
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Any
from datetime import datetime
import uuid


# Table name constants
KEYWORDS_TABLE = "seo_keywords"
SEO_AUDITS_TABLE = "seo_audits"
CONTENT_PIECES_TABLE = "content_pieces"
BACKLINK_PROFILES_TABLE = "backlink_profiles"
COMPETITOR_ANALYSES_TABLE = "competitor_analyses"


class Keyword(BaseModel):
    """Keyword tracking model"""
    id: Optional[str] = None
    keyword: str
    intent: Optional[str] = None
    priority: Optional[str] = None
    target_page: Optional[str] = None
    current_position: Optional[int] = None
    current_clicks: int = 0
    current_impressions: int = 0
    current_ctr: float = 0.0
    position_history: List[Any] = []
    added_at: Optional[str] = None
    last_checked_at: Optional[str] = None
    auto_discovered: bool = False


class SEOAudit(BaseModel):
    """SEO audit results"""
    id: Optional[str] = None
    audit_date: Optional[str] = None
    critical_issues: List[Any] = []
    high_issues: List[Any] = []
    medium_issues: List[Any] = []
    low_issues: List[Any] = []
    lcp_score: Optional[float] = None
    inp_score: Optional[float] = None
    cls_score: Optional[float] = None
    total_pages: Optional[int] = None
    pages_with_issues: Optional[int] = None
    broken_links: Optional[int] = None
    duplicate_content: Optional[int] = None
    auto_fixed: List[Any] = []
    summary: Optional[str] = None
    recommendations: List[Any] = []


class ContentPiece(BaseModel):
    """Generated content tracking"""
    id: Optional[str] = None
    title: str
    content_type: Optional[str] = None
    content: Optional[str] = None
    meta_title: Optional[str] = None
    meta_description: Optional[str] = None
    target_keyword: Optional[str] = None
    target_url: Optional[str] = None
    word_count: Optional[int] = None
    status: str = "draft"
    published_at: Optional[str] = None
    impressions_30d: int = 0
    clicks_30d: int = 0
    avg_position: Optional[float] = None
    created_at: Optional[str] = None
    created_by: str = "sama_content"
    approved_by: Optional[str] = None


class BacklinkProfile(BaseModel):
    """Backlink monitoring"""
    id: Optional[str] = None
    checked_at: Optional[str] = None
    total_backlinks: Optional[int] = None
    referring_domains: Optional[int] = None
    domain_rating: Optional[float] = None
    toxic_links: List[Any] = []
    new_links: List[Any] = []
    lost_links: List[Any] = []
    top_domains: List[Any] = []


class CompetitorAnalysis(BaseModel):
    """Competitor tracking"""
    id: Optional[str] = None
    competitor: str
    analyzed_at: Optional[str] = None
    keyword_gaps: List[Any] = []
    keyword_wins: List[Any] = []
    estimated_traffic: Optional[int] = None
    domain_rating: Optional[float] = None
    content_opportunities: List[Any] = []
