"""
Competitor Review Monitoring & Intelligence
Tracks competitor reviews on G2, Capterra, Trustpilot, TrustRadius, Software Advice.
Uses Claude for AI-powered sentiment analysis and opportunity identification.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import asyncio
import logging
import json

from anthropic import Anthropic
from agents.review_scraper import review_scraper
from shared.config import settings
from shared.database import get_supabase

logger = logging.getLogger(__name__)


class CompetitorReviewMonitor:
    """Monitor competitor reviews with AI-powered analysis"""

    COMPETITORS = {
        "gainsight": {
            "name": "Gainsight",
            "category": "Enterprise CS",
            "g2_url": "https://www.g2.com/products/gainsight-cs",
            "capterra_url": "https://www.capterra.com/p/gainsight",
            "trustpilot": "gainsight",
            "trustradius": "gainsight-cs",
            "software_advice": "gainsight"
        },
        "totango": {
            "name": "Totango",
            "category": "Enterprise CS",
            "g2_url": "https://www.g2.com/products/totango",
            "capterra_url": "https://www.capterra.com/p/totango",
            "trustpilot": "totango",
            "trustradius": "totango",
            "software_advice": "totango"
        },
        "churnzero": {
            "name": "ChurnZero",
            "category": "Mid-Market CS",
            "g2_url": "https://www.g2.com/products/churnzero",
            "capterra_url": "https://www.capterra.com/p/churnzero",
            "trustpilot": "churnzero",
            "trustradius": "churnzero",
            "software_advice": "churnzero"
        },
        "planhat": {
            "name": "Planhat",
            "category": "Mid-Market CS",
            "g2_url": "https://www.g2.com/products/planhat",
            "capterra_url": "https://www.capterra.com/p/planhat",
            "trustpilot": "planhat",
            "trustradius": "planhat",
            "software_advice": "planhat"
        },
        "vitally": {
            "name": "Vitally",
            "category": "SMB/Mid-Market CS",
            "g2_url": "https://www.g2.com/products/vitally",
            "capterra_url": "https://www.capterra.com/p/vitally",
            "trustpilot": "vitally.io",
            "trustradius": "vitally",
            "software_advice": "vitally"
        },
        "clientsuccess": {
            "name": "ClientSuccess",
            "category": "Mid-Market CS",
            "g2_url": "https://www.g2.com/products/clientsuccess",
            "capterra_url": "https://www.capterra.com/p/clientsuccess",
            "trustpilot": "clientsuccess",
            "trustradius": "clientsuccess",
            "software_advice": "clientsuccess"
        },
        "custify": {
            "name": "Custify",
            "category": "SMB CS",
            "g2_url": "https://www.g2.com/products/custify",
            "capterra_url": "https://www.capterra.com/p/custify",
            "trustpilot": "custify.com",
            "trustradius": "custify",
            "software_advice": "custify"
        }
    }

    def __init__(self):
        self.sb = None
        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY) if settings.ANTHROPIC_API_KEY else None
        self.model = settings.CLAUDE_MODEL

    def _get_sb(self):
        if not self.sb:
            self.sb = get_supabase()
        return self.sb

    async def monitor_competitor(self, competitor_key: str) -> Dict[str, Any]:
        """
        Monitor a specific competitor's reviews across all platforms

        Args:
            competitor_key: Competitor identifier (e.g., "gainsight")
        """
        if competitor_key not in self.COMPETITORS:
            return {"success": False, "error": f"Unknown competitor: {competitor_key}"}

        competitor = self.COMPETITORS[competitor_key]
        logger.info(f"Monitoring {competitor['name']} reviews...")

        try:
            results = {}

            # Scrape all available platforms for this competitor
            if competitor.get("g2_url"):
                results["g2"] = await review_scraper.scrape_g2_reviews(competitor["g2_url"])
            if competitor.get("capterra_url"):
                results["capterra"] = await review_scraper.scrape_capterra_reviews(competitor["capterra_url"])
            if competitor.get("trustpilot"):
                results["trustpilot"] = await review_scraper.scrape_trustpilot_reviews(competitor["trustpilot"])
            if competitor.get("trustradius"):
                results["trustradius"] = await review_scraper.scrape_trustradius_reviews(competitor["trustradius"])
            if competitor.get("software_advice"):
                results["software_advice"] = await review_scraper.scrape_software_advice_reviews(competitor["software_advice"])

            # Aggregate data
            successful = {k: v for k, v in results.items() if v.get("success")}
            total_reviews = sum(r.get("reviews_scraped", 0) for r in successful.values())

            total_weighted = sum(
                r.get("overall_rating", 0) * r.get("review_count", 0)
                for r in successful.values()
            )
            total_count = sum(r.get("review_count", 1) for r in successful.values())
            avg_rating = total_weighted / total_count if total_count > 0 else 0

            # Collect all reviews for AI analysis
            all_reviews = []
            for platform_result in successful.values():
                all_reviews.extend(platform_result.get("reviews", []))

            # Run AI-powered analysis
            ai_analysis = await self._ai_analyze_competitor_reviews(
                competitor["name"], competitor.get("category", ""), all_reviews
            )

            # Save to database
            sb = self._get_sb()
            sb.table("competitor_reviews").insert({
                "competitor": competitor["name"],
                "competitor_key": competitor_key,
                "category": competitor.get("category", ""),
                "total_reviews_scraped": total_reviews,
                "avg_rating": round(avg_rating, 2),
                "platforms": json.dumps(results, default=str),
                "ai_analysis": json.dumps(ai_analysis, default=str),
                "monitored_at": datetime.utcnow().isoformat()
            }).execute()

            return {
                "success": True,
                "competitor": competitor["name"],
                "category": competitor.get("category", ""),
                "total_reviews": total_reviews,
                "avg_rating": round(avg_rating, 2),
                "platforms_scraped": len(successful),
                "platforms": results,
                "ai_analysis": ai_analysis
            }

        except Exception as e:
            logger.error(f"Failed to monitor competitor {competitor_key}: {e}")
            return {"success": False, "error": str(e)}

    async def monitor_all_competitors(self) -> Dict[str, Any]:
        """Monitor all configured competitors"""
        results = {}

        for competitor_key in self.COMPETITORS.keys():
            result = await self.monitor_competitor(competitor_key)
            results[competitor_key] = result

        # Generate comprehensive competitive analysis
        analysis = await self._generate_competitive_intelligence(results)

        return {
            "success": True,
            "competitors_monitored": len(results),
            "results": results,
            "competitive_intelligence": analysis
        }

    async def _ai_analyze_competitor_reviews(
        self, competitor_name: str, category: str, reviews: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Use Claude to deeply analyze competitor reviews"""
        if not self.client or not reviews:
            return self._fallback_analysis(reviews)

        review_texts = []
        for r in reviews[:15]:
            entry = f"Rating: {r.get('rating', 'N/A')}/5"
            if r.get('title'):
                entry += f"\nTitle: {r['title']}"
            if r.get('text'):
                entry += f"\nReview: {r['text'][:300]}"
            if r.get('pros'):
                entry += f"\nPros: {r['pros'][:200]}"
            if r.get('cons'):
                entry += f"\nCons: {r['cons'][:200]}"
            review_texts.append(entry)

        prompt = f"""Analyze these reviews of {competitor_name} ({category} platform) and provide competitive intelligence for Successifier (an AI-native Customer Success platform).

Reviews:
{chr(10).join(f'---Review {i+1}---{chr(10)}{text}' for i, text in enumerate(review_texts))}

Return a JSON analysis:
{{
    "sentiment_summary": {{
        "overall": "positive|mixed|negative",
        "avg_satisfaction": 1-10,
        "key_emotion": "frustrated|satisfied|impressed|disappointed|neutral"
    }},
    "strengths": [
        {{"feature": "...", "frequency": "high|medium|low", "detail": "..."}}
    ],
    "weaknesses": [
        {{"pain_point": "...", "severity": "critical|high|medium|low", "detail": "...", "successifier_opportunity": "..."}}
    ],
    "pricing_sentiment": {{
        "perception": "expensive|fair|good_value",
        "complaints": ["..."],
        "willingness_to_switch": "high|medium|low"
    }},
    "switching_signals": [
        {{"signal": "...", "frequency": "high|medium|low", "target_messaging": "..."}}
    ],
    "ideal_prospect_profile": {{
        "company_size": "...",
        "industry": "...",
        "pain_points": ["..."],
        "why_they_would_switch": "..."
    }},
    "content_opportunities": [
        {{"topic": "...", "format": "blog|comparison|case_study|landing_page", "angle": "..."}}
    ],
    "ad_copy_hooks": ["..."]
}}"""

        try:
            def _call():
                return self.client.messages.create(
                    model=self.model,
                    max_tokens=2048,
                    system="You are a competitive intelligence analyst for Successifier, an AI-native Customer Success Platform. Analyze competitor reviews to find actionable opportunities. Always return valid JSON.",
                    messages=[{"role": "user", "content": prompt}]
                )

            response = await asyncio.to_thread(_call)
            text = response.content[0].text

            # Parse JSON from response
            json_match = re.search(r'\{[\s\S]*\}', text)
            if json_match:
                return json.loads(json_match.group())
        except Exception as e:
            logger.warning(f"AI analysis failed for {competitor_name}: {e}")

        return self._fallback_analysis(reviews)

    def _fallback_analysis(self, reviews: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Keyword-based fallback analysis when AI is unavailable"""
        complaints = self._extract_complaints(reviews)
        praises = self._extract_praises(reviews)

        return {
            "sentiment_summary": {
                "overall": "mixed",
                "avg_satisfaction": 5,
                "key_emotion": "neutral"
            },
            "strengths": [{"feature": p, "frequency": "medium", "detail": p} for p in praises[:5]],
            "weaknesses": [{"pain_point": c, "severity": "medium", "detail": c, "successifier_opportunity": ""} for c in complaints[:5]],
            "pricing_sentiment": {"perception": "unknown", "complaints": [], "willingness_to_switch": "unknown"},
            "switching_signals": [],
            "ideal_prospect_profile": {},
            "content_opportunities": [],
            "ad_copy_hooks": []
        }

    async def _generate_competitive_intelligence(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """Generate comprehensive competitive intelligence from all competitor data"""

        # Collect all analyses
        competitor_summaries = {}
        for key, result in results.items():
            if result.get("success"):
                competitor_summaries[self.COMPETITORS[key]["name"]] = {
                    "rating": result.get("avg_rating", 0),
                    "category": self.COMPETITORS[key].get("category", ""),
                    "total_reviews": result.get("total_reviews", 0),
                    "ai_analysis": result.get("ai_analysis", {})
                }

        if not self.client or not competitor_summaries:
            return self._fallback_competitive_intelligence(competitor_summaries)

        prompt = f"""Based on this competitive intelligence data across {len(competitor_summaries)} Customer Success platforms, generate a strategic market analysis for Successifier.

Competitor Data:
{json.dumps(competitor_summaries, indent=2, default=str)}

Return JSON:
{{
    "market_overview": {{
        "avg_market_rating": 0.0,
        "market_sentiment": "...",
        "key_trends": ["..."]
    }},
    "competitor_ranking": [
        {{"name": "...", "rating": 0.0, "primary_strength": "...", "primary_weakness": "...", "threat_level": "high|medium|low"}}
    ],
    "market_gaps": [
        {{"gap": "...", "affected_competitors": ["..."], "opportunity_size": "large|medium|small", "recommended_action": "..."}}
    ],
    "positioning_strategy": {{
        "primary_differentiator": "...",
        "messaging_themes": ["..."],
        "target_competitor_for_displacement": "...",
        "displacement_strategy": "..."
    }},
    "prospect_signals": {{
        "high_intent_keywords": ["..."],
        "competitor_pain_indicators": ["..."],
        "best_timing_for_outreach": "...",
        "recommended_outreach_channels": ["..."]
    }},
    "content_battlecards": [
        {{"competitor": "...", "their_claim": "...", "our_counter": "...", "proof_point": "..."}}
    ]
}}"""

        try:
            def _call():
                return self.client.messages.create(
                    model=self.model,
                    max_tokens=3000,
                    system="You are a B2B SaaS competitive intelligence strategist. Provide actionable market analysis. Always return valid JSON.",
                    messages=[{"role": "user", "content": prompt}]
                )

            response = await asyncio.to_thread(_call)
            text = response.content[0].text
            json_match = re.search(r'\{[\s\S]*\}', text)
            if json_match:
                return json.loads(json_match.group())
        except Exception as e:
            logger.warning(f"AI competitive intelligence failed: {e}")

        return self._fallback_competitive_intelligence(competitor_summaries)

    def _fallback_competitive_intelligence(self, summaries: Dict[str, Any]) -> Dict[str, Any]:
        """Fallback competitive intelligence without AI"""
        ratings = {name: data.get("rating", 0) for name, data in summaries.items()}

        return {
            "market_overview": {
                "avg_market_rating": sum(ratings.values()) / len(ratings) if ratings else 0,
                "market_sentiment": "mixed",
                "key_trends": []
            },
            "competitor_ranking": [
                {"name": name, "rating": rating, "threat_level": "medium"}
                for name, rating in sorted(ratings.items(), key=lambda x: x[1], reverse=True)
            ],
            "market_gaps": [],
            "positioning_strategy": {},
            "prospect_signals": {},
            "content_battlecards": []
        }

    def _extract_complaints(self, reviews: List[Dict[str, Any]]) -> List[str]:
        """Extract common complaints from reviews"""
        complaint_keywords = [
            "expensive", "costly", "price", "pricing",
            "difficult", "hard", "complex", "complicated", "steep learning",
            "slow", "laggy", "performance", "loading",
            "support", "customer service", "help", "response time",
            "bug", "error", "crash", "issue", "broken",
            "limited", "missing", "lacking", "no integration",
            "clunky", "outdated", "confusing", "overwhelming"
        ]

        complaints = []
        for review in reviews:
            if review.get("rating", 5) <= 3:
                text = (review.get("text", "") + " " + review.get("cons", "")).lower()
                for keyword in complaint_keywords:
                    if keyword in text:
                        sentences = text.split('.')
                        for sentence in sentences:
                            if keyword in sentence and len(sentence.strip()) > 10:
                                complaints.append(sentence.strip())
                                break

        return list(set(complaints))[:10]

    def _extract_praises(self, reviews: List[Dict[str, Any]]) -> List[str]:
        """Extract common praises from reviews"""
        praise_keywords = [
            "great", "excellent", "amazing", "fantastic", "love",
            "easy", "simple", "intuitive", "user-friendly",
            "helpful", "support", "responsive", "quick",
            "feature", "functionality", "powerful",
            "value", "worth", "affordable", "roi"
        ]

        praises = []
        for review in reviews:
            if review.get("rating", 3) >= 4:
                text = (review.get("text", "") + " " + review.get("pros", "")).lower()
                for keyword in praise_keywords:
                    if keyword in text:
                        sentences = text.split('.')
                        for sentence in sentences:
                            if keyword in sentence and len(sentence.strip()) > 10:
                                praises.append(sentence.strip())
                                break

        return list(set(praises))[:10]

    async def get_competitor_history(self, competitor_key: str, days: int = 90) -> Dict[str, Any]:
        """Get historical competitor review data for trend analysis"""
        try:
            sb = self._get_sb()
            cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
            result = sb.table("competitor_reviews").select("*").eq(
                "competitor_key", competitor_key
            ).gte("monitored_at", cutoff).order("monitored_at", desc=True).execute()

            history = result.data or []
            return {
                "success": True,
                "competitor": competitor_key,
                "data_points": len(history),
                "history": history
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_competitive_snapshot(self) -> Dict[str, Any]:
        """Get latest snapshot of all competitor ratings and key metrics"""
        try:
            sb = self._get_sb()
            snapshot = {}

            for key, comp in self.COMPETITORS.items():
                try:
                    result = sb.table("competitor_reviews").select(
                        "avg_rating,total_reviews_scraped,ai_analysis,monitored_at"
                    ).eq("competitor_key", key).order("monitored_at", desc=True).limit(1).execute()

                    if result.data:
                        latest = result.data[0]
                        ai = json.loads(latest.get("ai_analysis", "{}")) if isinstance(latest.get("ai_analysis"), str) else latest.get("ai_analysis", {})
                        snapshot[key] = {
                            "name": comp["name"],
                            "category": comp.get("category", ""),
                            "avg_rating": latest.get("avg_rating", 0),
                            "total_reviews": latest.get("total_reviews_scraped", 0),
                            "last_monitored": latest.get("monitored_at"),
                            "sentiment": ai.get("sentiment_summary", {}).get("overall", "unknown"),
                            "top_weakness": ai.get("weaknesses", [{}])[0].get("pain_point", "") if ai.get("weaknesses") else "",
                            "threat_level": "medium"
                        }
                    else:
                        snapshot[key] = {
                            "name": comp["name"],
                            "category": comp.get("category", ""),
                            "avg_rating": 0,
                            "total_reviews": 0,
                            "last_monitored": None,
                            "sentiment": "unknown",
                            "top_weakness": "",
                            "threat_level": "unknown"
                        }
                except Exception:
                    snapshot[key] = {"name": comp["name"], "avg_rating": 0, "last_monitored": None}

            return {"success": True, "snapshot": snapshot}

        except Exception as e:
            return {"success": False, "error": str(e)}


# Needed for regex in _ai_analyze_competitor_reviews
import re

# Global instance
competitor_monitor = CompetitorReviewMonitor()
