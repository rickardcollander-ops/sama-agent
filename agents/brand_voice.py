"""
Brand Voice Engine for Successifier
Stores and retrieves brand voice guidelines for consistent content generation.

This module exposes both:
  * The legacy class-level API (BrandVoice.get_system_prompt / brand_voice singleton)
    used by callers that haven't yet been threaded with tenant_id.
  * A tenant-aware path: BrandVoice.for_tenant(tenant_id) returns a
    TenantBrandVoice loaded from the tenant_brand_voices table. The two
    paths share the same prompt template via _render_system_prompt() so
    the wording is consistent.

IMPORTANT: voice is strictly per-tenant. for_tenant() raises
BrandVoiceNotFoundError when no row exists for tenant_id -- it never
falls back to another tenant's voice or to the Successifier defaults
(unless tenant_id == 'default').
"""

import logging
import re
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class BrandVoiceNotFoundError(LookupError):
    """Raised when no per-tenant brand voice exists yet for a tenant_id."""

    def __init__(self, tenant_id: str):
        super().__init__(
            f"No brand voice for tenant_id={tenant_id!r}; run brand_voice_scraper first."
        )
        self.tenant_id = tenant_id


class BrandVoice:
    """
    Successifier brand voice profile (default) + tenant-aware factory.
    """

    # ── Anti-AI-tell list (always merged into every voice's avoid list) ──
    AI_TELLS: List[str] = [
        "—",  # em-dash
        "delve", "delving",
        "tapestry",
        "moreover", "furthermore",
        "in today's world", "in the realm of", "in the world of",
        "navigate", "navigating",
        "leverage",
        "harness",
        "robust",
        "seamless", "seamlessly",
        "game-changer", "revolutionary", "disruptive",
        "it's important to note",
        "in conclusion",
        "ever-evolving", "ever-changing",
        "unleash", "unlock",
        "elevate", "elevating",
    ]

    # ── Legacy Successifier defaults (used when tenant_id == 'default') ──
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

    PROOF_POINTS = {
        "churn_reduction": "40% churn reduction",
        "nrr_improvement": "25% NRR improvement",
        "efficiency": "85% less manual work",
        "pricing": "from $79/month",
        "trial": "14-day free trial"
    }

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

    TARGET_PERSONA = {
        "title": "VP/Director of Customer Success",
        "company": "B2B SaaS company",
        "customer_base": "500-10,000 customers",
        "team_size": "3-15 CS team members",
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

    # ── System prompt rendering ───────────────────────────────────────────

    @classmethod
    def get_system_prompt(cls, content_type: str = "blog") -> str:
        """Backwards-compat class-level prompt using Successifier defaults."""
        return cls._render_system_prompt(
            tone_overall=cls.TONE["overall"],
            tone_do=cls.TONE["do"],
            tone_dont=cls.TONE["dont"],
            messaging_pillars=cls.MESSAGING_PILLARS,
            proof_points=cls.PROOF_POINTS,
            target_persona=cls.TARGET_PERSONA,
            vocabulary=cls.VOCABULARY,
            content_type=content_type,
            brand_name="Successifier",
        )

    @staticmethod
    def _render_system_prompt(
        *,
        tone_overall: str,
        tone_do: List[str],
        tone_dont: List[str],
        messaging_pillars: Any,
        proof_points: Dict[str, str],
        target_persona: Dict[str, Any],
        vocabulary: Dict[str, Any],
        content_type: str,
        brand_name: str,
    ) -> str:
        # Pillars: accept dict OR list of dicts
        if isinstance(messaging_pillars, dict):
            pillar_iter = list(messaging_pillars.values())
        elif isinstance(messaging_pillars, list):
            pillar_iter = messaging_pillars
        else:
            pillar_iter = []
        pillar_lines = [
            f"{i+1}. {p.get('title','(untitled)')}: {p.get('description','')}"
            for i, p in enumerate(pillar_iter)
            if isinstance(p, dict)
        ]
        pillars_block = "\n".join(pillar_lines) or "(none specified)"

        if isinstance(proof_points, dict):
            proof_lines = [f"- {v}" for v in proof_points.values() if v]
        else:
            proof_lines = [f"- {v}" for v in (proof_points or [])]
        proof_block = "\n".join(proof_lines) or "(none specified)"

        do_block = "\n".join(f"- {item}" for item in (tone_do or []))
        dont_block = "\n".join(f"- {item}" for item in (tone_dont or []))

        # Vocabulary -- always force AI-tells into the avoid list
        vocab = vocabulary or {}
        avoid_terms: List[str] = list(vocab.get("avoid", []) or [])
        avoid_lower = {a.lower() for a in avoid_terms if isinstance(a, str)}
        for tell in BrandVoice.AI_TELLS:
            if tell.lower() not in avoid_lower:
                avoid_terms.append(tell)
                avoid_lower.add(tell.lower())

        preferred = vocab.get("preferred", {}) or {}
        if isinstance(preferred, dict):
            preferred_lines = [
                f'- Use "{k}" -- {v}' if v else f'- Use "{k}"'
                for k, v in preferred.items()
            ]
        else:
            preferred_lines = [f'- Use "{p}"' for p in preferred]
        preferred_block = "\n".join(preferred_lines) or "(none specified)"
        avoid_block = "\n".join(f'- Avoid "{a}"' for a in avoid_terms)

        persona = target_persona or {}
        persona_block = (
            f"{persona.get('title', '(unspecified)')} -- "
            f"goals: {', '.join(persona.get('goals', []) or []) or 'n/a'}; "
            f"pain points: {', '.join(persona.get('pain_points', []) or []) or 'n/a'}"
        )

        base = f"""You are a content writer for {brand_name}.

BRAND VOICE:
{tone_overall}

MESSAGING PILLARS:
{pillars_block}

PROOF POINTS (cite where relevant):
{proof_block}

TARGET AUDIENCE:
{persona_block}

VOCABULARY -- PREFERRED:
{preferred_block}

VOCABULARY -- AVOID (this list includes AI-tells; never use any of these):
{avoid_block}

TONE GUIDELINES -- DO:
{do_block}

TONE GUIDELINES -- DON'T:
{dont_block}

WRITING STYLE RULES (mandatory):
- Write like a human professional, not like ChatGPT.
- NEVER use em-dashes (—). Use a comma, period, or parentheses instead.
- Avoid the avoid-list above. Avoid generic AI-prose cliches.
- Vary sentence length. Mix short (5-8 words) and longer sentences. Active voice.
- No throat-clearing openers ("In today's world", "In conclusion", "It's important to note").
- Concrete > abstract. Specific examples > generic platitudes.
"""

        if content_type == "blog":
            base += """
BLOG POST REQUIREMENTS:
- 1,500-2,500 words
- Compelling hook in the first 2 sentences (no warm-up paragraph)
- H2/H3 subheadings for structure
- Specific examples and data
- Clear takeaways and CTA at the end
- Natural keyword integration (no stuffing)
"""
        elif content_type == "landing_page":
            base += """
LANDING PAGE REQUIREMENTS:
- 800-1,200 words
- Clear value proposition above the fold
- Benefit-focused, not feature-focused
- Social proof and proof points
- Strong CTA
- Scannable: bullets, short paragraphs
"""
        elif content_type == "comparison":
            base += """
COMPARISON PAGE REQUIREMENTS:
- 2,000-3,000 words
- Fair but favorable comparison
- Feature-by-feature breakdown
- Pricing comparison
- Use case examples
- Clear "Why [our brand]" section
- CTA to try
"""
        elif content_type == "social_linkedin":
            base += """
LINKEDIN POST REQUIREMENTS:
- 600-1,400 characters (3-6 short paragraphs separated by blank lines)
- Hook on line 1 (the only line visible before "see more")
- 1 insight or specific anecdote
- 1 clear takeaway or question
- End with the article link on its own line
- Conversational, first-person OK
- 0-2 hashtags max, only if natural
"""
        elif content_type == "social_x":
            base += """
X / TWITTER POST REQUIREMENTS:
- Single tweet <= 280 chars OR a thread of 3-5 tweets each <= 280 chars
- Hook on tweet 1 (curiosity, contrarian, specific stat)
- Article link on the last tweet
- 0-2 hashtags max, 0-1 emoji max
"""
        elif content_type == "social_instagram":
            base += """
INSTAGRAM CAPTION REQUIREMENTS:
- 100-200 words
- Hook on line 1 (everything after collapses under "more")
- Line breaks between thoughts
- 5-8 relevant hashtags grouped at the end
- "Link in bio" pointing at the article
- Conversational, value-first
"""
        elif content_type == "social_facebook":
            base += """
FACEBOOK POST REQUIREMENTS:
- 100-250 words
- Conversational opening (often a question or scenario)
- 1 insight + 1 question to drive comments
- Article link at the end
- 0-1 emoji max, 0-1 hashtag max
"""

        return base

    # ── Tenant-aware factory ──────────────────────────────────────────────

    @classmethod
    def for_tenant(cls, tenant_id: str) -> "TenantBrandVoice":
        """Return a tenant-specific BrandVoice instance.

        Reads tenant_brand_voices.voice_json keyed strictly by tenant_id.
        If no row exists, raises BrandVoiceNotFoundError so the caller can
        trigger brand_voice_scraper.scrape_and_extract(tenant_id, domain).

        For tenant_id='default', returns a TenantBrandVoice rendered from
        the legacy Successifier defaults; this is only for the demo tenant.
        """
        if not tenant_id or tenant_id == "default":
            return TenantBrandVoice(tenant_id="default", voice_json=cls._default_voice_dict())

        from shared.database import get_supabase
        sb = get_supabase()
        try:
            result = (
                sb.table("tenant_brand_voices")
                .select("voice_json")
                .eq("tenant_id", tenant_id)
                .single()
                .execute()
            )
        except Exception as e:
            logger.debug(f"BrandVoice.for_tenant({tenant_id}) lookup failed: {e}")
            raise BrandVoiceNotFoundError(tenant_id) from e

        if not result.data:
            raise BrandVoiceNotFoundError(tenant_id)
        return TenantBrandVoice(tenant_id=tenant_id, voice_json=result.data["voice_json"] or {})

    @classmethod
    def _default_voice_dict(cls) -> Dict[str, Any]:
        return {
            "tone": {
                "overall": cls.TONE["overall"],
                "do": list(cls.TONE["do"]),
                "dont": list(cls.TONE["dont"]),
            },
            "vocabulary": {
                "preferred": dict(cls.VOCABULARY.get("preferred", {})),
                "avoid": list(cls.VOCABULARY.get("avoid", [])),
            },
            "messaging_pillars": [
                {
                    "title": p["title"],
                    "description": p["description"],
                    "key_phrases": list(p.get("key_phrases", [])),
                }
                for p in cls.MESSAGING_PILLARS.values()
            ],
            "proof_points": dict(cls.PROOF_POINTS),
            "target_persona": dict(cls.TARGET_PERSONA),
            "sentence_rhythm": {
                "avg_sentence_length": "medium",
                "rhythm": "Mix short and longer sentences for clarity.",
            },
        }

    # ── Post-generation cleanup + validation ──────────────────────────────

    @staticmethod
    def cleanup_ai_tells(content: str) -> str:
        """Replace em-dashes contextually (the most common AI-tell).

        ' — '  ->  ', '
        word—word  ->  word-word
        any leftover —  ->  ', '
        Other AI-tells are flagged by validate_content but left in place;
        the agent re-runs generation if too many remain.
        """
        if not content:
            return content
        out = content.replace(" — ", ", ")
        out = re.sub(r"(\w)—(\w)", r"\1-\2", out)
        out = out.replace("—", ", ")
        return out

    @classmethod
    def validate_content(
        cls,
        content: str,
        voice: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        issues: List[str] = []
        score = 100
        content_lower = content.lower()

        avoid_terms: List[str] = []
        if voice and isinstance(voice.get("vocabulary"), dict):
            avoid_terms.extend(voice["vocabulary"].get("avoid", []) or [])
        avoid_terms.extend(cls.VOCABULARY.get("avoid", []) or [])
        for t in cls.AI_TELLS:
            if t not in avoid_terms:
                avoid_terms.append(t)

        for term in avoid_terms:
            if not isinstance(term, str) or not term:
                continue
            if term.lower() in content_lower:
                issues.append(f"Avoid using '{term}'")
                score -= 5

        if "—" in content:
            issues.append("Contains em-dash; replace with comma or period")
            score -= 10

        proof_points_used = 0
        proof_dict = (voice or {}).get("proof_points") if isinstance(voice, dict) else None
        if not proof_dict:
            proof_dict = cls.PROOF_POINTS
        if isinstance(proof_dict, dict):
            for value in proof_dict.values():
                if isinstance(value, str) and value.lower() in content_lower:
                    proof_points_used += 1

        if proof_points_used == 0:
            issues.append("No proof points cited")
            score -= 10

        word_count = len(content.split())
        return {
            "score": max(0, score),
            "issues": issues,
            "proof_points_used": proof_points_used,
            "word_count": word_count,
            "passed": score >= 70 and "—" not in content,
        }


class TenantBrandVoice:
    """Per-tenant voice instance loaded from tenant_brand_voices.

    Renders the same prompt template as BrandVoice but with this tenant's
    voice_json substituted in. Strictly tied to one tenant_id; never
    shared across tenants.
    """

    def __init__(self, tenant_id: str, voice_json: Dict[str, Any]):
        self.tenant_id = tenant_id
        self.voice = voice_json or {}

    def get_system_prompt(self, content_type: str = "blog", brand_name: str = "") -> str:
        tone = self.voice.get("tone", {}) or {}
        return BrandVoice._render_system_prompt(
            tone_overall=tone.get("overall", "Professional and clear."),
            tone_do=tone.get("do", []) or [],
            tone_dont=tone.get("dont", []) or [],
            messaging_pillars=self.voice.get("messaging_pillars", []) or [],
            proof_points=self.voice.get("proof_points", {}) or {},
            target_persona=self.voice.get("target_persona", {}) or {},
            vocabulary=self.voice.get("vocabulary", {}) or {},
            content_type=content_type,
            brand_name=brand_name or self.tenant_id,
        )

    def validate_content(self, content: str) -> Dict[str, Any]:
        return BrandVoice.validate_content(content, voice=self.voice)

    def cleanup_ai_tells(self, content: str) -> str:
        return BrandVoice.cleanup_ai_tells(content)


# Backwards-compat singleton (Successifier defaults). New code should use
# BrandVoice.for_tenant(tenant_id) for tenant-aware voice; never share this
# singleton across tenants.
brand_voice = BrandVoice()
