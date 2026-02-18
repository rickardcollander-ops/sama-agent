"""
Social Agent /analyze endpoint with OODA loop implementation
"""

from typing import Dict, Any
from shared.ooda_templates import run_agent_ooda_cycle, create_analysis_structure, add_pattern, add_anomaly, create_action
from agents.social import social_agent, is_twitter_configured
from datetime import datetime, timedelta


async def run_social_analysis_with_ooda() -> Dict[str, Any]:
    """Run Social analysis using OODA loop"""
    
    async def observe():
        """OBSERVE: Fetch social media data"""
        observations = {}
        
        observations["twitter_configured"] = is_twitter_configured()
        
        # Fetch mentions if Twitter is configured
        if observations["twitter_configured"]:
            try:
                observations["mentions"] = await social_agent.get_mentions(max_results=20)
            except Exception:
                observations["mentions"] = []
            
            try:
                observations["competitor_tweets"] = await social_agent.search_competitor_mentions(max_results=10)
            except Exception:
                observations["competitor_tweets"] = []
        else:
            observations["mentions"] = []
            observations["competitor_tweets"] = []
        
        observations["content_calendar"] = social_agent.CONTENT_CALENDAR
        observations["engagement_rules"] = social_agent.ENGAGEMENT_RULES
        
        return observations
    
    async def orient(observations):
        """ORIENT: Analyze social engagement opportunities"""
        analysis = create_analysis_structure()
        
        mentions = observations.get("mentions", [])
        competitor_tweets = observations.get("competitor_tweets", [])
        twitter_configured = observations.get("twitter_configured", False)
        
        # Analyze mention engagement opportunities
        high_value_mentions = [m for m in mentions if m.get("user", {}).get("followers_count", 0) > 500]
        if high_value_mentions:
            add_pattern(analysis, "high_value_mentions", {"count": len(high_value_mentions)})
        
        # Analyze competitor opportunities
        if competitor_tweets:
            add_pattern(analysis, "competitor_opportunities", {"count": len(competitor_tweets)})
        
        # Check if Twitter is configured
        if not twitter_configured:
            add_anomaly(analysis, "twitter_not_configured", "critical", {"message": "Cannot post or monitor mentions"})
        
        # Analyze content calendar coverage
        today = datetime.now()
        upcoming_days = [(today + timedelta(days=i)).strftime("%A").lower() for i in range(7)]
        calendar_coverage = sum(1 for day in upcoming_days if day in observations.get("content_calendar", {}))
        if calendar_coverage < 7:
            add_pattern(analysis, "content_calendar_gaps", {"days_covered": calendar_coverage, "days_total": 7})
        
        return analysis
    
    async def decide(analysis, observations):
        """DECIDE: Generate social media actions"""
        actions = []
        mentions = observations.get("mentions", [])
        competitor_tweets = observations.get("competitor_tweets", [])
        content_calendar = observations.get("content_calendar", {})
        twitter_configured = observations.get("twitter_configured", False)
        
        # Generate content calendar actions
        today = datetime.now()
        for day_offset in range(7):
            post_date = today + timedelta(days=day_offset)
            day_name = post_date.strftime("%A").lower()
            
            if day_name in content_calendar:
                day_config = content_calendar[day_name]
                actions.append(create_action(
                    f"social-calendar-{day_name}-{post_date.strftime('%m%d')}",
                    "generate_post",
                    "high" if day_offset < 2 else "medium",
                    f"{day_config['theme']} ({post_date.strftime('%A %b %d')})",
                    f"Format: {day_config['format']}. Example: {day_config['example']}",
                    f"Generate and schedule a {day_config['format'].lower()} about {day_config['theme'].lower()}",
                    {"type": "engagement", "expected_impressions": 500},
                    topic=day_config["example"],
                    style="educational",
                    is_thread=day_config["format"] == "Educational thread",
                    scheduled_date=post_date.strftime("%Y-%m-%d")
                ))
        
        # Generate mention reply actions
        for mention in mentions:
            user = mention.get("user", {})
            followers = user.get("followers_count", 0)
            username = user.get("username", "unknown")
            text = mention.get("text", "")
            priority = "high" if followers > 500 else "medium" if followers > 100 else "low"
            
            actions.append(create_action(
                f"social-reply-{mention.get('id', '')}",
                "reply",
                priority,
                f"Reply to @{username} ({followers} followers)",
                text[:200],
                "Generate and post a reply to this mention",
                {"type": "engagement", "expected_reach": followers},
                original_tweet=text,
                tweet_id=mention.get("id", ""),
                username=username
            ))
        
        # Competitor opportunity actions
        for tweet in competitor_tweets:
            user = tweet.get("user", {})
            username = user.get("username", "unknown")
            text = tweet.get("text", "")
            
            actions.append(create_action(
                f"social-competitor-{tweet.get('id', '')}",
                "competitor_engage",
                "medium",
                f"Competitor opportunity: @{username}",
                text[:200],
                "Generate a helpful reply to this competitor-related tweet",
                {"type": "lead_generation", "expected_engagement": 10},
                original_tweet=text,
                tweet_id=tweet.get("id", ""),
                username=username
            ))
        
        # Thread creation action
        actions.append(create_action(
            "social-thread-weekly",
            "generate_thread",
            "high",
            "Create weekly educational thread",
            "Threads get 3-5x more engagement. Create a value-packed thread on a trending CS topic.",
            "Generate a 3-5 tweet thread about customer success best practices",
            {"type": "engagement", "expected_impressions": 2000},
            topic="customer success best practices for reducing churn",
            style="educational"
        ))
        
        # Twitter configuration action if needed
        if not twitter_configured:
            actions.append(create_action(
                "social-config-twitter",
                "config",
                "critical",
                "Configure Twitter API credentials",
                "Twitter API is not configured. Set TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET in Railway.",
                "Add Twitter API credentials to enable real posting and mention monitoring",
                {"type": "infrastructure", "enables": "all_social_features"}
            ))
        
        # Sort by priority
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        actions.sort(key=lambda a: priority_order.get(a.get("priority", "low"), 3))
        
        return actions
    
    return await run_agent_ooda_cycle("social", observe, orient, decide)
