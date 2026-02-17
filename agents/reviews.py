"""
Review Agent - Review Management Across Platforms
Manages reviews on G2, Capterra, Trustpilot, and other platforms
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from anthropic import Anthropic
import httpx

from shared.config import settings
from shared.database import AsyncSessionLocal
from shared.event_bus import event_bus
from .brand_voice import brand_voice

logger = logging.getLogger(__name__)


class ReviewAgent:
    """
    Review Agent responsible for:
    - Monitoring reviews across platforms
    - Generating review responses
    - Requesting reviews from happy customers
    - Analyzing review sentiment
    - Competitor review analysis
    """
    
    # Platforms to monitor
    PLATFORMS = {
        "g2": {
            "name": "G2",
            "url": "https://www.g2.com/products/successifier",
            "priority": "high",
            "target_reviews": 50,
            "current_rating": 4.8
        },
        "capterra": {
            "name": "Capterra",
            "url": "https://www.capterra.com/p/successifier",
            "priority": "high",
            "target_reviews": 30,
            "current_rating": 4.7
        },
        "trustpilot": {
            "name": "Trustpilot",
            "url": "https://www.trustpilot.com/review/successifier.com",
            "priority": "medium",
            "target_reviews": 20,
            "current_rating": 4.9
        },
        "product_hunt": {
            "name": "Product Hunt",
            "url": "https://www.producthunt.com/posts/successifier",
            "priority": "low",
            "target_reviews": 10,
            "current_rating": 5.0
        }
    }
    
    # Response templates by sentiment
    RESPONSE_STRATEGY = {
        "positive": {
            "tone": "Grateful and authentic",
            "include": ["Thank them", "Highlight specific feature they mentioned", "Invite to community"],
            "avoid": ["Generic 'thanks'", "Over-the-top enthusiasm"]
        },
        "neutral": {
            "tone": "Helpful and solution-oriented",
            "include": ["Acknowledge feedback", "Offer to help", "Share relevant resources"],
            "avoid": ["Defensive language", "Dismissing concerns"]
        },
        "negative": {
            "tone": "Empathetic and action-oriented",
            "include": ["Apologize sincerely", "Take ownership", "Offer direct contact", "Explain fix timeline"],
            "avoid": ["Excuses", "Blaming user", "Generic apologies"]
        }
    }
    
    # Review request triggers
    REVIEW_REQUEST_TRIGGERS = {
        "nps_promoter": {
            "condition": "NPS score 9-10",
            "timing": "Within 24 hours of NPS response",
            "platform": "G2 or Capterra"
        },
        "milestone_reached": {
            "condition": "Customer reaches 90-day milestone with high health score",
            "timing": "Day 91",
            "platform": "G2"
        },
        "feature_adoption": {
            "condition": "Customer actively uses 3+ features",
            "timing": "After 60 days",
            "platform": "Capterra"
        },
        "support_resolution": {
            "condition": "Support ticket resolved with positive CSAT",
            "timing": "Within 48 hours",
            "platform": "Trustpilot"
        }
    }
    
    def __init__(self):
        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        self.model = "claude-sonnet-4-20250514"
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.brand_voice = brand_voice
    
    async def generate_review_response(
        self,
        review: Dict[str, Any],
        platform: str
    ) -> Dict[str, Any]:
        """
        Generate response to a review
        
        Args:
            review: Review data (text, rating, reviewer)
            platform: Platform name (g2, capterra, etc.)
        
        Returns:
            Generated response
        """
        logger.info(f"💬 Generating response to {platform} review")
        
        review_text = review.get("text", "")
        rating = review.get("rating", 5)
        reviewer_name = review.get("reviewer", "Customer")
        
        # Determine sentiment
        if rating >= 4:
            sentiment = "positive"
        elif rating >= 3:
            sentiment = "neutral"
        else:
            sentiment = "negative"
        
        strategy = self.RESPONSE_STRATEGY[sentiment]
        
        system_prompt = f"""You are responding to a customer review for Successifier.

Tone: {strategy['tone']}

Include:
{chr(10).join('- ' + item for item in strategy['include'])}

Avoid:
{chr(10).join('- ' + item for item in strategy['avoid'])}

Keep response under 200 words. Be authentic and personal."""
        
        user_prompt = f"""Generate a response to this {platform.upper()} review:

Rating: {rating}/5
Reviewer: {reviewer_name}
Review: "{review_text}"

Requirements:
- Address specific points they mentioned
- Be genuine and personal (not templated)
- Include next steps or resources if helpful
- Sign off as the Successifier team
"""
        
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        
        response_text = response.content[0].text.strip()
        
        logger.info(f"✅ Response generated for {sentiment} review")
        
        return {
            "platform": platform,
            "review_id": review.get("id"),
            "sentiment": sentiment,
            "response": response_text,
            "status": "draft"
        }
    
    async def generate_review_request(
        self,
        customer: Dict[str, Any],
        trigger: str,
        platform: str = "g2"
    ) -> Dict[str, Any]:
        """
        Generate personalized review request
        
        Args:
            customer: Customer data
            trigger: What triggered the request
            platform: Target platform
        
        Returns:
            Review request email/message
        """
        logger.info(f"📧 Generating review request for {customer.get('name')}")
        
        system_prompt = """You are writing a review request email for Successifier.

Tone: Personal, grateful, not pushy
Goal: Make it easy and natural for happy customers to leave a review

DO:
- Personalize based on their usage
- Make it feel like a genuine ask, not automated
- Provide direct link
- Keep it short (under 150 words)

