"""
Content Agent Chat Endpoint
Allows natural language interaction with the Content agent
"""

from fastapi import APIRouter, Body, HTTPException
from typing import Dict, Any
from agents.content import content_agent
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


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
    
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")
    
    try:
        # Use Claude to interpret the request and route to appropriate action
        if not content_agent.client:
            return {
                "response": "Content agent is not configured. Please set ANTHROPIC_API_KEY in Railway."
            }
        
        # Ask Claude to interpret the user's request
        interpretation = content_agent.client.messages.create(
            model=content_agent.model,
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": f"""You are the Content Agent for Successifier. A user has sent you this message:

"{message}"

Interpret their request and respond with ONE of these actions:
1. CREATE_BLOG_POST - if they want to create a blog post
2. CREATE_COMPARISON - if they want to create a competitor comparison page
3. ANALYZE_GAPS - if they want to analyze content gaps
4. GENERAL_QUESTION - if they're asking a general question

Also extract key parameters:
- topic: the main topic/keyword
- competitor: competitor name (if applicable)

Respond in this exact format:
ACTION: [action type]
TOPIC: [topic]
COMPETITOR: [competitor name or N/A]
EXPLANATION: [brief explanation of what you'll do]"""
            }]
        )
        
        response_text = interpretation.content[0].text
        
        # Parse Claude's response
        lines = response_text.strip().split('\n')
        action = None
        topic = None
        competitor = None
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
                return {
                    "response": f"✅ Created blog post: '{result.get('title')}'\n\n📊 {result.get('word_count', 0)} words\n🔗 Will be live at: successifier.com/blog/{slug}\n\nVercel is deploying now (~2 min)"
                }
            else:
                return {
                    "response": f"✅ Generated blog post: '{result.get('title')}' ({result.get('word_count', 0)} words)\n\n⚠️ GitHub push failed: {github_result.get('error', 'Unknown error')}\n\nContent saved to Supabase as draft."
                }
        
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
                return {
                    "response": f"✅ Created comparison page: Successifier vs {competitor.title()}\n\n🔗 Will be live at: successifier.com/vs/{competitor.lower()}\n\nVercel is deploying now (~2 min)"
                }
            else:
                return {
                    "response": f"✅ Generated comparison page for {competitor.title()}\n\n⚠️ GitHub push failed: {github_result.get('error', 'Unknown error')}\n\nContent saved to Supabase as draft."
                }
        
        elif action == "ANALYZE_GAPS":
            return {
                "response": "📊 To analyze content gaps, click 'Run Content Analysis' button above. This will:\n\n1. Check existing content in Supabase\n2. Analyze SEO keywords from GSC\n3. Identify missing competitor comparisons\n4. Suggest new content opportunities\n\nThe analysis will generate actionable items you can execute."
            }
        
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
            
            return {
                "response": answer.content[0].text
            }
    
    except Exception as e:
        logger.error(f"Chat error: {e}")
        return {
            "response": f"Sorry, I encountered an error: {str(e)}\n\nPlease try rephrasing your request or check backend logs."
        }
