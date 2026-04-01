"""
Demo seed data for SAMA 2.0
When DEMO_MODE is enabled, GET endpoints return this data instead of empty results.
"""

from datetime import datetime, timedelta, timezone

_now = datetime.now(timezone.utc)


def _date_ago(days: int) -> str:
    return (_now - timedelta(days=days)).isoformat()


# ── SEO Keywords ─────────────────────────────────────────────────────────────

DEMO_SEO_KEYWORDS = [
    {"id": "demo-kw-1", "keyword": "customer success platform", "position": 4, "previous_position": 7, "clicks": 320, "impressions": 8500, "ctr": 3.8, "trend": "up", "intent": "commercial"},
    {"id": "demo-kw-2", "keyword": "customer retention software", "position": 8, "previous_position": 12, "clicks": 185, "impressions": 6200, "ctr": 3.0, "trend": "up", "intent": "commercial"},
    {"id": "demo-kw-3", "keyword": "reduce churn rate", "position": 3, "previous_position": 3, "clicks": 410, "impressions": 9800, "ctr": 4.2, "trend": "stable", "intent": "informational"},
    {"id": "demo-kw-4", "keyword": "net revenue retention", "position": 6, "previous_position": 9, "clicks": 145, "impressions": 4300, "ctr": 3.4, "trend": "up", "intent": "informational"},
    {"id": "demo-kw-5", "keyword": "customer health score", "position": 2, "previous_position": 2, "clicks": 530, "impressions": 11000, "ctr": 4.8, "trend": "stable", "intent": "informational"},
    {"id": "demo-kw-6", "keyword": "saas churn analysis", "position": 11, "previous_position": 15, "clicks": 95, "impressions": 3100, "ctr": 3.1, "trend": "up", "intent": "informational"},
    {"id": "demo-kw-7", "keyword": "customer onboarding tool", "position": 7, "previous_position": 5, "clicks": 210, "impressions": 5800, "ctr": 3.6, "trend": "down", "intent": "commercial"},
    {"id": "demo-kw-8", "keyword": "product adoption metrics", "position": 5, "previous_position": 8, "clicks": 175, "impressions": 4900, "ctr": 3.6, "trend": "up", "intent": "informational"},
    {"id": "demo-kw-9", "keyword": "customer success manager tools", "position": 9, "previous_position": 14, "clicks": 130, "impressions": 3700, "ctr": 3.5, "trend": "up", "intent": "commercial"},
    {"id": "demo-kw-10", "keyword": "gainsight alternative", "position": 1, "previous_position": 2, "clicks": 680, "impressions": 12500, "ctr": 5.4, "trend": "up", "intent": "commercial"},
]

# ── Content Pieces ───────────────────────────────────────────────────────────

DEMO_CONTENT_PIECES = [
    {"id": "demo-cp-1", "title": "The Ultimate Guide to Customer Health Scores", "content_type": "blog_article", "status": "published", "target_keyword": "customer health score", "word_count": 2400, "impressions_30d": 1800, "clicks_30d": 120, "avg_position": 2.3, "created_at": _date_ago(14), "published_at": _date_ago(12)},
    {"id": "demo-cp-2", "title": "5 Strategies to Reduce SaaS Churn in 2025", "content_type": "blog_article", "status": "published", "target_keyword": "reduce churn rate", "word_count": 1950, "impressions_30d": 2200, "clicks_30d": 185, "avg_position": 3.1, "created_at": _date_ago(21), "published_at": _date_ago(19)},
    {"id": "demo-cp-3", "title": "Gainsight vs Successifier: Honest Comparison", "content_type": "comparison", "status": "draft", "target_keyword": "gainsight alternative", "word_count": 3100, "impressions_30d": 0, "clicks_30d": 0, "avg_position": 0, "created_at": _date_ago(3), "published_at": None},
    {"id": "demo-cp-4", "title": "Net Revenue Retention: The Metric That Matters Most", "content_type": "blog_article", "status": "review", "target_keyword": "net revenue retention", "word_count": 1700, "impressions_30d": 0, "clicks_30d": 0, "avg_position": 0, "created_at": _date_ago(1), "published_at": None},
    {"id": "demo-cp-5", "title": "Customer Success Platform Buyer's Guide", "content_type": "landing_page", "status": "draft", "target_keyword": "customer success platform", "word_count": 2800, "impressions_30d": 0, "clicks_30d": 0, "avg_position": 0, "created_at": _date_ago(5), "published_at": None},
]

# ── Social Posts ─────────────────────────────────────────────────────────────