DON'T:
- Be overly formal
- Guilt trip
- Offer incentives (against review platform policies)
"""
        
        customer_name = customer.get("name", "there")
        company = customer.get("company", "your team")
        
        user_prompt = f"""Write a review request email for:

Customer: {customer_name}
Company: {company}
Trigger: {trigger}
Platform: {platform.upper()}

Include:
- Personal greeting
- Mention their success/milestone
- Simple ask to share experience
- Direct link to review page
- Thank them

Format as email (subject + body).
"""
        
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        
        content = response.content[0].text.strip()
        
        # Split into subject and body
        lines = content.split('\n')
        subject = lines[0].replace("Subject:", "").strip()
        body = '\n'.join(lines[2:]).strip()
        
        logger.info(f"✅ Review request generated for {platform}")
        
        return {
            "customer_id": customer.get("id"),
            "platform": platform,
            "trigger": trigger,
            "subject": subject,
            "body": body,
            "review_url": self.PLATFORMS[platform]["url"],
            "status": "draft"
        }
    
    async def analyze_reviews(
        self,
        reviews: List[Dict[str, Any]],
        platform: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Analyze review sentiment and extract insights
        
        Args:
            reviews: List of reviews
            platform: Optional platform filter
        
        Returns:
            Analysis with insights
        """
        logger.info(f"📊 Analyzing {len(reviews)} reviews")
        
        if not reviews:
            return {"total": 0, "insights": []}
        
        # Calculate metrics
        total = len(reviews)
        avg_rating = sum(r.get("rating", 0) for r in reviews) / total
        
        # Sentiment distribution
        positive = sum(1 for r in reviews if r.get("rating", 0) >= 4)
        neutral = sum(1 for r in reviews if r.get("rating", 0) == 3)
        negative = sum(1 for r in reviews if r.get("rating", 0) < 3)
        
        # Extract common themes using Claude
        review_texts = [r.get("text", "") for r in reviews[:20]]  # Sample
        
        system_prompt = "You are analyzing customer reviews to extract themes and insights."
        
        user_prompt = f"""Analyze these customer reviews and identify:

1. Top 3 most mentioned positive features
2. Top 3 most mentioned pain points or requests
3. Common use cases
4. Competitor comparisons mentioned

Reviews:
{chr(10).join('- "' + text + '"' for text in review_texts[:10])}

Format as JSON:
{{
  "positive_features": ["feature 1", "feature 2", "feature 3"],
  "pain_points": ["pain 1", "pain 2", "pain 3"],
  "use_cases": ["use case 1", "use case 2"],
  "competitor_mentions": ["competitor 1", "competitor 2"]
}}
"""
        
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}]
            )
            
            import json
            themes = json.loads(response.content[0].text)
        except:
            themes = {
                "positive_features": [],
                "pain_points": [],
                "use_cases": [],
                "competitor_mentions": []
            }
        
        logger.info(f"✅ Analysis complete: {avg_rating:.1f}/5 average rating")
        
        return {
            "total_reviews": total,
            "average_rating": round(avg_rating, 2),
            "sentiment_distribution": {
                "positive": positive,
                "neutral": neutral,
                "negative": negative
            },
            "themes": themes,
            "platform": platform
        }
    
    async def monitor_competitor_reviews(
        self,
        competitor: str,
        platform: str = "g2"
    ) -> Dict[str, Any]:
        """
        Analyze competitor reviews for insights
        
        Args:
            competitor: Competitor name (gainsight, totango, etc.)
            platform: Platform to analyze
        
        Returns:
            Competitor review analysis
        """
        logger.info(f"🔍 Monitoring {competitor} reviews on {platform}")
        
        # This would fetch real competitor reviews from APIs
        # For now, return placeholder analysis
        
        analysis = {
            "competitor": competitor,
            "platform": platform,
            "average_rating": 0.0,
            "total_reviews": 0,
            "common_complaints": [],
            "opportunities": [],
            "pricing_mentions": []
        }
        
        return analysis
    
    async def identify_review_opportunities(
        self,
        customers: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Identify customers who should be asked for reviews
        
        Args:
            customers: List of customer data
        
        Returns:
            List of review opportunities
        """
        logger.info(f"🎯 Identifying review opportunities from {len(customers)} customers")
        
        opportunities = []
        
        for customer in customers:
            # Check triggers
            nps_score = customer.get("nps_score", 0)
            health_score = customer.get("health_score", 0)
            days_active = customer.get("days_active", 0)
            
            # NPS Promoter
            if nps_score >= 9:
                opportunities.append({
                    "customer_id": customer.get("id"),
                    "customer_name": customer.get("name"),
                    "trigger": "nps_promoter",
                    "platform": "g2",
                    "priority": "high",
                    "reason": f"NPS score: {nps_score}"
                })
            
            # Milestone reached
            elif days_active >= 90 and health_score >= 80:
                opportunities.append({
                    "customer_id": customer.get("id"),
                    "customer_name": customer.get("name"),
                    "trigger": "milestone_reached",
                    "platform": "g2",
                    "priority": "medium",
                    "reason": f"90+ days active with {health_score}% health score"
                })
        
        # Sort by priority
        priority_order = {"high": 0, "medium": 1, "low": 2}
        opportunities.sort(key=lambda x: priority_order[x["priority"]])
        
        logger.info(f"✅ Found {len(opportunities)} review opportunities")
        
        return opportunities


# Global review agent instance
review_agent = ReviewAgent()
