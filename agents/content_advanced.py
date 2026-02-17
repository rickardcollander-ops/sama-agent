"""
Advanced Content Generation Features
Pillar pages, FAQ pages, content briefs, 30-day refresh workflow
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from anthropic import Anthropic
import logging

from shared.config import settings
from shared.database import get_supabase
from agents.brand_voice import brand_voice
from agents.seo_schema import schema_generator

logger = logging.getLogger(__name__)


class AdvancedContentGenerator:
    """Advanced content generation capabilities"""
    
    def __init__(self):
        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY) if settings.ANTHROPIC_API_KEY else None
        self.model = "claude-sonnet-4-20250514"
        self.sb = None
    
    def _get_sb(self):
        if not self.sb:
            self.sb = get_supabase()
        return self.sb
    
    async def generate_pillar_page(
        self,
        topic: str,
        target_keyword: str,
        pillar: str,
        subtopics: List[str]
    ) -> Dict[str, Any]:
        """
        Generate comprehensive pillar page (3000-5000 words)
        
        Args:
            topic: Main topic for pillar page
            target_keyword: Primary keyword to target
            pillar: Content pillar category
            subtopics: List of subtopics to cover
        """
        if not self.client:
            return {"error": "Anthropic API key not configured"}
        
        system_prompt = f"""You are an expert content writer for Successifier, a Customer Success platform.

Brand Voice: {brand_voice['tone']}
Content Pillar: {pillar}

Generate a comprehensive pillar page (3000-5000 words) that serves as the ultimate guide on this topic.

Requirements:
- Target keyword: {target_keyword}
- Cover all subtopics: {', '.join(subtopics)}
- Include detailed sections with examples
- Add actionable takeaways
- Use clear headings (H2, H3)
- Include statistics and data points
- Add internal linking opportunities
- SEO-optimized meta description
- Engaging introduction and conclusion

Format as JSON with:
{{
  "title": "SEO-optimized title with keyword",
  "meta_description": "155 characters max",
  "introduction": "Hook paragraph",
  "sections": [
    {{
      "heading": "H2 heading",
      "content": "Section content",
      "subsections": [
        {{"heading": "H3", "content": "..."}}
      ]
    }}
  ],
  "conclusion": "Summary and CTA",
  "internal_links": ["suggested anchor texts"],
  "word_count": 0
}}"""
        
        user_prompt = f"""Create a pillar page about: {topic}

Subtopics to cover:
{chr(10).join(f'- {st}' for st in subtopics)}

Make it comprehensive, actionable, and SEO-optimized."""
        
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=8000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}]
            )
            
            import json
            result = json.loads(response.content[0].text)
            
            # Generate schema markup
            article_schema = schema_generator.generate_article_schema(
                title=result["title"],
                description=result["meta_description"],
                author="Successifier Team",
                date_published=datetime.utcnow()
            )
            
            # Save to database
            sb = self._get_sb()
            content_data = {
                "type": "pillar_page",
                "title": result["title"],
                "content": json.dumps(result),
                "meta_description": result["meta_description"],
                "target_keyword": target_keyword,
                "pillar": pillar,
                "word_count": result.get("word_count", 0),
                "schema_markup": article_schema,
                "status": "draft",
                "created_at": datetime.utcnow().isoformat()
            }
            
            sb.table("content_library").insert(content_data).execute()
            
            return {
                "success": True,
                "type": "pillar_page",
                **result,
                "schema_markup": article_schema
            }
            
        except Exception as e:
            logger.error(f"Failed to generate pillar page: {e}")
            return {"error": str(e)}
    
    async def generate_faq_page(
        self,
        topic: str,
        target_keyword: str,
        num_questions: int = 10
    ) -> Dict[str, Any]:
        """
        Generate FAQ page with schema markup
        
        Args:
            topic: Topic for FAQ page
            target_keyword: SEO keyword
            num_questions: Number of Q&A pairs to generate
        """
        if not self.client:
            return {"error": "Anthropic API key not configured"}
        
        system_prompt = f"""You are an expert at creating FAQ pages for Successifier.

Brand Voice: {brand_voice['tone']}

Generate {num_questions} frequently asked questions and detailed answers about {topic}.

Requirements:
- Questions should be natural, conversational
- Answers should be comprehensive (100-200 words each)
- Include keyword: {target_keyword}
- Address common pain points
- Provide actionable information

