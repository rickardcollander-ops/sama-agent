from fastapi import APIRouter, HTTPException, BackgroundTasks, Body
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


@router.get("/actions")
async def get_social_actions(status: str = None, limit: int = 100):
    """Get Social actions from database"""
    from shared.actions_db import get_actions
    actions = await get_actions(agent_name="social", status=status, limit=limit)
    return {"success": True, "actions": actions}


@router.post("/analyze")
async def run_social_analysis():
    """Analyze social using OODA loop (Observe → Orient → Decide → Act → Reflect)"""
    from api.routes.social_analyze_ooda import run_social_analysis_with_ooda
    return await run_social_analysis_with_ooda()


@router.post("/analyze-legacy")
async def run_social_analysis_legacy():
    """Legacy social analysis (deprecated - use /analyze)"""
    from agents.social import is_twitter_configured

    actions = []
    mentions = []
    competitor_tweets = []
    twitter_configured = is_twitter_configured()

    # 1. Fetch real mentions if Twitter is configured
    if twitter_configured:
        try:
            mentions = await social_agent.get_mentions(max_results=20)
        except Exception:
            mentions = []
        
        # 2. Search competitor mentions
        try:
            competitor_tweets = await social_agent.search_competitor_mentions(max_results=10)
        except Exception:
            competitor_tweets = []
    
    # 3. Generate content calendar actions
    from datetime import datetime, timedelta
    today = datetime.now()
    for day_offset in range(7):
        post_date = today + timedelta(days=day_offset)
        day_name = post_date.strftime("%A").lower()
        if day_name in social_agent.CONTENT_CALENDAR:
            day_config = social_agent.CONTENT_CALENDAR[day_name]
            actions.append({
                "id": f"social-calendar-{day_name}-{post_date.strftime('%m%d')}",
                "type": "generate_post",
                "priority": "high" if day_offset < 2 else "medium",
                "title": f"{day_config['theme']} ({post_date.strftime('%A %b %d')})",
                "description": f"Format: {day_config['format']}. Example: {day_config['example']}",
                "action": f"Generate and schedule a {day_config['format'].lower()} about {day_config['theme'].lower()}",
                "topic": day_config["example"],
                "style": "educational",
                "is_thread": day_config["format"] == "Educational thread",
                "scheduled_date": post_date.strftime("%Y-%m-%d"),
                "status": "pending"
            })
    
    # 4. Generate mention reply actions
    for mention in mentions:
        user = mention.get("user", {})
        followers = user.get("followers_count", 0)
        username = user.get("username", "unknown")
        text = mention.get("text", "")
        priority = "high" if followers > 500 else "medium" if followers > 100 else "low"
        actions.append({
            "id": f"social-reply-{mention.get('id', '')}",
            "type": "reply",
            "priority": priority,
            "title": f"Reply to @{username} ({followers} followers)",
            "description": text[:200],
            "action": f"Generate and post a reply to this mention",
            "original_tweet": text,
            "tweet_id": mention.get("id", ""),
            "username": username,
            "status": "pending"
        })
    
    # 5. Competitor opportunity actions
    for tweet in competitor_tweets:
        user = tweet.get("user", {})
        username = user.get("username", "unknown")
        text = tweet.get("text", "")
        actions.append({
            "id": f"social-competitor-{tweet.get('id', '')}",
            "type": "competitor_engage",
            "priority": "medium",
            "title": f"Competitor opportunity: @{username}",
            "description": text[:200],
            "action": "Generate a helpful reply to this competitor-related tweet",
            "original_tweet": text,
            "tweet_id": tweet.get("id", ""),
            "username": username,
            "status": "pending"
        })
    
    # 6. Thread creation action (always suggest)
    actions.append({
        "id": "social-thread-weekly",
        "type": "generate_thread",
        "priority": "high",
        "title": "Create weekly educational thread",
        "description": "Threads get 3-5x more engagement. Create a value-packed thread on a trending CS topic.",
        "action": "Generate a 3-5 tweet thread about customer success best practices",
        "topic": "customer success best practices for reducing churn",
        "style": "educational",
        "status": "pending"
    })
    
    # 7. Hashtag strategy reminder
    if not twitter_configured:
        actions.append({
            "id": "social-config-twitter",
            "type": "config",
            "priority": "critical",
            "title": "Configure Twitter API credentials",
            "description": "Twitter API is not configured. Set TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET in Railway.",
            "action": "Add Twitter API credentials to enable real posting and mention monitoring",
            "status": "pending"
        })
    
    # Sort by priority
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    actions.sort(key=lambda a: priority_order.get(a.get("priority", "low"), 3))
    
    return {
        "success": True,
        "summary": {
            "total_actions": len(actions),
            "calendar_posts": sum(1 for a in actions if a["type"] == "generate_post"),
            "mention_replies": sum(1 for a in actions if a["type"] == "reply"),
            "competitor_opportunities": sum(1 for a in actions if a["type"] == "competitor_engage"),
            "twitter_configured": twitter_configured,
            "mentions_found": len(mentions),
            "competitor_tweets": len(competitor_tweets),
        },
        "mentions": mentions[:10],
        "actions": actions
    }


