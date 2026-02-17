"""
Content Agent - AI-Powered Content Generation
Generates blog posts, landing pages, comparison pages, and social content
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from anthropic import Anthropic
import httpx

from shared.config import settings
from shared.database import get_supabase
from .models import CONTENT_PIECES_TABLE, KEYWORDS_TABLE
from .brand_voice import brand_voice

logger = logging.getLogger(__name__)


class ContentAgent:
    """
    Content Agent responsible for:
    - Blog post generation
    - Landing page creation
    - Comparison page creation
    - Social media content
    - SEO optimization
    """
    
    def __init__(self):
        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY) if settings.ANTHROPIC_API_KEY else None
        self.model = "claude-sonnet-4-20250514"
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.brand_voice = brand_voice
        self.settings = settings
        self.sb = None
    
    def _get_sb(self):
        if not self.sb:
            self.sb = get_supabase()
        return self.sb
    
    async def generate_blog_post(
        self,
        topic: str,
        target_keyword: Optional[str] = None,
        word_count: int = 2000,
        pillar: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate a complete blog post
        
        Args:
            topic: Blog post topic
            target_keyword: Primary keyword to target
            word_count: Target word count (1500-2500)
            pillar: Content pillar (churn_prevention, health_scoring, etc.)
        
        Returns:
            Generated blog post with metadata
        """
        logger.info(f"📝 Generating blog post: {topic}")
        
        # Get brand voice system prompt
        system_prompt = brand_voice.get_system_prompt("blog")
        
        # Build user prompt
        user_prompt = f"""Write a comprehensive blog post about: {topic}

Target word count: {word_count} words
"""
        
        if target_keyword:
            user_prompt += f"\nPrimary keyword to target: {target_keyword}"
        
        if pillar:
            pillar_info = brand_voice.CONTENT_PILLARS.get(pillar, {})
            user_prompt += f"\n\nContent pillar: {pillar_info.get('title', pillar)}"
        
        user_prompt += """

Structure:
1. Compelling headline (H1)
2. Hook paragraph (grab attention immediately)
3. Main content with H2 and H3 subheadings
4. Specific examples and data points
5. Key takeaways section
6. Strong CTA

Include:
- Successifier proof points where relevant
- Practical, actionable advice
- Real-world examples
- Data and statistics

Format as markdown with proper headings.
"""
        
        # Generate content
        response = self.client.messages.create(
            model=self.model,
            max_tokens=8192,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        
        content = response.content[0].text
        
        # Extract title from content
        lines = content.split('\n')
        title = lines[0].replace('#', '').strip() if lines else topic
        
        # Generate meta description
        meta_description = await self._generate_meta_description(title, content)
        
        # Validate against brand voice
        validation = brand_voice.validate_content(content)
        
        # Save to database
        saved = await self._save_content(
            title=title,
            content=content,
            content_type="blog",
            target_keyword=target_keyword,
            meta_description=meta_description,
            word_count=validation['word_count']
        )
        
        # Notify SEO Agent of new content
        try:
            from shared.event_bus import event_bus
            if target_keyword:
                await event_bus.publish(
                    event_type="content_published",
                    target_agent="sama_seo",
                    data={
                        "content_id": saved.get("id", ""),
                        "title": title,
                        "keyword": target_keyword,
                        "word_count": validation['word_count']
                    }
                )
        except Exception:
            pass
        
        logger.info(f"✅ Blog post generated: {title} ({validation['word_count']} words)")
        
        return {
            "id": saved.get("id", ""),
            "title": title,
            "content": content,
            "meta_description": meta_description,
            "word_count": validation['word_count'],
            "validation": validation,
            "status": "draft"
        }
    
    async def generate_landing_page(
        self,
        topic: str,
        target_keyword: str,
        use_case: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate a conversion-focused landing page
        
        Args:
            topic: Landing page topic
            target_keyword: Primary keyword
            use_case: Specific use case to highlight
        
        Returns:
            Generated landing page
        """
        logger.info(f"🎯 Generating landing page: {topic}")
        
        system_prompt = brand_voice.get_system_prompt("landing_page")
        
        user_prompt = f"""Create a high-converting landing page for: {topic}

Primary keyword: {target_keyword}
"""
        
        if use_case:
            user_prompt += f"\nUse case focus: {use_case}"
        
        user_prompt += """

Structure:
1. Headline (value proposition)
2. Subheadline (expand on value)
3. Hero section (key benefits)
4. Problem section (pain points)
5. Solution section (how Successifier solves it)
6. Features/Benefits (3-5 key features)
7. Social proof (proof points)
8. CTA section
9. FAQ section (3-5 questions)

Requirements:
- Clear, benefit-focused copy
- Scannable format (bullets, short paragraphs)
- Multiple CTAs
- Include all Successifier proof points
- Address target persona pain points

Format as markdown.
"""
        
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        
        content = response.content[0].text
        lines = content.split('\n')
        title = lines[0].replace('#', '').strip() if lines else topic
        
        meta_description = await self._generate_meta_description(title, content)
        validation = brand_voice.validate_content(content)
        
        saved = await self._save_content(
            title=title,
            content=content,
            content_type="landing_page",
            target_keyword=target_keyword,
            meta_description=meta_description,
            word_count=validation['word_count']
        )
        
        logger.info(f"✅ Landing page generated: {title}")
        
        return {
            "id": saved.get("id", ""),
            "title": title,
            "content": content,
            "meta_description": meta_description,
            "validation": validation,
            "status": "draft"
        }
    
    async def generate_comparison_page(
        self,
        competitor: str
    ) -> Dict[str, Any]:
        """
        Generate a comparison page (Successifier vs Competitor)
        
        Args:
            competitor: Competitor name (gainsight, totango, churnzero)
        
        Returns:
            Generated comparison page
        """
        logger.info(f"⚖️ Generating comparison page: Successifier vs {competitor}")
        
        system_prompt = brand_voice.get_system_prompt("comparison")
        
        user_prompt = f"""Create a comprehensive comparison page: Successifier vs {competitor.title()}

Structure:
1. Headline: "Successifier vs {competitor.title()}: Which Customer Success Platform is Right for You?"
2. Executive Summary (key differences)
3. Feature Comparison Table
4. Pricing Comparison
5. Use Case Comparison (who each is best for)
6. Why Choose Successifier section
7. Migration Guide (switching from {competitor})
8. FAQ
9. CTA

Requirements:
- Fair but favorable to Successifier
- Specific feature comparisons
- Real pricing data
- Highlight Successifier advantages:
  * AI-native (not retrofitted)
  * Affordable pricing (from $79/month vs enterprise pricing)
  * Fast setup (30 minutes vs weeks)
  * Better for small-to-mid teams
- Address common objections
- Include proof points

Format as markdown.
"""
        
        response = self.client.messages.create(
            model=self.model,
            max_tokens=8192,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        
        content = response.content[0].text
        title = f"Successifier vs {competitor.title()}"
        target_keyword = f"{competitor} alternative"
        
        meta_description = await self._generate_meta_description(title, content)
        validation = brand_voice.validate_content(content)
        
        saved = await self._save_content(
            title=title,
            content=content,
            content_type="comparison",
            target_keyword=target_keyword,
            meta_description=meta_description,
            word_count=validation['word_count'],
            target_url=f"/vs/{competitor.lower()}"
        )
        
        logger.info(f"✅ Comparison page generated: {title}")
        
        return {
            "id": saved.get("id", ""),
            "title": title,
            "content": content,
            "meta_description": meta_description,
            "validation": validation,
            "target_url": f"/vs/{competitor.lower()}",
            "status": "draft"
        }
    
    async def generate_social_post(
        self,
        topic: str,
        platform: str = "twitter",
        style: str = "educational"
    ) -> Dict[str, Any]:
        """
        Generate social media post
        
        Args:
            topic: Post topic
            platform: Social platform (twitter, linkedin)
            style: Post style (educational, announcement, engagement)
        
        Returns:
            Generated social post
        """
        logger.info(f"📱 Generating {platform} post: {topic}")
        
        system_prompt = brand_voice.get_system_prompt("blog")  # Use blog voice
        
        if platform == "twitter":
            user_prompt = f"""Create a Twitter/X post about: {topic}

Style: {style}

Requirements:
- 1-3 tweets (thread if needed)
- Engaging hook
- Valuable insight or data
- Clear takeaway
- Optional CTA
- Use emojis sparingly (1-2 max)
- No hashtags unless highly relevant

Tone: Professional but conversational. Data-driven. Insightful.
"""
        else:  # LinkedIn
            user_prompt = f"""Create a LinkedIn post about: {topic}

Style: {style}

Requirements:
- 150-300 words
- Strong opening hook
- Valuable insight
- Personal or data-driven angle
- Clear takeaway
- Subtle CTA
- Professional tone

Format: Plain text, no special formatting.
"""
        
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        
        content = response.content[0].text.strip()
        
        # Save as content piece
        saved = await self._save_content(
            title=f"{platform.title()} post: {topic}",
            content=content,
            content_type="social",
            word_count=len(content.split())
        )
        
        logger.info(f"✅ Social post generated for {platform}")
        
        return {
            "id": saved.get("id", ""),
            "platform": platform,
            "content": content,
            "status": "draft"
        }
    
    async def optimize_for_seo(
        self,
        content_id: str,
        target_keyword: str
    ) -> Dict[str, Any]:
        """
        Optimize existing content for SEO
        
        Args:
            content_id: Content piece ID
            target_keyword: Keyword to optimize for
        
        Returns:
            Optimization results
        """
        logger.info(f"🔍 Optimizing content for: {target_keyword}")
        
        sb = self._get_sb()
        result = sb.table(CONTENT_PIECES_TABLE).select("*").eq("id", content_id).execute()
        
        if not result.data:
            raise ValueError(f"Content piece {content_id} not found")
        
        piece = result.data[0]
        
        # Analyze current content
        analysis = await self._analyze_seo(piece["content"], target_keyword)
        
        # Generate optimized version if needed
        if analysis['keyword_density'] < 0.5 or analysis['keyword_density'] > 2.5:
            optimized_content = await self._optimize_content(
                piece["content"],
                target_keyword,
                analysis
            )
            
            sb.table(CONTENT_PIECES_TABLE).update({
                "content": optimized_content,
                "target_keyword": target_keyword
            }).eq("id", content_id).execute()
            
            logger.info(f"✅ Content optimized for {target_keyword}")
        
        return analysis
    
    async def _generate_meta_description(self, title: str, content: str) -> str:
        """Generate SEO-optimized meta description"""
        prompt = f"""Write a compelling meta description (150-160 characters) for this content:

Title: {title}

Content preview: {content[:500]}...

Requirements:
- 150-160 characters
- Include primary value proposition
- Include CTA or benefit
- Engaging and click-worthy
"""
        
        response = self.client.messages.create(
            model=self.model,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}]
        )
        
        return response.content[0].text.strip()
    
    async def _analyze_seo(self, content: str, keyword: str) -> Dict[str, Any]:
        """Analyze content for SEO"""
        content_lower = content.lower()
        keyword_lower = keyword.lower()
        
        # Count keyword occurrences
        keyword_count = content_lower.count(keyword_lower)
        word_count = len(content.split())
        keyword_density = (keyword_count / word_count * 100) if word_count > 0 else 0
        
        # Check keyword placement
        in_title = keyword_lower in content[:200].lower()
        in_first_paragraph = keyword_lower in content[:500].lower()
        
        return {
            "keyword_count": keyword_count,
            "keyword_density": round(keyword_density, 2),
            "in_title": in_title,
            "in_first_paragraph": in_first_paragraph,
            "word_count": word_count,
            "optimal": 0.5 <= keyword_density <= 2.5 and in_title and in_first_paragraph
        }
    
    async def _optimize_content(
        self,
        content: str,
        keyword: str,
        analysis: Dict[str, Any]
    ) -> str:
        """Optimize content for keyword"""
        # This would use Claude to rewrite sections
        # For now, return original content
        return content
    
    async def _save_content(
        self,
        title: str,
        content: str,
        content_type: str,
        target_keyword: Optional[str] = None,
        meta_description: Optional[str] = None,
        word_count: int = 0,
        target_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """Save content to Supabase"""
        sb = self._get_sb()
        
        record = {
            "title": title,
            "content": content,
            "content_type": content_type,
            "target_keyword": target_keyword,
            "meta_title": title[:200] if len(title) > 200 else title,
            "meta_description": meta_description,
            "word_count": word_count,
            "target_url": target_url,
            "status": "draft",
            "created_at": datetime.utcnow().isoformat()
        }
        
        result = sb.table(CONTENT_PIECES_TABLE).insert(record).execute()
        return result.data[0] if result.data else record


# Global content agent instance
content_agent = ContentAgent()
