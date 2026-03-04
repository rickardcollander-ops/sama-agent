"""
Analytics Agent - Cross-Channel Marketing Analytics
Provides unified reporting and attribution across all marketing channels.
Aggregates real data from SEO, Ads, Reviews, and Content agents.
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from anthropic import Anthropic
import httpx

from shared.config import settings
from shared.database import get_supabase
from shared.google_auth import is_gsc_configured, is_ads_configured
from .models import (
    CONTENT_PIECES_TABLE, DAILY_METRICS_TABLE, KEYWORDS_TABLE, REVIEWS_TABLE,
)

logger = logging.getLogger(__name__)


class AnalyticsAgent:
    """
    Analytics Agent responsible for:
    - Cross-channel attribution
    - Marketing performance dashboards
    - ROI calculation
    - Trend analysis
    - Automated insights
    - Weekly/monthly reports
    """

    # Metrics tracked per channel
    CHANNEL_METRICS = {
        "seo": {
            "metrics": ["organic_traffic", "keyword_rankings", "impressions", "clicks", "ctr", "conversions"],
            "attribution_window": 30
        },
        "content": {
            "metrics": ["blog_views", "time_on_page", "social_shares", "backlinks", "conversions"],
            "attribution_window": 90
        },
        "google_ads": {
            "metrics": ["impressions", "clicks", "ctr", "conversions", "cost", "cpa", "roas"],
            "attribution_window": 7
        },
        "social": {
            "metrics": ["impressions", "engagements", "followers", "clicks", "conversions"],
            "attribution_window": 14
        },
        "reviews": {
            "metrics": ["total_reviews", "average_rating", "response_rate", "sentiment_score"],
            "attribution_window": 60
        }
    }

    # Attribution models
    ATTRIBUTION_MODELS = {
        "first_touch": "Credit to first interaction",
        "last_touch": "Credit to last interaction before conversion",
        "linear": "Equal credit to all touchpoints",
        "time_decay": "More credit to recent touchpoints",
        "position_based": "40% first, 40% last, 20% middle"
    }

    # Report templates
    REPORT_TEMPLATES = {
        "weekly_summary": {
            "frequency": "weekly",
            "sections": ["overview", "top_performers", "alerts", "recommendations"],
            "recipients": ["marketing_team"]
        },
        "monthly_deep_dive": {
            "frequency": "monthly",
            "sections": ["overview", "channel_breakdown", "attribution", "roi", "trends", "action_items"],
            "recipients": ["leadership", "marketing_team"]
        },
        "quarterly_review": {
            "frequency": "quarterly",
            "sections": ["executive_summary", "goal_progress", "channel_performance", "roi", "strategic_recommendations"],
            "recipients": ["leadership"]
        }
    }

    def __init__(self):
        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        self.model = "claude-sonnet-4-20250514"
        self.http_client = httpx.AsyncClient(timeout=30.0)

    # ── Data fetchers (one per channel) ───────────────────────────────

    async def _fetch_seo_data(self, date_range: int = 28) -> Dict[str, Any]:
        """Fetch real SEO data from the SEO agent's GSC integration."""
        channel_data = {
            "configured": is_gsc_configured(),
            "total_clicks": 0,
            "total_impressions": 0,
            "avg_ctr": 0.0,
            "avg_position": 0.0,
            "date_range_days": date_range,
            "keywords_tracked": 0,
            "top_3_keywords": 0,
            "top_10_keywords": 0,
        }

        if not is_gsc_configured():
            channel_data["status"] = "not_configured"
            return channel_data

        try:
            from agents.seo import seo_agent

            # Get aggregate GSC summary (clicks, impressions, CTR, position)
            gsc_data = await seo_agent._fetch_gsc_data()
            if gsc_data.get("status") == "ok":
                channel_data["total_clicks"] = gsc_data.get("total_clicks", 0)
                channel_data["total_impressions"] = gsc_data.get("total_impressions", 0)
                channel_data["avg_ctr"] = gsc_data.get("avg_ctr", 0.0)
                channel_data["avg_position"] = gsc_data.get("avg_position", 0.0)
                channel_data["status"] = "ok"
            else:
                channel_data["status"] = gsc_data.get("status", "error")
                channel_data["message"] = gsc_data.get("message", "")

            # Get keyword ranking summary from the database
            try:
                keywords = await seo_agent.get_keywords()
                channel_data["keywords_tracked"] = len(keywords)
                channel_data["top_3_keywords"] = sum(
                    1 for k in keywords
                    if k.get("current_position") and k["current_position"] <= 3
                )
                channel_data["top_10_keywords"] = sum(
                    1 for k in keywords
                    if k.get("current_position") and k["current_position"] <= 10
                )
            except Exception as e:
                logger.warning(f"Could not fetch keyword summary: {e}")

        except Exception as e:
            logger.warning(f"SEO data fetch failed: {e}")
            channel_data["status"] = "error"
            channel_data["error"] = str(e)

        return channel_data

    async def _fetch_ads_data(self, date_range: int = 30) -> Dict[str, Any]:
        """Fetch real Google Ads data from the Ads agent."""
        channel_data = {
            "configured": is_ads_configured(),
            "total_spend": 0.0,
            "total_clicks": 0,
            "total_impressions": 0,
            "total_conversions": 0.0,
            "avg_ctr": 0.0,
            "avg_cpa": 0.0,
            "roas": 0.0,
            "campaign_count": 0,
            "date_range_days": date_range,
        }

        if not is_ads_configured():
            channel_data["status"] = "not_configured"
            return channel_data

        try:
            from agents.ads import ads_agent

            campaigns = await ads_agent.get_campaign_performance(date_range=date_range)
            if not campaigns:
                channel_data["status"] = "no_data"
                return channel_data

            total_impressions = sum(c["impressions"] for c in campaigns)
            total_clicks = sum(c["clicks"] for c in campaigns)
            total_conversions = sum(c["conversions"] for c in campaigns)
            total_cost = sum(c["cost"] for c in campaigns)
            total_conv_value = sum(c.get("roas", 0) * c.get("cost", 0) for c in campaigns)

            channel_data.update({
                "status": "ok",
                "total_spend": round(total_cost, 2),
                "total_clicks": total_clicks,
                "total_impressions": total_impressions,
                "total_conversions": round(total_conversions, 2),
                "avg_ctr": round(total_clicks / total_impressions * 100, 2) if total_impressions > 0 else 0.0,
                "avg_cpa": round(total_cost / total_conversions, 2) if total_conversions > 0 else 0.0,
                "roas": round(total_conv_value / total_cost, 2) if total_cost > 0 else 0.0,
                "campaign_count": len(campaigns),
                "campaigns": campaigns,
            })

        except Exception as e:
            logger.warning(f"Ads data fetch failed: {e}")
            channel_data["status"] = "error"
            channel_data["error"] = str(e)

        return channel_data

    async def _fetch_reviews_data(self) -> Dict[str, Any]:
        """Fetch review data from the Supabase reviews table."""
        channel_data = {
            "total_reviews": 0,
            "avg_rating": 0.0,
            "response_rate": 0.0,
            "reviews_last_30d": 0,
            "rating_distribution": {},
        }

        try:
            sb = get_supabase()
            result = sb.table("reviews").select("*").execute()
            reviews = result.data or []

            if not reviews:
                channel_data["status"] = "no_data"
                return channel_data

            channel_data["total_reviews"] = len(reviews)

            # Average rating (exclude null ratings)
            rated = [r for r in reviews if r.get("rating") is not None]
            if rated:
                channel_data["avg_rating"] = round(
                    sum(r["rating"] for r in rated) / len(rated), 2
                )

            # Rating distribution
            dist = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
            for r in rated:
                bucket = min(5, max(1, int(r["rating"])))
                dist[bucket] += 1
            channel_data["rating_distribution"] = dist

            # Response rate: reviews that have a non-empty response
            responded = sum(
                1 for r in reviews
                if r.get("response") or r.get("responded_at")
            )
            channel_data["response_rate"] = round(
                responded / len(reviews) * 100, 2
            ) if reviews else 0.0

            # Reviews in the last 30 days
            cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat()
            channel_data["reviews_last_30d"] = sum(
                1 for r in reviews
                if (r.get("created_at") or "") >= cutoff
            )

            channel_data["status"] = "ok"

        except Exception as e:
            logger.warning(f"Reviews data fetch failed: {e}")
            channel_data["status"] = "error"
            channel_data["error"] = str(e)

        return channel_data

    async def _fetch_content_data(self) -> Dict[str, Any]:
        """Fetch content data from the Supabase content_pieces table."""
        channel_data = {
            "total_pieces": 0,
            "published": 0,
            "draft": 0,
            "total_impressions": 0,
            "total_clicks": 0,
            "pieces_last_30d": 0,
        }

        try:
            sb = get_supabase()
            result = sb.table(CONTENT_PIECES_TABLE).select("*").execute()
            pieces = result.data or []

            if not pieces:
                channel_data["status"] = "no_data"
                return channel_data

            channel_data["total_pieces"] = len(pieces)
            channel_data["published"] = sum(
                1 for p in pieces if (p.get("status") or "").lower() == "published"
            )
            channel_data["draft"] = sum(
                1 for p in pieces if (p.get("status") or "").lower() == "draft"
            )
            channel_data["total_impressions"] = sum(
                p.get("impressions_30d", 0) or 0 for p in pieces
            )
            channel_data["total_clicks"] = sum(
                p.get("clicks_30d", 0) or 0 for p in pieces
            )

            # Content created in last 30 days
            cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat()
            channel_data["pieces_last_30d"] = sum(
                1 for p in pieces
                if (p.get("created_at") or "") >= cutoff
            )

            channel_data["status"] = "ok"

        except Exception as e:
            logger.warning(f"Content data fetch failed: {e}")
            channel_data["status"] = "error"
            channel_data["error"] = str(e)

        return channel_data

    # ── Core report generator ─────────────────────────────────────────

    async def generate_weekly_report(
        self,
        date_range: int = 7
    ) -> Dict[str, Any]:
        """
        Generate weekly marketing performance report by aggregating real data
        from all configured channels: SEO, Google Ads, Reviews, and Content.

        Args:
            date_range: Number of days to analyze

        Returns:
            Weekly report with real data and AI-generated insights
        """
        logger.info(f"Generating weekly report ({date_range} days)")

        # Fetch all channel data in parallel
        seo_task = self._fetch_seo_data(date_range=max(date_range, 28))
        ads_task = self._fetch_ads_data(date_range=date_range)
        reviews_task = self._fetch_reviews_data()
        content_task = self._fetch_content_data()

        seo_data, ads_data, reviews_data, content_data = await asyncio.gather(
            seo_task, ads_task, reviews_task, content_task,
            return_exceptions=True,
        )

        # Convert exceptions to error dicts so the report is always valid
        if isinstance(seo_data, Exception):
            logger.warning(f"SEO data failed: {seo_data}")
            seo_data = {"status": "error", "error": str(seo_data)}
        if isinstance(ads_data, Exception):
            logger.warning(f"Ads data failed: {ads_data}")
            ads_data = {"status": "error", "error": str(ads_data)}
        if isinstance(reviews_data, Exception):
            logger.warning(f"Reviews data failed: {reviews_data}")
            reviews_data = {"status": "error", "error": str(reviews_data)}
        if isinstance(content_data, Exception):
            logger.warning(f"Content data failed: {content_data}")
            content_data = {"status": "error", "error": str(content_data)}

        # Aggregate overview numbers
        total_clicks = (
            seo_data.get("total_clicks", 0)
            + ads_data.get("total_clicks", 0)
            + content_data.get("total_clicks", 0)
        )
        total_impressions = (
            seo_data.get("total_impressions", 0)
            + ads_data.get("total_impressions", 0)
            + content_data.get("total_impressions", 0)
        )
        total_conversions = ads_data.get("total_conversions", 0.0)
        total_spend = ads_data.get("total_spend", 0.0)
        roas = ads_data.get("roas", 0.0)

        # Build alerts
        alerts = []
        if seo_data.get("avg_position", 0) > 20:
            alerts.append({
                "severity": "warning",
                "channel": "seo",
                "message": f"Average GSC position is {seo_data['avg_position']} - needs improvement"
            })
        if ads_data.get("avg_cpa", 0) > 100 and ads_data.get("total_conversions", 0) > 0:
            alerts.append({
                "severity": "warning",
                "channel": "google_ads",
                "message": f"Average CPA is ${ads_data['avg_cpa']:.2f} - above $100 target"
            })
        if reviews_data.get("avg_rating", 0) > 0 and reviews_data["avg_rating"] < 4.0:
            alerts.append({
                "severity": "critical",
                "channel": "reviews",
                "message": f"Average review rating dropped to {reviews_data['avg_rating']} - below 4.0 threshold"
            })
        if reviews_data.get("response_rate", 0) < 80 and reviews_data.get("total_reviews", 0) > 0:
            alerts.append({
                "severity": "warning",
                "channel": "reviews",
                "message": f"Review response rate is {reviews_data['response_rate']}% - target is 80%+"
            })

        # Build top performers list
        top_performers = []
        if seo_data.get("top_10_keywords", 0) > 0:
            top_performers.append({
                "channel": "seo",
                "metric": "top_10_keywords",
                "value": seo_data["top_10_keywords"],
                "label": f"{seo_data['top_10_keywords']} keywords in top 10"
            })
        if ads_data.get("campaigns"):
            best_campaign = max(ads_data["campaigns"], key=lambda c: c.get("conversions", 0), default=None)
            if best_campaign and best_campaign.get("conversions", 0) > 0:
                top_performers.append({
                    "channel": "google_ads",
                    "metric": "best_campaign",
                    "value": best_campaign["conversions"],
                    "label": f"Top campaign: {best_campaign['name']} ({best_campaign['conversions']} conversions)"
                })

        # Strip verbose campaign list from channel_performance to keep report clean
        ads_summary = {k: v for k, v in ads_data.items() if k != "campaigns"}

        report = {
            "period": f"Last {date_range} days",
            "generated_at": datetime.utcnow().isoformat(),
            "overview": {
                "total_traffic": total_clicks,
                "total_impressions": total_impressions,
                "total_conversions": round(total_conversions, 2),
                "total_spend": round(total_spend, 2),
                "total_revenue": round(total_spend * roas, 2) if roas > 0 else 0.0,
                "roi": round((roas - 1) * 100, 1) if roas > 0 else 0.0,
                "roas": roas,
            },
            "channel_performance": {
                "seo": seo_data,
                "google_ads": ads_summary,
                "reviews": reviews_data,
                "content": content_data,
            },
            "top_performers": top_performers,
            "alerts": alerts,
            "recommendations": [],
        }

        # Persist daily metrics from this run
        try:
            await self.collect_daily_metrics(
                seo_data=seo_data,
                ads_data=ads_data,
                reviews_data=reviews_data,
                content_data=content_data,
            )
        except Exception as e:
            logger.warning(f"Failed to persist daily metrics: {e}")

        # Generate AI insights
        try:
            insights = await self._generate_insights(report)
            report["insights"] = insights
        except Exception as e:
            logger.warning(f"Insight generation failed: {e}")
            report["insights"] = []

        logger.info("Weekly report generated successfully")
        return report

    # ── Daily metrics collection ──────────────────────────────────────

    async def collect_daily_metrics(
        self,
        seo_data: Optional[Dict[str, Any]] = None,
        ads_data: Optional[Dict[str, Any]] = None,
        reviews_data: Optional[Dict[str, Any]] = None,
        content_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Gather data from all agents and upsert into the daily_metrics table.

        Can be called with pre-fetched data (from generate_weekly_report) or
        with no arguments to trigger fresh fetches from each agent.

        Args:
            seo_data: Pre-fetched SEO data dict, or None to fetch fresh.
            ads_data: Pre-fetched Ads data dict, or None to fetch fresh.
            reviews_data: Pre-fetched Reviews data dict, or None to fetch fresh.
            content_data: Pre-fetched Content data dict, or None to fetch fresh.

        Returns:
            Summary of what was upserted.
        """
        logger.info("Collecting daily metrics for all channels")

        # Fetch any missing channel data
        if seo_data is None:
            seo_data = await self._fetch_seo_data()
        if ads_data is None:
            ads_data = await self._fetch_ads_data()
        if reviews_data is None:
            reviews_data = await self._fetch_reviews_data()
        if content_data is None:
            content_data = await self._fetch_content_data()

        today = datetime.utcnow().strftime("%Y-%m-%d")
        sb = get_supabase()
        upserted = []

        # Helper to upsert a single channel row
        def _upsert_channel(channel: str, row: Dict[str, Any]):
            record = {
                "date": today,
                "channel": channel,
                "total_sessions": row.get("total_clicks", 0),
                "total_conversions": row.get("total_conversions", 0),
                "total_revenue": row.get("total_revenue", 0.0),
                "total_ad_spend": row.get("total_ad_spend", 0.0),
                "avg_position": row.get("avg_position", 0.0),
                "total_clicks": row.get("total_clicks", 0),
                "total_impressions": row.get("total_impressions", 0),
            }
            try:
                sb.table(DAILY_METRICS_TABLE).upsert(
                    record,
                    on_conflict="date,channel",
                ).execute()
                upserted.append(channel)
            except Exception as e:
                logger.warning(f"Upsert failed for {channel}: {e}")

        # SEO channel
        _upsert_channel("seo", {
            "total_clicks": seo_data.get("total_clicks", 0),
            "total_impressions": seo_data.get("total_impressions", 0),
            "avg_position": seo_data.get("avg_position", 0.0),
            "total_conversions": 0,
            "total_revenue": 0.0,
            "total_ad_spend": 0.0,
        })

        # Google Ads channel
        ads_spend = ads_data.get("total_spend", 0.0)
        ads_roas = ads_data.get("roas", 0.0)
        _upsert_channel("google_ads", {
            "total_clicks": ads_data.get("total_clicks", 0),
            "total_impressions": ads_data.get("total_impressions", 0),
            "total_conversions": ads_data.get("total_conversions", 0),
            "total_ad_spend": ads_spend,
            "total_revenue": round(ads_spend * ads_roas, 2) if ads_roas > 0 else 0.0,
            "avg_position": 0.0,
        })

        # Reviews channel (non-traffic channel, so clicks/impressions are 0)
        _upsert_channel("reviews", {
            "total_clicks": 0,
            "total_impressions": 0,
            "total_conversions": 0,
            "total_revenue": 0.0,
            "total_ad_spend": 0.0,
            "avg_position": reviews_data.get("avg_rating", 0.0),
        })

        # Content channel
        _upsert_channel("content", {
            "total_clicks": content_data.get("total_clicks", 0),
            "total_impressions": content_data.get("total_impressions", 0),
            "total_conversions": 0,
            "total_revenue": 0.0,
            "total_ad_spend": 0.0,
            "avg_position": 0.0,
        })

        result = {
            "date": today,
            "channels_upserted": upserted,
            "total_channels": len(upserted),
        }
        logger.info(f"Daily metrics collected: {len(upserted)} channels upserted for {today}")
        return result

    # ── Live metrics (bypass daily_metrics table) ─────────────────────

    async def get_live_metrics(self) -> Dict[str, Any]:
        """
        Fetch live metrics directly from all agents, bypassing the
        daily_metrics table. Useful for dashboards that need real-time data.

        Returns:
            Dict with per-channel live data and an aggregated overview.
        """
        logger.info("Fetching live metrics from all agents")

        seo_data, ads_data, reviews_data, content_data = await asyncio.gather(
            self._fetch_seo_data(),
            self._fetch_ads_data(),
            self._fetch_reviews_data(),
            self._fetch_content_data(),
            return_exceptions=True,
        )

        # Normalise exceptions
        if isinstance(seo_data, Exception):
            seo_data = {"status": "error", "error": str(seo_data)}
        if isinstance(ads_data, Exception):
            ads_data = {"status": "error", "error": str(ads_data)}
        if isinstance(reviews_data, Exception):
            reviews_data = {"status": "error", "error": str(reviews_data)}
        if isinstance(content_data, Exception):
            content_data = {"status": "error", "error": str(content_data)}

        total_clicks = (
            seo_data.get("total_clicks", 0)
            + ads_data.get("total_clicks", 0)
            + content_data.get("total_clicks", 0)
        )
        total_impressions = (
            seo_data.get("total_impressions", 0)
            + ads_data.get("total_impressions", 0)
            + content_data.get("total_impressions", 0)
        )

        return {
            "fetched_at": datetime.utcnow().isoformat(),
            "overview": {
                "total_clicks": total_clicks,
                "total_impressions": total_impressions,
                "total_conversions": round(ads_data.get("total_conversions", 0), 2),
                "total_spend": round(ads_data.get("total_spend", 0), 2),
                "roas": ads_data.get("roas", 0.0),
            },
            "channels": {
                "seo": seo_data,
                "google_ads": {k: v for k, v in ads_data.items() if k != "campaigns"},
                "reviews": reviews_data,
                "content": content_data,
            },
        }

    # ── Existing methods (unchanged) ──────────────────────────────────

    async def calculate_attribution(
        self,
        conversions: List[Dict[str, Any]],
        model: str = "linear"
    ) -> Dict[str, Any]:
        """
        Calculate attribution across channels

        Args:
            conversions: List of conversions with touchpoints
            model: Attribution model to use

        Returns:
            Attribution results by channel
        """
        logger.info(f"Calculating {model} attribution for {len(conversions)} conversions")

        if model not in self.ATTRIBUTION_MODELS:
            raise ValueError(f"Unknown attribution model: {model}")

        attribution = {
            "model": model,
            "total_conversions": len(conversions),
            "channel_attribution": {
                "seo": 0.0,
                "content": 0.0,
                "google_ads": 0.0,
                "social": 0.0,
                "reviews": 0.0
            }
        }

        # Apply attribution logic based on model
        for conversion in conversions:
            touchpoints = conversion.get("touchpoints", [])

            if model == "first_touch":
                if touchpoints:
                    channel = touchpoints[0].get("channel")
                    attribution["channel_attribution"][channel] += 1.0

            elif model == "last_touch":
                if touchpoints:
                    channel = touchpoints[-1].get("channel")
                    attribution["channel_attribution"][channel] += 1.0

            elif model == "linear":
                if touchpoints:
                    credit_per_touch = 1.0 / len(touchpoints)
                    for touchpoint in touchpoints:
                        channel = touchpoint.get("channel")
                        attribution["channel_attribution"][channel] += credit_per_touch

        logger.info(f"Attribution calculated using {model} model")

        return attribution

    async def calculate_roi(
        self,
        channel: str,
        date_range: int = 30
    ) -> Dict[str, Any]:
        """
        Calculate ROI for a specific channel using real data.

        Args:
            channel: Channel name
            date_range: Days to analyze

        Returns:
            ROI metrics
        """
        logger.info(f"Calculating ROI for {channel} ({date_range} days)")

        roi_data = {
            "channel": channel,
            "period": f"Last {date_range} days",
            "metrics": {
                "total_spend": 0.0,
                "total_revenue": 0.0,
                "roi": 0.0,
                "roas": 0.0,
                "conversions": 0,
                "cpa": 0.0,
                "ltv": 0.0
            }
        }

        if channel == "google_ads":
            ads_data = await self._fetch_ads_data(date_range=date_range)
            if ads_data.get("status") == "ok":
                spend = ads_data.get("total_spend", 0)
                roas = ads_data.get("roas", 0)
                revenue = round(spend * roas, 2) if roas > 0 else 0.0
                roi_data["metrics"] = {
                    "total_spend": spend,
                    "total_revenue": revenue,
                    "roi": round((roas - 1) * 100, 1) if roas > 0 else 0.0,
                    "roas": roas,
                    "conversions": ads_data.get("total_conversions", 0),
                    "cpa": ads_data.get("avg_cpa", 0),
                    "ltv": 0.0,
                }
        elif channel == "seo":
            seo_data = await self._fetch_seo_data(date_range=max(date_range, 28))
            roi_data["metrics"]["total_spend"] = 0.0  # Organic - no direct spend
            roi_data["metrics"]["conversions"] = 0
            roi_data["seo_metrics"] = {
                "clicks": seo_data.get("total_clicks", 0),
                "impressions": seo_data.get("total_impressions", 0),
                "avg_position": seo_data.get("avg_position", 0),
            }
        else:
            # Fallback: aggregate from daily_metrics table for any channel
            try:
                sb = get_supabase()
                cutoff = (datetime.utcnow() - timedelta(days=date_range)).strftime("%Y-%m-%d")
                result = (
                    sb.table(DAILY_METRICS_TABLE)
                    .select("*")
                    .eq("channel", channel)
                    .gte("date", cutoff)
                    .execute()
                )
                rows = result.data or []
                if rows:
                    total_spend = sum(r.get("total_ad_spend", 0) or 0 for r in rows)
                    total_revenue = sum(r.get("total_revenue", 0) or 0 for r in rows)
                    total_conversions = sum(r.get("total_conversions", 0) or 0 for r in rows)
                    roas = round(total_revenue / total_spend, 2) if total_spend > 0 else 0.0
                    roi_data["metrics"] = {
                        "total_spend": round(total_spend, 2),
                        "total_revenue": round(total_revenue, 2),
                        "roi": round((roas - 1) * 100, 1) if roas > 0 else 0.0,
                        "roas": roas,
                        "conversions": round(total_conversions, 2),
                        "cpa": round(total_spend / total_conversions, 2) if total_conversions > 0 else 0.0,
                        "ltv": 0.0,
                    }
            except Exception as e:
                logger.warning(f"ROI calculation from daily_metrics failed for {channel}: {e}")

        return roi_data

    async def identify_trends(
        self,
        metric: str,
        channel: str,
        lookback_days: int = 90
    ) -> Dict[str, Any]:
        """
        Identify trends in a specific metric

        Args:
            metric: Metric to analyze
            channel: Channel to analyze
            lookback_days: Days of historical data

        Returns:
            Trend analysis
        """
        logger.info(f"Analyzing {metric} trend for {channel}")

        # Fetch from daily_metrics table for historical trend
        trend = {
            "metric": metric,
            "channel": channel,
            "period": f"Last {lookback_days} days",
            "direction": "stable",
            "change_percent": 0.0,
            "data_points": [],
            "forecast": {
                "next_7_days": 0.0,
                "next_30_days": 0.0
            }
        }

        try:
            sb = get_supabase()
            cutoff = (datetime.utcnow() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
            result = (
                sb.table(DAILY_METRICS_TABLE)
                .select("*")
                .eq("channel", channel)
                .gte("date", cutoff)
                .order("date")
                .execute()
            )
            rows = result.data or []

            if len(rows) >= 2:
                # Map metric name to column
                col_map = {
                    "clicks": "total_clicks",
                    "impressions": "total_impressions",
                    "conversions": "total_conversions",
                    "spend": "total_ad_spend",
                    "revenue": "total_revenue",
                    "sessions": "total_sessions",
                    "position": "avg_position",
                }
                col = col_map.get(metric, metric)

                values = [r.get(col, 0) or 0 for r in rows]
                trend["data_points"] = [
                    {"date": r["date"], "value": r.get(col, 0) or 0}
                    for r in rows
                ]

                # Compare first half to second half for direction
                mid = len(values) // 2
                first_half_avg = sum(values[:mid]) / mid if mid > 0 else 0
                second_half_avg = sum(values[mid:]) / (len(values) - mid) if (len(values) - mid) > 0 else 0

                if first_half_avg > 0:
                    change = ((second_half_avg - first_half_avg) / first_half_avg) * 100
                    trend["change_percent"] = round(change, 1)
                    if change > 10:
                        trend["direction"] = "up"
                    elif change < -10:
                        trend["direction"] = "down"

        except Exception as e:
            logger.warning(f"Trend analysis failed: {e}")

        return trend

    async def generate_insights(
        self,
        data: Dict[str, Any]
    ) -> List[str]:
        """
        Generate AI-powered insights from analytics data

        Args:
            data: Analytics data

        Returns:
            List of insights
        """
        logger.info("Generating AI insights")

        system_prompt = """You are a marketing analytics expert analyzing data for Successifier.

Generate actionable insights that:
- Identify opportunities
- Flag issues
- Suggest optimizations
- Are specific and data-driven

Keep each insight to 1-2 sentences."""

        user_prompt = f"""Analyze this marketing data and generate 3-5 key insights:

{data}

Focus on:
- What's working well
- What needs attention
- Specific recommendations
"""

        def _call():
            return self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}]
            )
        response = await asyncio.to_thread(_call)

        insights_text = response.content[0].text.strip()
        insights = [i.strip() for i in insights_text.split('\n') if i.strip()]

        logger.info(f"Generated {len(insights)} insights")

        return insights

    async def create_dashboard(
        self,
        dashboard_type: str = "overview"
    ) -> Dict[str, Any]:
        """
        Create marketing dashboard data populated with real metrics from
        daily_metrics, keywords, content_pieces, and live agent data.

        Args:
            dashboard_type: Type of dashboard (overview, seo, ads, etc.)

        Returns:
            Dashboard configuration and data
        """
        logger.info(f"Creating {dashboard_type} dashboard")

        sb = get_supabase()

        if dashboard_type == "overview":
            dashboard = await self._build_overview_dashboard(sb)
        elif dashboard_type == "seo":
            dashboard = await self._build_seo_dashboard(sb)
        elif dashboard_type == "ads":
            dashboard = await self._build_ads_dashboard(sb)
        else:
            dashboard = await self._build_overview_dashboard(sb)

        logger.info(f"Dashboard created: {dashboard['title']}")
        return dashboard

    async def _build_overview_dashboard(self, sb) -> Dict[str, Any]:
        """Build the overview dashboard with real aggregated data."""
        # Fetch last 30 days of daily_metrics
        cutoff = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")

        def _query():
            return sb.table(DAILY_METRICS_TABLE).select("*").gte("date", cutoff).order("date").execute()

        try:
            result = await asyncio.to_thread(_query)
            rows = result.data or []
        except Exception as e:
            logger.warning(f"Dashboard daily_metrics query failed: {e}")
            rows = []

        total_traffic = sum(r.get("total_sessions", 0) or 0 for r in rows)
        total_conversions = sum(r.get("total_conversions", 0) or 0 for r in rows)
        total_spend = sum(r.get("total_ad_spend", 0) or 0 for r in rows)
        total_revenue = sum(r.get("total_revenue", 0) or 0 for r in rows)
        roi_pct = round((total_revenue - total_spend) / total_spend * 100, 1) if total_spend > 0 else 0.0

        # Traffic by channel
        channel_traffic: Dict[str, int] = {}
        for r in rows:
            ch = r.get("channel", "unknown")
            channel_traffic[ch] = channel_traffic.get(ch, 0) + (r.get("total_sessions", 0) or 0)
        traffic_chart = [{"channel": ch, "sessions": v} for ch, v in sorted(channel_traffic.items(), key=lambda x: -x[1])]

        # Top performing content
        def _query_content():
            return sb.table(CONTENT_PIECES_TABLE).select("title,status,impressions_30d,clicks_30d").eq("status", "published").order("clicks_30d", desc=True).limit(5).execute()

        try:
            content_result = await asyncio.to_thread(_query_content)
            top_content = content_result.data or []
        except Exception as e:
            logger.warning(f"Dashboard content query failed: {e}")
            top_content = []

        return {
            "title": "Marketing Overview",
            "widgets": [
                {"type": "metric", "title": "Total Traffic", "value": total_traffic},
                {"type": "metric", "title": "Conversions", "value": round(total_conversions, 2)},
                {"type": "metric", "title": "ROI", "value": f"{roi_pct}%"},
                {"type": "metric", "title": "Total Spend", "value": f"${total_spend:,.2f}"},
                {"type": "metric", "title": "Total Revenue", "value": f"${total_revenue:,.2f}"},
                {"type": "chart", "title": "Traffic by Channel", "data": traffic_chart},
                {"type": "table", "title": "Top Performing Content", "data": top_content},
            ],
        }

    async def _build_seo_dashboard(self, sb) -> Dict[str, Any]:
        """Build the SEO dashboard with real keyword and traffic data."""
        seo_data = await self._fetch_seo_data()

        # Top keywords from the keywords table
        def _query_keywords():
            return (
                sb.table(KEYWORDS_TABLE)
                .select("keyword,current_position,current_clicks,current_impressions,current_ctr")
                .order("current_clicks", desc=True)
                .limit(10)
                .execute()
            )

        try:
            kw_result = await asyncio.to_thread(_query_keywords)
            top_keywords = kw_result.data or []
        except Exception as e:
            logger.warning(f"Dashboard keywords query failed: {e}")
            top_keywords = []

        # SEO traffic trend from daily_metrics
        cutoff = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")

        def _query_trend():
            return (
                sb.table(DAILY_METRICS_TABLE)
                .select("date,total_sessions,total_clicks,total_impressions")
                .eq("channel", "seo")
                .gte("date", cutoff)
                .order("date")
                .execute()
            )

        try:
            trend_result = await asyncio.to_thread(_query_trend)
            trend_data = trend_result.data or []
        except Exception as e:
            logger.warning(f"Dashboard SEO trend query failed: {e}")
            trend_data = []

        return {
            "title": "SEO Performance",
            "widgets": [
                {"type": "metric", "title": "Organic Traffic", "value": seo_data.get("total_clicks", 0)},
                {"type": "metric", "title": "Avg. Position", "value": round(seo_data.get("avg_position", 0), 1)},
                {"type": "metric", "title": "Keywords Tracked", "value": seo_data.get("keywords_tracked", 0)},
                {"type": "metric", "title": "Top 10 Keywords", "value": seo_data.get("top_10_keywords", 0)},
                {"type": "chart", "title": "Traffic Trend", "data": trend_data},
                {"type": "table", "title": "Top Keywords", "data": top_keywords},
            ],
        }

    async def _build_ads_dashboard(self, sb) -> Dict[str, Any]:
        """Build the Google Ads dashboard with real campaign data."""
        ads_data = await self._fetch_ads_data()
        campaigns = ads_data.get("campaigns", [])

        # Ads spend trend from daily_metrics
        cutoff = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")

        def _query_trend():
            return (
                sb.table(DAILY_METRICS_TABLE)
                .select("date,total_ad_spend,total_revenue,total_conversions,total_clicks")
                .eq("channel", "google_ads")
                .gte("date", cutoff)
                .order("date")
                .execute()
            )

        try:
            trend_result = await asyncio.to_thread(_query_trend)
            trend_data = trend_result.data or []
        except Exception as e:
            logger.warning(f"Dashboard ads trend query failed: {e}")
            trend_data = []

        # Top campaigns (up to 5)
        top_campaigns = sorted(campaigns, key=lambda c: c.get("conversions", 0), reverse=True)[:5] if campaigns else []

        spend = ads_data.get("total_spend", 0)
        roas = ads_data.get("roas", 0)
        cpa = ads_data.get("avg_cpa", 0)

        return {
            "title": "Google Ads Performance",
            "widgets": [
                {"type": "metric", "title": "Spend", "value": f"${spend:,.2f}"},
                {"type": "metric", "title": "ROAS", "value": f"{roas}x"},
                {"type": "metric", "title": "CPA", "value": f"${cpa:,.2f}"},
                {"type": "metric", "title": "Conversions", "value": round(ads_data.get("total_conversions", 0), 2)},
                {"type": "chart", "title": "Campaign Performance", "data": trend_data},
                {"type": "table", "title": "Top Campaigns", "data": top_campaigns},
            ],
        }

    async def _generate_insights(self, report_data: Dict[str, Any]) -> List[str]:
        """Internal method to generate insights"""
        return await self.generate_insights(report_data)


# Global analytics agent instance
analytics_agent = AnalyticsAgent()