@router.post("/execute")
async def execute_social_action(action: Dict[str, Any] = Body(...)):
    """Execute a social media action"""
    if not action:
        raise HTTPException(status_code=400, detail="No action provided")

    action_type = action.get("type", "")
    action_db_id = action.get("db_id")  # UUID from agent_actions table

    async def _mark_completed(result_data: dict):
        """Helper to mark action as completed in DB"""
        if action_db_id:
            try:
                from shared.actions_db import update_action_status
                await update_action_status(action_db_id, "completed", execution_result=result_data)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Could not update action status: {e}")

    try:
        if action_type == "generate_post":
            topic = action.get("topic", "customer success tips")
            style = action.get("style", "educational")
            is_thread = action.get("is_thread", False)
            result = await social_agent.generate_post(
                topic=topic,
                style=style,
                thread=is_thread
            )
            response = {"success": True, "action_type": "post_generated", "result": result}
            await _mark_completed(result)
            return response

        elif action_type == "generate_thread":
            topic = action.get("topic", "customer success best practices")
            result = await social_agent.generate_post(
                topic=topic,
                style=action.get("style", "educational"),
                thread=True
            )
            response = {"success": True, "action_type": "thread_generated", "result": result}
            await _mark_completed(result)
            return response

        elif action_type in ("reply", "competitor_engage", "engage_interesting"):
            original_tweet = action.get("original_tweet", "")
            if original_tweet:
                context_map = {
                    "reply": f"User: @{action.get('username', 'unknown')}",
                    "competitor_engage": "This user is frustrated with a competitor. Be empathetic, provide value, mention Successifier only if directly relevant.",
                    "engage_interesting": "This user is discussing customer success challenges. Provide genuine value and insight. Only mention Successifier if it's directly relevant to their specific problem."
                }
                reply = await social_agent.generate_reply(
                    original_tweet=original_tweet,
                    context=context_map.get(action_type, "")
                )
                result_data = {
                    "reply": reply,
                    "original_tweet": original_tweet[:200],
                    "tweet_url": action.get("tweet_url", ""),
                    "username": action.get("username", ""),
                    "status": "draft"
                }
                await _mark_completed(result_data)
                return {"success": True, "action_type": "reply_generated", "result": result_data}
            return {"success": False, "message": "No original tweet provided"}

        elif action_type == "publish":
            content = action.get("content", "")
            tweet_id = action.get("reply_to")
            if content:
                result = await social_agent.publish_tweet(text=content, reply_to=tweet_id)
                await _mark_completed(result)
                return {"success": True, "action_type": "tweet_published", "result": result}
            return {"success": False, "message": "No content to publish"}

        elif action_type == "config":
            return {
                "success": False,
                "message": "Twitter API configuration must be done in Railway environment variables. Set TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET."
            }

        else:
            return {"success": False, "message": f"Unknown action type: {action_type}"}

    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/drafts")
async def get_social_drafts(limit: int = 20):
    """Get generated social content drafts from content_pieces"""
    from shared.database import get_supabase
    try:
        sb = get_supabase()
        result = sb.table("content_pieces").select("*").eq("created_by", "sama_social").order(
            "created_at", desc=True
        ).limit(limit).execute()
        return {"success": True, "drafts": result.data or []}
    except Exception as e:
        return {"success": False, "drafts": [], "error": str(e)}


@router.get("/interesting-tweets")
async def get_interesting_tweets():
    """Search for interesting tweets about CS pain points"""
    try:
        tweets = await social_agent.search_interesting_tweets(max_results=20)
        return {"success": True, "tweets": tweets, "count": len(tweets)}
    except Exception as e:
        return {"success": False, "tweets": [], "error": str(e)}
