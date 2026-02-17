"""
Tests for Google Ads Agent
"""

import pytest
from agents.ads import GoogleAdsAgent


@pytest.fixture
def ads_agent():
    return GoogleAdsAgent()


def test_ads_agent_initialization(ads_agent):
    """Test Ads agent initializes correctly"""
    assert ads_agent is not None
    assert len(ads_agent.CAMPAIGN_STRUCTURE) == 5
    assert len(ads_agent.RSA_HEADLINE_BANK) == 15
    assert len(ads_agent.RSA_DESCRIPTION_BANK) == 4


def test_campaign_structure(ads_agent):
    """Test campaign structure is complete"""
    campaigns = ads_agent.CAMPAIGN_STRUCTURE
    
    assert "brand" in campaigns
    assert "core_product" in campaigns
    assert "churn_prevention" in campaigns
    assert "health_scoring" in campaigns
    assert "competitor_conquest" in campaigns
    
    # Check each campaign has required fields
    for campaign_type, config in campaigns.items():
        assert "name" in config
        assert "ad_groups" in config
        assert "match_types" in config
        assert "bidding_strategy" in config
        assert "keywords" in config


def test_rsa_headline_bank(ads_agent):
    """Test RSA headline bank"""
    headlines = ads_agent.RSA_HEADLINE_BANK
    
    # Check we have 15 headlines
    assert len(headlines) == 15
    
    # Check all headlines are within character limit
    for headline in headlines:
        assert len(headline) <= 30, f"Headline too long: {headline}"
    
    # Check headlines include proof points
    all_headlines = " ".join(headlines)
    assert "40%" in all_headlines or "Reduce Churn" in all_headlines
    assert "$79" in all_headlines or "Affordable" in all_headlines


def test_rsa_description_bank(ads_agent):
    """Test RSA description bank"""
    descriptions = ads_agent.RSA_DESCRIPTION_BANK
    
    # Check we have 4 descriptions
    assert len(descriptions) == 4
    
    # Check all descriptions are within character limit
    for description in descriptions:
        assert len(description) <= 90, f"Description too long: {description}"
    
    # Check descriptions include proof points
    all_descriptions = " ".join(descriptions)
    assert "40%" in all_descriptions
    assert "25%" in all_descriptions
    assert "$79" in all_descriptions


def test_optimization_rules(ads_agent):
    """Test optimization rules are defined"""
    rules = ads_agent.OPTIMIZATION_RULES
    
    assert "pause_underperformer" in rules
    assert "scale_winner" in rules
    assert "quality_score_fix" in rules
    assert "budget_reallocation" in rules
    assert "negative_keyword_harvest" in rules
    
    # Check each rule has required fields
    for rule_name, rule_config in rules.items():
        assert "condition" in rule_config
        assert "action" in rule_config
        assert "schedule" in rule_config


@pytest.mark.asyncio
async def test_generate_rsa(ads_agent):
    """Test RSA generation"""
    assert hasattr(ads_agent, 'generate_rsa')


@pytest.mark.asyncio
async def test_optimize_bids(ads_agent):
    """Test bid optimization"""
    assert hasattr(ads_agent, 'optimize_bids')


@pytest.mark.asyncio
async def test_harvest_negative_keywords(ads_agent):
    """Test negative keyword harvesting"""
    # Test with sample data
    search_terms = [
        {"search_term": "free customer success", "ctr": 0.1, "conversions": 0, "impressions": 200},
        {"search_term": "customer success platform", "ctr": 3.5, "conversions": 5, "impressions": 150},
        {"search_term": "cheap cs software", "ctr": 0.2, "conversions": 0, "impressions": 300}
    ]
    
    negative_keywords = await ads_agent.harvest_negative_keywords(search_terms)
    
    # Should identify low performers
    assert len(negative_keywords) >= 2
    assert "free customer success" in negative_keywords
    assert "cheap cs software" in negative_keywords
    assert "customer success platform" not in negative_keywords


@pytest.mark.asyncio
async def test_create_campaign(ads_agent):
    """Test campaign creation"""
    result = await ads_agent.create_campaign("brand")
    
    assert result["campaign_type"] == "brand"
    assert result["name"] == "Brand Campaign"
    assert "keywords" in result
    assert len(result["keywords"]) > 0
