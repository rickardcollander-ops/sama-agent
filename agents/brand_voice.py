"""
Brand Voice Engine for Successifier
Stores and retrieves brand voice guidelines for consistent content generation
"""

from typing import Dict, Any, List


class BrandVoice:
    """
    Successifier brand voice profile
    Based on SAMA 2.0 spec section 4.2
    """
    
    # Core messaging pillars
    MESSAGING_PILLARS = {
        "ai_native": {
            "title": "AI-Native (Not AI-Bolted-On)",
            "description": "Built from the ground up with AI at the core, not retrofitted",
            "key_phrases": [
                "AI-native platform",
                "built for AI from day one",
                "AI at the core",
                "designed with AI intelligence"
            ]
        },
        "affordable": {
            "title": "Affordable (Enterprise Features at Startup Pricing)",
            "description": "Enterprise-grade capabilities without enterprise pricing",
            "key_phrases": [
                "from $79/month",
                "enterprise features at startup pricing",
                "affordable for growing teams",
                "no enterprise tax"
            ]
        },
        "fast_value": {
            "title": "Fast Time-to-Value",
            "description": "Quick setup and immediate ROI",
            "key_phrases": [
                "30-minute setup",
                "ROI in 30 days",
                "see results immediately",
                "up and running in minutes"
            ]
        }
    }
    
    # Proof points - always cite these
    PROOF_POINTS = {
        "churn_reduction": "40% churn reduction",
        "nrr_improvement": "25% NRR improvement",
        "efficiency": "85% less manual work",
        "pricing": "from $79/month",
        "trial": "14-day free trial"
    }
    
    # Tone guidelines
    TONE = {
        "overall": "Professional but approachable. Expert without being academic. Confident without being arrogant.",
        "do": [
            "Use data and specific metrics",
            "Be direct and clear",
            "Show expertise through insights, not jargon",
            "Use active voice",
            "Keep sentences concise",
            "Address pain points directly"
        ],
        "dont": [
            "Use buzzwords without substance",
            "Be overly casual or use excessive emojis",
            "Make claims without data",
            "Use academic or overly technical language",
            "Talk down to readers",
            "Use clichés like 'game-changer' or 'revolutionary'"
        ]
    }
    
    # Vocabulary guidelines
    VOCABULARY = {
        "preferred": {
            "customer success": "Always use this, not 'client success'",
            "health score": "Not 'account score'",
            "churn": "Direct term, don't euphemize",
            "AI-native": "Hyphenated when used as adjective",
            "onboarding": "One word, not 'on-boarding'",
            "playbook": "Not 'workflow' or 'automation'",
            "less manual work": "Not 'headcount reduction' or 'replacing people'"
        },
        "avoid": [
            "client success",
            "account score",
            "customer attrition",
            "headcount reduction",
            "replacing CSMs",
            "game-changer",
            "revolutionary",
            "disruptive"
        ]
    }
    
    # Target persona
    TARGET_PERSONA = {
        "title": "VP/Director of Customer Success",
        "company": "B2B SaaS company",
        "customer_base": "500–10,000 customers",
        "team_size": "3–15 CS team members",
        "pain_points": [
            "Manual work overwhelming the team",
            "Can't scale CS without hiring",
            "Churn happening before they can intervene",
            "No visibility into customer health",
            "Onboarding takes too long",
            "Can't identify expansion opportunities"
        ],
        "goals": [
            "Reduce churn",
            "Increase NRR",
            "Scale CS operations",
            "Improve customer experience",
            "Prove CS ROI"
        ]
    }
    
    # Content pillars (from SAMA 2.0 spec)
    CONTENT_PILLARS = {
        "churn_prevention": {
            "title": "Churn Prevention",
            "topics": [
                "Detecting churn signals early",
                "Predicting churn with AI",
                "Reducing SaaS churn",
                "Churn analysis and prevention"
            ]
        },
        "health_scoring": {
            "title": "Customer Health Scoring",
            "topics": [
                "Building effective health scores",
                "Using health scores to prioritize",
                "AI-powered health scoring",
                "Health score frameworks"
            ]
        },
        "automation": {
            "title": "CS Automation",
            "topics": [
                "Automating customer success workflows",
                "CS playbooks and automation",
                "Scaling CS with automation",
                "AI automation for CS teams"
            ]
        },
        "nrr_growth": {
            "title": "NRR Growth",
            "topics": [
                "Expansion revenue strategies",
                "Identifying upsell opportunities",
                "NRR benchmarks and best practices",
                "Growing revenue from existing customers"
            ]
        },
        "comparisons": {
            "title": "Platform Comparisons",
            "topics": [
                "Gainsight alternatives",
                "Totango vs comparisons",
                "ChurnZero alternatives",
                "CS platform comparison"
            ]
        },
        "onboarding": {
            "title": "Customer Onboarding",
            "topics": [
                "Customer onboarding software",
                "Onboarding portals and automation",
                "Reducing time-to-value",
                "Onboarding best practices"
            ]
        }
    }
    
    @classmethod
    def get_system_prompt(cls, content_type: str = "blog") -> str:
        """
        Generate system prompt for content generation
        
        Args:
            content_type: Type of content (blog, landing_page, comparison, etc.)
        
        Returns:
            System prompt with brand voice guidelines
        """
        base_prompt = f"""You are a content writer for Successifier, an AI-native Customer Success Platform.

BRAND VOICE:
{cls.TONE['overall']}

MESSAGING PILLARS:
1. {cls.MESSAGING_PILLARS['ai_native']['title']}: {cls.MESSAGING_PILLARS['ai_native']['description']}
2. {cls.MESSAGING_PILLARS['affordable']['title']}: {cls.MESSAGING_PILLARS['affordable']['description']}
3. {cls.MESSAGING_PILLARS['fast_value']['title']}: {cls.MESSAGING_PILLARS['fast_value']['description']}

PROOF POINTS (always cite):
- {cls.PROOF_POINTS['churn_reduction']}
- {cls.PROOF_POINTS['nrr_improvement']}
- {cls.PROOF_POINTS['efficiency']}
- {cls.PROOF_POINTS['pricing']}
- {cls.PROOF_POINTS['trial']}

TARGET AUDIENCE:
{cls.TARGET_PERSONA['title']} at a {cls.TARGET_PERSONA['company']} with {cls.TARGET_PERSONA['customer_base']} customers and a CS team of {cls.TARGET_PERSONA['team_size']} people.

VOCABULARY:
- Use "customer success" not "client success"
- Use "health score" not "account score"
- Use "less manual work" not "headcount reduction"

TONE GUIDELINES:
DO:
{chr(10).join('- ' + item for item in cls.TONE['do'])}

DON'T:
{chr(10).join('- ' + item for item in cls.TONE['dont'])}
"""
        
        # Add content-type specific guidelines
        if content_type == "blog":
            base_prompt += """

BLOG POST REQUIREMENTS:
- 1,500–2,500 words
- Start with a compelling hook
- Use subheadings (H2, H3) for structure
- Include specific examples and data
- End with clear takeaways and CTA
- Natural keyword integration (no keyword stuffing)
"""
        elif content_type == "landing_page":
            base_prompt += """

LANDING PAGE REQUIREMENTS:
- 800–1,200 words
- Clear value proposition above the fold
- Benefit-focused (not feature-focused)
- Social proof and proof points
- Strong CTA
- Scannable format (bullets, short paragraphs)
"""
        elif content_type == "comparison":
            base_prompt += """

COMPARISON PAGE REQUIREMENTS:
- 2,000–3,000 words
- Fair but favorable comparison
- Feature-by-feature breakdown
- Pricing comparison
- Use case examples
- Clear "Why Successifier" section
- CTA to try Successifier
"""
        
        return base_prompt
    
    @classmethod
    def validate_content(cls, content: str) -> Dict[str, Any]:
        """
        Validate content against brand voice guidelines
        
        Args:
            content: Content to validate
        
        Returns:
            Validation results with score and issues
        """
        issues = []
        score = 100
        
        # Check for avoided terms
        content_lower = content.lower()
        for term in cls.VOCABULARY['avoid']:
            if term.lower() in content_lower:
                issues.append(f"Avoid using '{term}'")
                score -= 10
        
        # Check for proof points
        proof_points_used = 0
        for key, value in cls.PROOF_POINTS.items():
            if value.lower() in content_lower:
                proof_points_used += 1
        
        if proof_points_used == 0:
            issues.append("No proof points cited")
            score -= 20
        
        # Check word count for blog posts
        word_count = len(content.split())
        
        return {
            "score": max(0, score),
            "issues": issues,
            "proof_points_used": proof_points_used,
            "word_count": word_count,
            "passed": score >= 70
        }


# Global brand voice instance
brand_voice = BrandVoice()
