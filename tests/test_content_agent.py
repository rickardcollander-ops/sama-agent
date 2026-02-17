"""
Tests for Content Agent
"""

import pytest
from agents.content import ContentAgent
from agents.brand_voice import BrandVoice


@pytest.fixture
def content_agent():
    return ContentAgent()


@pytest.fixture
def brand_voice():
    return BrandVoice()


def test_content_agent_initialization(content_agent):
    """Test Content agent initializes correctly"""
    assert content_agent is not None
    assert content_agent.brand_voice is not None
    assert content_agent.model == "claude-sonnet-4-20250514"


def test_brand_voice_messaging_pillars(brand_voice):
    """Test brand voice has all messaging pillars"""
    assert "ai_native" in brand_voice.MESSAGING_PILLARS
    assert "affordable" in brand_voice.MESSAGING_PILLARS
    assert "fast_value" in brand_voice.MESSAGING_PILLARS


def test_brand_voice_proof_points(brand_voice):
    """Test brand voice has all proof points"""
    assert brand_voice.PROOF_POINTS["churn_reduction"] == "40% churn reduction"
    assert brand_voice.PROOF_POINTS["nrr_improvement"] == "25% NRR improvement"
    assert brand_voice.PROOF_POINTS["efficiency"] == "85% less manual work"
    assert brand_voice.PROOF_POINTS["pricing"] == "from $79/month"


def test_brand_voice_content_pillars(brand_voice):
    """Test brand voice has all content pillars"""
    pillars = brand_voice.CONTENT_PILLARS
    assert "churn_prevention" in pillars
    assert "health_scoring" in pillars
    assert "automation" in pillars
    assert "nrr_growth" in pillars
    assert "comparisons" in pillars
    assert "onboarding" in pillars


def test_brand_voice_system_prompt_blog(brand_voice):
    """Test system prompt generation for blog"""
    prompt = brand_voice.get_system_prompt("blog")
    assert "Successifier" in prompt
    assert "AI-native" in prompt
    assert "40% churn reduction" in prompt
    assert "1,500–2,500 words" in prompt


def test_brand_voice_system_prompt_landing_page(brand_voice):
    """Test system prompt generation for landing page"""
    prompt = brand_voice.get_system_prompt("landing_page")
    assert "Successifier" in prompt
    assert "800–1,200 words" in prompt
    assert "conversion" in prompt.lower()


def test_brand_voice_validation(brand_voice):
    """Test content validation"""
    good_content = """
    Successifier is an AI-native customer success platform that helps reduce churn by 40%.
    Our health score system provides 25% NRR improvement while delivering 85% less manual work.
    Starting from $79/month with a 14-day free trial.
    """ * 50  # Make it longer
    
    validation = brand_voice.validate_content(good_content)
    assert validation["proof_points_used"] >= 3
    assert validation["score"] > 70
    assert validation["passed"] is True


def test_brand_voice_validation_avoid_terms(brand_voice):
    """Test validation catches avoided terms"""
    bad_content = "Our client success platform helps with account scores and headcount reduction."
    
    validation = brand_voice.validate_content(bad_content)
    assert len(validation["issues"]) > 0
    assert validation["score"] < 100


@pytest.mark.asyncio
async def test_generate_blog_post(content_agent):
    """Test blog post generation"""
    # This would require Anthropic API key
    # For now, just test the method exists
    assert hasattr(content_agent, 'generate_blog_post')


@pytest.mark.asyncio
async def test_generate_landing_page(content_agent):
    """Test landing page generation"""
    assert hasattr(content_agent, 'generate_landing_page')


@pytest.mark.asyncio
async def test_generate_comparison_page(content_agent):
    """Test comparison page generation"""
    assert hasattr(content_agent, 'generate_comparison_page')


@pytest.mark.asyncio
async def test_generate_social_post(content_agent):
    """Test social post generation"""
    assert hasattr(content_agent, 'generate_social_post')
