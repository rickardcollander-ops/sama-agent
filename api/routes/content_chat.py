"""
Content Agent Chat Endpoint
Allows natural language interaction with the Content agent
"""

from fastapi import APIRouter, Body, HTTPException
from typing import Dict, Any
from agents.content import content_agent
from shared.chat_db import save_message, get_chat_history
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/chat/history")
async def get_chat_history_endpoint(user_id: str = "default_user"):
    """Get chat history for Content agent"""
    history = await get_chat_history("content", user_id)
    return {"history": history}


@router.post("/chat")
async def chat_with_content_agent(request: Dict[str, Any] = Body(...)):
    """
    Chat with Content agent using natural language
    
    Examples:
    - "Create a blog post about reducing customer churn"
    - "Generate a comparison page for Totango"
    - "Analyze content gaps for Q1"
    """
    message = request.get("message", "")
    user_id = request.get("user_id", "default_user")
    
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")
    
    # Save user message
    await save_message("content", "user", message, user_id)
    
    try:
        # Use Claude to interpret the request and route to appropriate action
        if not content_agent.client:
            return {
                "response": "Content agent is not configured. Please set ANTHROPIC_API_KEY in Railway."
            }
        
        # Fetch current content from Supabase for context
        from shared.database import get_supabase
        sb = get_supabase()
        
        try:
            content_result = sb.table("content_pieces").select("*").order("created_at", desc=True).limit(20).execute()
            saved_content = content_result.data or []
        except Exception:
            saved_content = []
        
        # Build context about saved content
        content_summary = f"\n\nCurrent content in database ({len(saved_content)} pieces):\n"
        for cp in saved_content[:10]:
            content_summary += f"- {cp.get('title', 'Untitled')} ({cp.get('status', 'draft')}, {cp.get('type', 'unknown')}, {cp.get('word_count', 0)} words)\n"
        
        # Ask Claude to interpret the user's request
        interpretation = content_agent.client.messages.create(
            model=content_agent.model,
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": f"""You are the Content Agent for Successifier. A user has sent you this message:

"{message}"
{content_summary}

Interpret their request and respond with ONE of these actions:
1. CREATE_BLOG_POST - if they want to create a blog post
2. CREATE_COMPARISON - if they want to create a competitor comparison page
3. ANALYZE_GAPS - if they want to analyze content gaps
4. LIST_CONTENT - if they want to see what content exists
5. PUBLISH_CONTENT - if they want to publish a draft
6. REPUBLISH_CONTENT - if they want to re-publish/push existing content to GitHub
7. DELETE_CONTENT - if they want to delete content
8. GENERAL_QUESTION - if they're asking a general question

Also extract key parameters:
- topic: the main topic/keyword
- competitor: competitor name (if applicable)
- content_title: exact title of content to modify (if applicable)

Respond in this exact format:
ACTION: [action type]
TOPIC: [topic]
COMPETITOR: [competitor name or N/A]
CONTENT_TITLE: [exact title or N/A]
EXPLANATION: [brief explanation of what you'll do]"""
            }]
        )
        
        response_text = interpretation.content[0].text
        
        # Parse Claude's response
        lines = response_text.strip().split('\n')
        action = None
        topic = None
        competitor = None
        content_title = None
        explanation = ""
        
        for line in lines:
            if line.startswith("ACTION:"):
                action = line.split(":", 1)[1].strip()
            elif line.startswith("TOPIC:"):
                topic = line.split(":", 1)[1].strip()
            elif line.startswith("COMPETITOR:"):
                competitor = line.split(":", 1)[1].strip()
                if competitor.lower() == "n/a":
                    competitor = None
            elif line.startswith("CONTENT_TITLE:"):
                content_title = line.split(":", 1)[1].strip()
                if content_title.lower() == "n/a":
                    content_title = None
            elif line.startswith("EXPLANATION:"):
                explanation = line.split(":", 1)[1].strip()
        
        # Execute the appropriate action
        if action == "CREATE_BLOG_POST":
            result = await content_agent.generate_blog_post(
                topic=topic or message,
                target_keyword=topic or "",
                word_count=2000
            )
            
            # Save to GitHub if configured
            from shared.github_helper import create_blog_post
            import re
            
            slug = re.sub(r'[^a-z0-9]+', '-', result.get("title", topic).lower()).strip('-')
            github_result = await create_blog_post(
                title=result.get("title", ""),
                content=result.get("content", ""),
                slug=slug,
                excerpt=result.get("meta_description", "")[:160],
                keywords=[topic] if topic else [],
                meta_description=result.get("meta_description", ""),
                author="SAMA Content Agent"
            )
            
            if github_result.get("success"):
                response_text = f"✅ Created blog post: '{result.get('title')}'\n\n📊 {result.get('word_count', 0)} words\n🔗 Will be live at: successifier.com/blog/{slug}\n\nVercel is deploying now (~2 min)"
            else:
                response_text = f"✅ Generated blog post: '{result.get('title')}' ({result.get('word_count', 0)} words)\n\n⚠️ GitHub push failed: {github_result.get('error', 'Unknown error')}\n\nContent saved to Supabase as draft."
            
            await save_message("content", "agent", response_text, user_id)
            return {"response": response_text}
        
        elif action == "CREATE_COMPARISON":
            if not competitor:
                return {
                    "response": "Please specify which competitor you want to compare with (e.g., 'Create comparison for Gainsight')"
                }
            
            result = await content_agent.generate_comparison_page(competitor=competitor.lower())
            
            # Save to GitHub
            from shared.github_helper import create_comparison_page
            github_result = await create_comparison_page(
                competitor=competitor.lower(),
                content=result.get("content", "")
            )
            
            if github_result.get("success"):
                response_text = f"✅ Created comparison page: Successifier vs {competitor.title()}\n\n🔗 Will be live at: successifier.com/vs/{competitor.lower()}\n\nVercel is deploying now (~2 min)"
            else:
                response_text = f"✅ Generated comparison page for {competitor.title()}\n\n⚠️ GitHub push failed: {github_result.get('error', 'Unknown error')}\n\nContent saved to Supabase as draft."
            
            await save_message("content", "agent", response_text, user_id)
            return {"response": response_text}
        
        elif action == "LIST_CONTENT":
            # List all saved content
            response_lines = [f"📚 **Content Library** ({len(saved_content)} pieces):\n"]
            
            for cp in saved_content:
                status_emoji = "✅" if cp.get("status") == "published" else "📝"
                title = cp.get("title", "Untitled")
                content_type = cp.get("type", "unknown")
                word_count = cp.get("word_count", 0)
                status = cp.get("status", "draft")
                
                response_lines.append(f"{status_emoji} **{title}**")
                response_lines.append(f"   Type: {content_type} | Status: {status} | {word_count} words\n")
            
            response_text = "\n".join(response_lines)
            await save_message("content", "agent", response_text, user_id)
            return {"response": response_text}
        
        elif action == "PUBLISH_CONTENT":
            if not content_title:
                response_text = "Please specify which content to publish (e.g., 'Publish Successifier vs Gainsight')"
                await save_message("content", "agent", response_text, user_id)
                return {"response": response_text}
            
            # Find content by title
            matching = [cp for cp in saved_content if content_title.lower() in cp.get("title", "").lower()]
            
            if not matching:
                response_text = f"❌ Could not find content with title containing '{content_title}'"
                await save_message("content", "agent", response_text, user_id)
                return {"response": response_text}
            
            content = matching[0]
            
            # Update status to published
            sb.table("content_pieces").update({"status": "published"}).eq("id", content["id"]).execute()
            
            response_text = f"✅ Published: **{content.get('title')}**\n\nStatus changed from draft to published."
            await save_message("content", "agent", response_text, user_id)
            return {"response": response_text}
        
        elif action == "REPUBLISH_CONTENT":
            # Re-publish existing content to GitHub
            # Check if user wants to republish all content
            if "all" in message.lower() or "alla" in message.lower():
                comparison_pages = [cp for cp in saved_content if cp.get("type") == "comparison" or "vs" in cp.get("title", "").lower()]
                
                if not comparison_pages:
                    response_text = "❌ No comparison pages found to republish."
                    await save_message("content", "agent", response_text, user_id)
                    return {"response": response_text}
                
                results = []
                for cp in comparison_pages:
                    # Extract competitor from title
                    title = cp.get("title", "")
                    import re
                    match = re.search(r'vs\s+(\w+)', title, re.IGNORECASE)
                    if match:
                        comp = match.group(1).lower()
                        
                        # Regenerate and push to GitHub
                        result = await content_agent.generate_comparison_page(competitor=comp)
                        from shared.github_helper import create_comparison_page
                        github_result = await create_comparison_page(comp, result.get("content", ""))
                        
                        if github_result.get("success"):
                            results.append(f"✅ {title}")
                        else:
                            results.append(f"❌ {title}: {github_result.get('error', 'Unknown error')}")
                
                response_text = f"📤 **Republished {len(results)} pages:**\n\n" + "\n".join(results) + "\n\nVercel is deploying now (~2 min)"
                await save_message("content", "agent", response_text, user_id)
                return {"response": response_text}
            
            # Single page republish
            matching = [cp for cp in saved_content if (content_title and content_title.lower() in cp.get("title", "").lower()) or (competitor and competitor.lower() in cp.get("title", "").lower())]
            
            if not matching:
                response_text = f"❌ Could not find content to republish"
                await save_message("content", "agent", response_text, user_id)
                return {"response": response_text}
            
            content = matching[0]
            title = content.get("title", "")
            
            # Extract competitor from title
            import re
            match = re.search(r'vs\s+(\w+)', title, re.IGNORECASE)
            if match:
                comp = match.group(1).lower()
                
                # Regenerate and push to GitHub
                result = await content_agent.generate_comparison_page(competitor=comp)
                from shared.github_helper import create_comparison_page
                github_result = await create_comparison_page(comp, result.get("content", ""))
                
                if github_result.get("success"):
                    response_text = f"✅ Republished: **{title}**\n\n🔗 Will be live at: successifier.com/vs/{comp}\n\nVercel is deploying now (~2 min)"
                else:
                    response_text = f"❌ Failed to push to GitHub: {github_result.get('error', 'Unknown error')}"
                
                await save_message("content", "agent", response_text, user_id)
                return {"response": response_text}
            else:
                response_text = f"❌ Could not extract competitor name from title: {title}"
                await save_message("content", "agent", response_text, user_id)
                return {"response": response_text}
        
        elif action == "DELETE_CONTENT":
            if not content_title:
                response_text = "Please specify which content to delete (e.g., 'Delete Successifier vs Gainsight')"
                await save_message("content", "agent", response_text, user_id)
                return {"response": response_text}
            
            # Find content by title
            matching = [cp for cp in saved_content if content_title.lower() in cp.get("title", "").lower()]
            
            if not matching:
                response_text = f"❌ Could not find content with title containing '{content_title}'"
                await save_message("content", "agent", response_text, user_id)
                return {"response": response_text}
            
            content = matching[0]
            
            # Delete from database
            sb.table("content_pieces").delete().eq("id", content["id"]).execute()
            
            response_text = f"🗑️ Deleted: **{content.get('title')}**\n\nContent removed from database."
            await save_message("content", "agent", response_text, user_id)
            return {"response": response_text}
        
        elif action == "ANALYZE_GAPS":
            response_text = "📊 To analyze content gaps, click 'Run Content Analysis' button above. This will:\n\n1. Check existing content in Supabase\n2. Analyze SEO keywords from GSC\n3. Identify missing competitor comparisons\n4. Suggest new content opportunities\n\nThe analysis will generate actionable items you can execute."
            await save_message("content", "agent", response_text, user_id)
            return {"response": response_text}
        
        else:
            # General question - let Claude answer
            answer = content_agent.client.messages.create(
                model=content_agent.model,
                max_tokens=512,
                messages=[{
                    "role": "user",
                    "content": f"""You are the Content Agent for Successifier, a customer success platform. Answer this question:

"{message}"

Be helpful, concise, and actionable. If relevant, mention what content actions you can help with."""
                }]
            )
            
            response_text = answer.content[0].text
            await save_message("content", "agent", response_text, user_id)
            return {"response": response_text}
    
    except Exception as e:
        logger.error(f"Chat error: {e}")
        return {
            "response": f"Sorry, I encountered an error: {str(e)}\n\nPlease try rephrasing your request or check backend logs."
        }
