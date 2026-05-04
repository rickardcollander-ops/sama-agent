"""
Content Agent - AI-Powered Content Generation
Generates blog posts, landing pages, comparison pages, and social content
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from anthropic import Anthropic
import httpx

from shared.config import settings
from shared.database import get_supabase
from .models import CONTENT_PIECES_TABLE, KEYWORDS_TABLE, COMPETITOR_ANALYSES_TABLE
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
    
    def __init__(self, tenant_config=None):
        self.tenant_config = tenant_config
        api_key = tenant_config.anthropic_api_key if tenant_config else settings.ANTHROPIC_API_KEY
        self.client = Anthropic(api_key=api_key) if api_key else None
        self.model = "claude-sonnet-4-6"
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.brand_voice = brand_voice
        self.settings = settings
        self.sb = None
    
    def _get_sb(self):
        if not self.sb:
            self.sb = get_supabase()
        return self.sb
    
    async def run_cycle(self) -> str:
        """Run a content cycle: analyze gaps and generate a blog post."""
        gaps = await self.analyze_competitor_content_gaps()
        gap_list = gaps.get("gaps", [])
        if gap_list:
            top = gap_list[0]
            topic = top.get("title", "Industry Insights")
            keyword = top.get("target_keyword")
            result = await self.generate_blog_post(topic=topic, target_keyword=keyword)
            return f"Generated blog post: {result.get('title', topic)}"
        else:
            result = await self.generate_blog_post(topic="Industry Best Practices")
            return f"Generated blog post: {result.get('title', 'Industry Best Practices')}"

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
        def _call():
            return self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}]
            )
        response = await asyncio.to_thread(_call)

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
        
        def _call():
            return self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}]
            )
        response = await asyncio.to_thread(_call)

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
        
        def _call():
            return self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}]
            )
        response = await asyncio.to_thread(_call)

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
        
        def _call():
            return self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}]
            )
        response = await asyncio.to_thread(_call)

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
        
        def _call():
            return self.client.messages.create(
                model=self.model,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}]
            )
        response = await asyncio.to_thread(_call)

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
        """Optimize content for a target keyword using Claude.

        Takes the current SEO analysis (keyword density, placement flags) and
        rewrites the content to hit optimal density, improve heading structure,
        and suggest internal link anchors -- all while preserving the original
        meaning and brand voice.
        """
        if not self.client:
            logger.warning("No Anthropic client configured; skipping content optimization")
            return content

        system_prompt = self.brand_voice.get_system_prompt("blog")

        issues: List[str] = []
        if analysis.get("keyword_density", 0) < 0.5:
            issues.append(
                f"Keyword density is too low ({analysis['keyword_density']}%). "
                "Add more natural mentions of the target keyword."
            )
        elif analysis.get("keyword_density", 0) > 2.5:
            issues.append(
                f"Keyword density is too high ({analysis['keyword_density']}%). "
                "Reduce keyword stuffing while keeping the content relevant."
            )
        if not analysis.get("in_title"):
            issues.append("The target keyword is missing from the title/H1. Include it naturally.")
        if not analysis.get("in_first_paragraph"):
            issues.append("The target keyword does not appear in the first paragraph. Add it early.")

        # Fetch existing content titles for internal-link suggestions
        internal_pages: List[str] = []
        try:
            sb = self._get_sb()
            pages_result = sb.table(CONTENT_PIECES_TABLE).select("title,target_url").limit(50).execute()
            for page in (pages_result.data or []):
                title = page.get("title", "")
                url = page.get("target_url", "")
                if title and url:
                    internal_pages.append(f"- \"{title}\" ({url})")
        except Exception:
            pass

        internal_links_section = ""
        if internal_pages:
            internal_links_section = (
                "\n\nINTERNAL PAGES AVAILABLE FOR LINKING:\n"
                + "\n".join(internal_pages[:20])
                + "\n\nWhere relevant, add markdown links to these internal pages using "
                "natural anchor text. Do not force links where they don't fit."
            )

        user_prompt = f"""Optimize the following content for the target keyword: "{keyword}"

