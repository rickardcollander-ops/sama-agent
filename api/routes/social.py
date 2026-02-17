from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from agents.social import social_agent

router = APIRouter()


class PostRequest(BaseModel):
    topic: str
    style: str = "educational"
    thread: bool = False


class ReplyRequest(BaseModel):
    original_tweet: str
    context: Optional[str] = None


class ScheduleRequest(BaseModel):
    date_range: int = 7


class MentionMonitorRequest(BaseModel):
    mentions: List[Dict[str, Any]]


class EngagementAnalysisRequest(BaseModel):
    posts: List[Dict[str, Any]]


@router.get("/status")
async def get_status():
    """Get Social agent status"""
    return {
        "agent": "social",
        "status": "operational",
        "content_calendar": list(social_agent.CONTENT_CALENDAR.keys()),
        "engagement_rules": len(social_agent.ENGAGEMENT_RULES),
        "hashtag_strategy": social_agent.HASHTAG_STRATEGY
    }


@router.post("/post/generate")
async def generate_post(request: PostRequest):
    """Generate a social media post"""
    try:
        result = await social_agent.generate_post(
            topic=request.topic,
            style=request.style,
            thread=request.thread
        )
        return {"success": True, "post": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class LinkedInPostRequest(BaseModel):
    topic: str
    style: str = "professional"
    include_hashtags: bool = True


class LinkedInPublishRequest(BaseModel):
    content: str


@router.post("/linkedin/generate")
async def generate_linkedin_post(request: LinkedInPostRequest):
    """Generate a LinkedIn post"""
    try:
        result = await social_agent.generate_post(
            topic=request.topic,
            style=request.style,
            thread=False
        )
        content = result.get("content", result.get("tweet", "")) if isinstance(result, dict) else str(result)
        return {"success": True, "content": content}
    except Exception as e:
        return {"success": False, "content": f"Generated post about: {request.topic}\n\nNote: AI generation requires API key. Configure ANTHROPIC_API_KEY in Railway.", "error": str(e)}


@router.post("/linkedin/post")
async def publish_linkedin_post(request: LinkedInPublishRequest):
    """Publish a LinkedIn post (placeholder - needs LinkedIn API)"""
    return {
        "success": True,
        "message": "Post queued for publishing. Connect LinkedIn API for auto-posting.",
        "content_length": len(request.content)
    }


@router.post("/post/publish")
async def publish_post(request: LinkedInPublishRequest):
    """Publish a social media post (placeholder)"""
    return {
        "success": True,
        "message": "Post queued for publishing. Connect social media APIs for auto-posting.",
        "content_length": len(request.content)
    }


@router.post("/reply/generate")
async def generate_reply(request: ReplyRequest):
    """Generate a reply to a tweet"""
    try:
        reply = await social_agent.generate_reply(
            original_tweet=request.original_tweet,
            context=request.context
        )
        return {"success": True, "reply": reply}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/schedule")
async def schedule_posts(request: ScheduleRequest):
    """Generate content calendar"""
    try:
        scheduled = await social_agent.schedule_posts(
            date_range=request.date_range
        )
        return {
            "success": True,
            "scheduled_posts": scheduled,
            "count": len(scheduled)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/mentions/monitor")
async def monitor_mentions(request: MentionMonitorRequest):
    """Monitor and prioritize mentions"""
    try:
        prioritized = await social_agent.monitor_mentions(
            mentions=request.mentions
        )
        return {
            "success": True,
            "mentions": prioritized,
            "high_priority": sum(1 for m in prioritized if m["priority"] == "high")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/engagement/analyze")
async def analyze_engagement(request: EngagementAnalysisRequest):
    """Analyze post engagement"""
    try:
        analysis = await social_agent.analyze_engagement(
            posts=request.posts
        )
        return {"success": True, "analysis": analysis}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/content-calendar")
async def get_content_calendar():
    """Get content calendar template"""
    return {
        "calendar": social_agent.CONTENT_CALENDAR
    }


@router.get("/engagement-rules")
async def get_engagement_rules():
    """Get engagement rules"""
    return {
        "rules": social_agent.ENGAGEMENT_RULES
    }
