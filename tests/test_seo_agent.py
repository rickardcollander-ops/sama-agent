"""
Tests for SEO Agent
"""

import pytest
from agents.seo import SEOAgent


@pytest.fixture
def seo_agent():
    return SEOAgent()


def test_seo_agent_initialization(seo_agent):
    """Test SEO agent initializes correctly"""
    assert seo_agent is not None
    assert len(seo_agent.TARGET_KEYWORDS) == 14
    assert len(seo_agent.COMPETITORS) == 3


def test_target_keywords_structure(seo_agent):
    """Test target keywords have correct structure"""
    for keyword in seo_agent.TARGET_KEYWORDS:
        assert "keyword" in keyword
        assert "intent" in keyword
        assert "priority" in keyword
        assert "target_page" in keyword
        assert keyword["priority"] in ["P0", "P1", "P2"]
        assert keyword["intent"] in ["commercial", "informational", "transactional"]


@pytest.mark.asyncio
async def test_initialize_keywords(seo_agent):
    """Test keyword initialization"""
    # This would require database setup
    # For now, just test the method exists
    assert hasattr(seo_agent, 'initialize_keywords')


@pytest.mark.asyncio
async def test_run_weekly_audit(seo_agent):
    """Test weekly audit execution"""
    # This would require full setup
    # For now, just test the method exists
    assert hasattr(seo_agent, 'run_weekly_audit')


@pytest.mark.asyncio
async def test_track_keyword_rankings(seo_agent):
    """Test keyword tracking"""
    # This would require database setup
    # For now, just test the method exists
    assert hasattr(seo_agent, 'track_keyword_rankings')