DEMO_SOCIAL_POSTS = [
    {"id": "demo-sp-1", "platform": "linkedin", "content": "Customer health scores aren't just a metric — they're a mindset shift.", "status": "published", "likes": 84, "comments": 12, "shares": 8, "impressions": 4200, "engagement_rate": 2.5, "published_at": _date_ago(1)},
    {"id": "demo-sp-2", "platform": "twitter", "content": "Hot take: if your CS team still measures success by NPS alone, you're leaving money on the table.", "status": "published", "likes": 142, "comments": 23, "shares": 31, "impressions": 8900, "engagement_rate": 2.2, "published_at": _date_ago(2)},
    {"id": "demo-sp-3", "platform": "linkedin", "content": "We analyzed 500+ SaaS companies and found the #1 predictor of churn.", "status": "published", "likes": 210, "comments": 45, "shares": 28, "impressions": 12300, "engagement_rate": 2.3, "published_at": _date_ago(3)},
    {"id": "demo-sp-4", "platform": "twitter", "content": "Your onboarding flow is your first impression. Make it count.", "status": "published", "likes": 67, "comments": 8, "shares": 14, "impressions": 3800, "engagement_rate": 2.3, "published_at": _date_ago(4)},
    {"id": "demo-sp-5", "platform": "linkedin", "content": "Just shipped: automated customer health score alerts.", "status": "published", "likes": 156, "comments": 32, "shares": 19, "impressions": 9100, "engagement_rate": 2.3, "published_at": _date_ago(5)},
    {"id": "demo-sp-6", "platform": "twitter", "content": "Net revenue retention > new logo acquisition. Here's why.", "status": "scheduled", "likes": 0, "comments": 0, "shares": 0, "impressions": 0, "engagement_rate": 0, "published_at": None},
    {"id": "demo-sp-7", "platform": "linkedin", "content": "The best CS teams I've seen all have one thing in common: proactive playbooks.", "status": "published", "likes": 98, "comments": 18, "shares": 11, "impressions": 5600, "engagement_rate": 2.3, "published_at": _date_ago(7)},
    {"id": "demo-sp-8", "platform": "twitter", "content": "Churn isn't a surprise — it's a signal you missed.", "status": "published", "likes": 203, "comments": 29, "shares": 42, "impressions": 11200, "engagement_rate": 2.4, "published_at": _date_ago(8)},
    {"id": "demo-sp-9", "platform": "linkedin", "content": "Product adoption isn't about features shipped. It's about value realized.", "status": "draft", "likes": 0, "comments": 0, "shares": 0, "impressions": 0, "engagement_rate": 0, "published_at": None},
    {"id": "demo-sp-10", "platform": "twitter", "content": "Every customer success team needs a single source of truth.", "status": "published", "likes": 78, "comments": 11, "shares": 9, "impressions": 4100, "engagement_rate": 2.4, "published_at": _date_ago(10)},
]

# ── Analytics Overview (30 days) ─────────────────────────────────────────────

DEMO_ANALYTICS_OVERVIEW = {
    "period": "last_30_days",
    "summary": {
        "total_sessions": 28400,
        "total_pageviews": 68200,
        "total_clicks": 3250,
        "total_impressions": 89000,
        "avg_position": 6.2,
        "bounce_rate": 42.1,
        "avg_session_duration": 185,
        "conversion_rate": 2.8,
        "total_conversions": 795,
    },
    "channels": {
        "organic_search": {"sessions": 14200, "conversions": 425, "revenue": 42500},
        "social": {"sessions": 6800, "conversions": 170, "revenue": 17000},
        "paid_search": {"sessions": 4100, "conversions": 123, "revenue": 12300},
        "direct": {"sessions": 2100, "conversions": 52, "revenue": 5200},
        "referral": {"sessions": 1200, "conversions": 25, "revenue": 2500},
    },
    "trends": [
        {"date": _date_ago(i), "sessions": 800 + (i % 7) * 80, "clicks": 90 + (i % 5) * 15, "impressions": 2500 + (i % 3) * 300, "conversions": 20 + (i % 4) * 5}
        for i in range(30)
    ],
}

# ── Ad Creatives ─────────────────────────────────────────────────────────────

DEMO_AD_CREATIVES = [
    {
        "id": "demo-ad-1",
        "platform": "google",
        "format": "responsive_search",
        "headline": "Customer Success Platform | Reduce Churn by 40%",
        "body_text": "Proactive health scores, automated playbooks, and real-time alerts. Trusted by 500+ SaaS companies. Start free trial.",
        "cta": "Start Free Trial",
        "status": "active",
        "performance": {"impressions": 12400, "clicks": 372, "ctr": 3.0, "conversions": 28, "cost": 1860.0, "cpa": 66.43},
        "created_at": _date_ago(15),
    },
    {
        "id": "demo-ad-2",
        "platform": "linkedin",
        "format": "sponsored_content",
        "headline": "Stop Reacting to Churn. Start Predicting It.",
        "body_text": "Successifier gives your CS team the visibility they need before it's too late. See how top SaaS companies retain 95%+ of revenue.",
        "cta": "See a Demo",
        "status": "draft",
        "performance": {"impressions": 0, "clicks": 0, "ctr": 0, "conversions": 0, "cost": 0, "cpa": 0},
        "created_at": _date_ago(2),
    },
    {
        "id": "demo-ad-3",
        "platform": "meta",
        "format": "single_image",
        "headline": "The #1 Gainsight Alternative for Growing SaaS",
        "body_text": "Half the price, twice the ease. Migrate in days, not months. Join 500+ companies that made the switch.",
        "cta": "Learn More",
        "status": "paused",
        "performance": {"impressions": 8900, "clicks": 178, "ctr": 2.0, "conversions": 11, "cost": 1245.0, "cpa": 113.18},
        "created_at": _date_ago(22),
    },
]

# ── Ad Platform Credentials (demo — all disconnected) ────────────────────────

DEMO_AD_CREDENTIALS = [
    {"platform": "google", "is_connected": False, "account_id": None, "connected_at": None},
    {"platform": "meta", "is_connected": False, "account_id": None, "connected_at": None},
    {"platform": "linkedin", "is_connected": False, "account_id": None, "connected_at": None},
]