Format as JSON:
{{
  "title": "FAQ: [topic]",
  "meta_description": "...",
  "faqs": [
    {{"question": "...", "answer": "..."}}
  ]
}}"""
        
        user_prompt = f"Generate FAQ page about: {topic}"
        
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}]
            )
            
            import json
            result = json.loads(response.content[0].text)
            
            # Generate FAQ schema
            faq_schema = schema_generator.generate_faq_schema(result["faqs"])
            
            # Save to database
            sb = self._get_sb()
            content_data = {
                "type": "faq_page",
                "title": result["title"],
                "content": json.dumps(result),
                "meta_description": result["meta_description"],
                "target_keyword": target_keyword,
                "schema_markup": faq_schema,
                "status": "draft",
                "created_at": datetime.utcnow().isoformat()
            }
            
            sb.table("content_library").insert(content_data).execute()
            
            return {
                "success": True,
                "type": "faq_page",
                **result,
                "schema_markup": faq_schema
            }
            
        except Exception as e:
            logger.error(f"Failed to generate FAQ page: {e}")
            return {"error": str(e)}
    
    async def generate_content_brief(
        self,
        keyword: str,
        search_intent: str,
        competitor_urls: List[str]
    ) -> Dict[str, Any]:
        """
        Generate content brief for writers
        
        Args:
            keyword: Target keyword
            search_intent: User intent (informational, commercial, etc.)
            competitor_urls: Top ranking competitor URLs
        """
        if not self.client:
            return {"error": "Anthropic API key not configured"}
        
        system_prompt = """You are a content strategist creating detailed content briefs.

Generate a comprehensive content brief that a writer can use to create high-quality content.

Format as JSON:
{
  "target_keyword": "...",
  "search_intent": "...",
  "recommended_word_count": 0,
  "title_suggestions": ["...", "...", "..."],
  "outline": [
    {"heading": "H2", "talking_points": ["...", "..."]}
  ],
  "keywords_to_include": ["...", "..."],
  "questions_to_answer": ["...", "..."],
  "competitor_gaps": ["...", "..."],
  "unique_angles": ["...", "..."],
  "cta_suggestions": ["...", "..."]
}"""
        
        user_prompt = f"""Create content brief for:
Keyword: {keyword}
Search Intent: {search_intent}
Competitor URLs: {', '.join(competitor_urls)}

Analyze what competitors are doing and suggest how to create better content."""
        
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=3000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}]
            )
            
            import json
            result = json.loads(response.content[0].text)
            
            return {
                "success": True,
                **result
            }
            
        except Exception as e:
            logger.error(f"Failed to generate content brief: {e}")
            return {"error": str(e)}
    
    async def identify_content_for_refresh(self) -> List[Dict[str, Any]]:
        """
        Identify content that needs refreshing (30+ days old)
        """
        sb = self._get_sb()
        
        # Get published content older than 30 days
        cutoff_date = (datetime.utcnow() - timedelta(days=30)).isoformat()
        
        result = sb.table("content_library")\
            .select("*")\
            .eq("status", "published")\
            .lt("last_updated", cutoff_date)\
            .order("last_updated", desc=False)\
            .limit(20)\
            .execute()
        
        content_to_refresh = result.data if result.data else []
        
        return content_to_refresh
    
    async def refresh_content(self, content_id: str) -> Dict[str, Any]:
        """
        Refresh existing content with updated information
        
        Args:
            content_id: ID of content to refresh
        """
        if not self.client:
            return {"error": "Anthropic API key not configured"}
        
        sb = self._get_sb()
        
        # Get existing content
        result = sb.table("content_library")\
            .select("*")\
            .eq("id", content_id)\
            .single()\
            .execute()
        
        if not result.data:
            return {"error": "Content not found"}
        
        existing_content = result.data
        
        system_prompt = f"""You are refreshing existing content for Successifier.

Original content:
Title: {existing_content.get('title')}
Target Keyword: {existing_content.get('target_keyword')}

Your task:
1. Update statistics and data points
2. Add new insights and trends
3. Improve SEO optimization
4. Enhance readability
5. Add new examples
6. Keep the core message and structure

Format as JSON with updated content."""
        
        user_prompt = f"""Refresh this content:

{existing_content.get('content')}

Make it current, relevant, and improved while maintaining the original intent."""
        
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}]
            )
            
            import json
            refreshed = json.loads(response.content[0].text)
            
            # Update in database
            sb.table("content_library")\
                .update({
                    "content": json.dumps(refreshed),
                    "last_updated": datetime.utcnow().isoformat(),
                    "refresh_count": existing_content.get("refresh_count", 0) + 1
                })\
                .eq("id", content_id)\
                .execute()
            
            return {
                "success": True,
                "content_id": content_id,
                "refreshed_at": datetime.utcnow().isoformat(),
                **refreshed
            }
            
        except Exception as e:
            logger.error(f"Failed to refresh content: {e}")
            return {"error": str(e)}


# Global instance
advanced_content_generator = AdvancedContentGenerator()