SEO ISSUES TO FIX:
{chr(10).join("- " + issue for issue in issues) if issues else "- General optimization needed for keyword relevance."}

OPTIMIZATION REQUIREMENTS:
1. Achieve a keyword density between 0.8% and 1.5% for "{keyword}" using natural phrasing.
2. Ensure "{keyword}" appears in the H1/title, first paragraph, at least one H2 subheading, and the conclusion.
3. Improve heading hierarchy (H1 -> H2 -> H3) for better SEO structure.
4. Add semantic variations and related terms for "{keyword}" throughout.
5. Keep the same overall structure, meaning, and tone -- do not shorten the content.
6. Preserve all existing data points, proof points, and CTAs.{internal_links_section}

ORIGINAL CONTENT:
{content}

Return ONLY the optimized content in markdown format. Do not include any commentary or explanation."""

        try:
            def _call():
                return self.client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}]
                )
            response = await asyncio.to_thread(_call)
            optimized = response.content[0].text.strip()

            # Validate the optimization didn't drastically shrink the content
            original_words = len(content.split())
            optimized_words = len(optimized.split())
            if optimized_words < original_words * 0.7:
                logger.warning(
                    "Optimized content is significantly shorter than original "
                    f"({optimized_words} vs {original_words} words); keeping original"
                )
                return content

            logger.info(
                f"Content optimized for '{keyword}': "
                f"{original_words} -> {optimized_words} words"
            )
            return optimized

        except Exception as e:
            logger.error(f"Failed to optimize content for '{keyword}': {e}")
            return content
    
    # ------------------------------------------------------------------ #
    #  Competitor content themes used for gap analysis                     #
    # ------------------------------------------------------------------ #
    COMPETITOR_CONTENT_THEMES: Dict[str, Dict[str, List[str]]] = {
        "gainsight": {
            "name": "Gainsight",
            "themes": [
                "customer success management",
                "customer health scoring",
                "net revenue retention",
                "customer lifecycle management",
                "product adoption analytics",
                "customer journey orchestration",
                "cs ops and strategy",
                "customer success qualified leads",
                "digital customer success",
                "voice of the customer",
                "community-led growth",
                "renewal management",
                "customer 360 view",
            ],
        },
        "totango": {
            "name": "Totango",
            "themes": [
                "customer success software",
                "successbloc templates",
                "customer health monitoring",
                "customer onboarding automation",
                "churn prediction",
                "customer segmentation",
                "cs workflow automation",
                "account-based customer success",
                "proactive customer engagement",
                "time to value optimization",
                "customer success KPIs",
            ],
        },
        "churnzero": {
            "name": "ChurnZero",
            "themes": [
                "churn reduction strategies",
                "real-time customer alerts",
                "customer engagement scoring",
                "in-app communication",
                "customer success automation",
                "usage tracking and analytics",
                "SaaS retention strategies",
                "customer success playbooks",
                "renewal forecasting",
                "product usage analytics",
                "customer health dashboards",
            ],
        },
    }

    async def analyze_competitor_content_gaps(self) -> Dict[str, Any]:
        """Analyze content gaps relative to competitor themes and SEO keyword data.

        Returns a structured result containing:
        - ``gaps``: list of actionable gap records, each with a recommended
          content type, title, target keyword, and rationale.
        - ``coverage``: per-competitor coverage percentage.
        - ``summary``: human-readable summary string.
        """
        logger.info("Analyzing competitor content gaps")

        sb = self._get_sb()

        # -- 1. Fetch our existing content --------------------------------- #
        try:
            cp_result = sb.table(CONTENT_PIECES_TABLE).select(
                "id,title,target_keyword,content_type,word_count,status"
            ).limit(200).execute()
            content_pieces = cp_result.data or []
        except Exception as e:
            logger.error(f"Failed to fetch content pieces: {e}")
            content_pieces = []

        # Build a searchable blob of all our content titles + keywords
        our_content_text = " ".join(
            (cp.get("title", "") + " " + (cp.get("target_keyword", "") or "")).lower()
            for cp in content_pieces
        )

        # -- 2. Fetch SEO keywords from GSC data -------------------------- #
        try:
            kw_result = sb.table(KEYWORDS_TABLE).select("*").execute()
            seo_keywords = kw_result.data or []
        except Exception as e:
            logger.error(f"Failed to fetch SEO keywords: {e}")
            seo_keywords = []

        # Index keywords by normalised keyword text for fast lookup
        kw_index: Dict[str, Dict[str, Any]] = {}
        for kw in seo_keywords:
            kw_text = kw.get("keyword", "").lower().strip()
            if kw_text:
                kw_index[kw_text] = kw

        # Keywords we rank for but have no dedicated content targeting
        existing_target_keywords = {
            cp.get("target_keyword", "").lower().strip()
            for cp in content_pieces
            if cp.get("target_keyword")
        }
        orphan_keywords = [
            kw for kw_text, kw in kw_index.items()
            if kw_text not in existing_target_keywords
        ]

        # -- 3. Fetch any prior competitor analyses ------------------------ #
        competitor_data: Dict[str, Dict[str, Any]] = {}
        try:
            ca_result = sb.table(COMPETITOR_ANALYSES_TABLE).select(
                "competitor,keyword_gaps,content_opportunities"
            ).execute()
            for ca in (ca_result.data or []):
                comp = ca.get("competitor", "").lower().replace(".com", "")
                competitor_data[comp] = ca
        except Exception:
            pass

        # -- 4. Compare our content against competitor themes -------------- #
        gaps: List[Dict[str, Any]] = []
        coverage: Dict[str, Dict[str, Any]] = {}

        for comp_key, comp_info in self.COMPETITOR_CONTENT_THEMES.items():
            comp_name = comp_info["name"]
            themes = comp_info["themes"]
            covered = 0
            total = len(themes)

            for theme in themes:
                theme_lower = theme.lower()
                # Check if any of our content covers this theme
                theme_words = theme_lower.split()
                has_coverage = any(
                    word in our_content_text for word in theme_words if len(word) > 4
                ) and theme_lower.split()[0] in our_content_text

                if has_coverage:
                    covered += 1
                    continue

                # This is a gap -- find the best keyword to target
                best_keyword = theme  # default to the theme itself
                best_impressions = 0
                for kw_text, kw_data in kw_index.items():
                    # Check if this keyword is semantically related to the theme
                    theme_tokens = set(theme_lower.split())
                    kw_tokens = set(kw_text.split())
                    overlap = theme_tokens & kw_tokens
                    if len(overlap) >= 1 and len(overlap) / len(theme_tokens) >= 0.3:
                        imps = kw_data.get("current_impressions", 0)
                        if imps > best_impressions:
                            best_impressions = imps
                            best_keyword = kw_data.get("keyword", theme)

                # Decide content type
                if "alternative" in theme_lower or "vs" in theme_lower:
                    content_type = "comparison"
                elif any(w in theme_lower for w in ["how", "guide", "strategy", "strategies", "best practice"]):
                    content_type = "blog_post"
                else:
                    content_type = "blog_post"

                # Determine priority based on keyword data
                priority = "high" if best_impressions > 50 else "medium"

                # Merge insights from stored competitor analysis if available
                extra_context = ""
                ca = competitor_data.get(comp_key, {})
                opp_list = ca.get("content_opportunities", [])
                for opp in (opp_list if isinstance(opp_list, list) else []):
                    opp_text = opp if isinstance(opp, str) else str(opp)
                    if theme_lower.split()[0] in opp_text.lower():
                        extra_context = f" Competitor insight: {opp_text[:120]}"
                        break

                gaps.append({
                    "competitor": comp_name,
                    "theme": theme,
                    "recommended_type": content_type,
                    "priority": priority,
                    "target_keyword": best_keyword,
                    "keyword_impressions": best_impressions,
                    "title": f"Write a {content_type.replace('_', ' ')} about {theme} targeting '{best_keyword}'",
                    "description": (
                        f"{comp_name} covers '{theme}' but we have no matching content. "
                        f"Target keyword: '{best_keyword}'"
                        + (f" ({best_impressions} impressions/month)" if best_impressions else "")
                        + f".{extra_context}"
                    ),
                    "action": (
                        f"Generate a {content_type.replace('_', ' ')} covering '{theme}' "
                        f"optimized for the keyword '{best_keyword}'"
                    ),
                })

            coverage[comp_key] = {
                "name": comp_name,
                "total_themes": total,
                "covered": covered,
                "gaps": total - covered,
                "coverage_pct": round(covered / total * 100, 1) if total else 0,
            }

        # -- 5. Add orphan-keyword gaps (GSC keywords without content) ----- #
        for kw_data in orphan_keywords:
            keyword = kw_data.get("keyword", "")
            impressions = kw_data.get("current_impressions", 0)
            position = kw_data.get("current_position", 0)
            if impressions < 10 and (position or 100) > 50:
                continue  # skip very low value orphans

            priority = "high" if impressions > 100 else "medium"
            gaps.append({
                "competitor": "organic_opportunity",
                "theme": keyword,
                "recommended_type": "blog_post",
                "priority": priority,
                "target_keyword": keyword,
                "keyword_impressions": impressions,
                "title": f"Write a blog post targeting '{keyword}'",
                "description": (
                    f"We rank position {position} for '{keyword}' "
                    f"({impressions} impressions) but have no dedicated content."
                ),
                "action": (
                    f"Generate a blog post targeting '{keyword}' to improve "
                    f"from position {position} and capture more organic traffic"
                ),
            })

        # Sort: high priority first, then by impressions descending
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        gaps.sort(key=lambda g: (
            priority_order.get(g.get("priority", "low"), 3),
            -g.get("keyword_impressions", 0),
        ))

        total_competitor_gaps = sum(c["gaps"] for c in coverage.values())
        orphan_count = sum(1 for g in gaps if g["competitor"] == "organic_opportunity")

        summary = (
            f"Found {total_competitor_gaps} competitor theme gaps across "
            f"{len(self.COMPETITOR_CONTENT_THEMES)} competitors and {orphan_count} "
            f"orphan keyword opportunities. "
            + " | ".join(
                f"{v['name']}: {v['coverage_pct']}% covered"
                for v in coverage.values()
            )
        )

        logger.info(f"Competitor gap analysis complete: {summary}")

        return {
            "gaps": gaps,
            "coverage": coverage,
            "orphan_keywords": orphan_count,
            "total_gaps": len(gaps),
            "summary": summary,
        }

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
        """Save content to Supabase (upsert by title)"""
        sb = self._get_sb()
        
        # Check if content with this title already exists
        existing = sb.table(CONTENT_PIECES_TABLE).select("id").eq("title", title).execute()
        
        record = {
            "title": title,
            "content": content,
            "content_type": content_type,
            "target_keyword": target_keyword,
            "meta_title": title[:200] if len(title) > 200 else title,
            "meta_description": meta_description,
            "word_count": word_count,
            "target_url": target_url,
        }
        
        if existing.data:
            # Update existing record
            record["updated_at"] = datetime.utcnow().isoformat()
            result = sb.table(CONTENT_PIECES_TABLE).update(record).eq("id", existing.data[0]["id"]).execute()
            logger.info(f"📝 Updated existing content: {title}")
        else:
            # Insert new record
            record["status"] = "draft"
            record["created_at"] = datetime.utcnow().isoformat()
            result = sb.table(CONTENT_PIECES_TABLE).insert(record).execute()
            logger.info(f"✨ Created new content: {title}")
        
        return result.data[0] if result.data else record


# Global content agent instance
content_agent = ContentAgent()
