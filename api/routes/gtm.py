"""
GTM Strategy & Sequences API
Go-to-market intelligence, ICP analysis, sequences, and agent signals.
"""

from fastapi import APIRouter, HTTPException
from typing import Dict, Any, List, Optional
from pydantic import BaseModel
import logging
from datetime import datetime

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Models ────────────────────────────────────────────────────────────

class SequenceStep(BaseModel):
    day: int
    channel: str  # "linkedin_dm", "email", "linkedin_comment", "twitter"
    action: str
    template: str

class Sequence(BaseModel):
    id: str
    name: str
    description: str
    target_persona: str
    steps: List[SequenceStep]
    status: str = "active"  # active, paused, draft
    created_at: str = ""

class GenerateSequencesRequest(BaseModel):
    count: int = 5
    focus: Optional[str] = None  # e.g., "enterprise", "startup", "churned"

class SequenceUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    steps: Optional[List[dict]] = None


# ── Default Sequences ─────────────────────────────────────────────────

DEFAULT_SEQUENCES: List[Dict[str, Any]] = [
    {
        "id": "seq_new_trial",
        "name": "New Trial Onboarding",
        "description": "Welcome new trial signups and guide them to activation",
        "target_persona": "New trial user (signed up < 3 days)",
        "status": "active",
        "steps": [
            {"day": 0, "channel": "linkedin_dm", "action": "connect_request", "template": "Hi {first_name}, I noticed you just started exploring Successifier — welcome! I'm one of the founders. Happy to help if you have any questions about getting started."},
            {"day": 1, "channel": "email", "action": "send_email", "template": "Quick tip: most teams see results fastest when they start with {use_case}. Here's a 2-min setup guide."},
            {"day": 3, "channel": "linkedin_dm", "action": "follow_up", "template": "Hey {first_name}, how's the trial going? A lot of {industry} teams use the {feature} — want me to show you how?"},
            {"day": 5, "channel": "linkedin_comment", "action": "engage_post", "template": "Engage with their recent LinkedIn post to build rapport"},
            {"day": 7, "channel": "linkedin_dm", "action": "offer_call", "template": "Would a quick 15-min call be helpful? I can walk you through exactly how {company} could use Successifier to {value_prop}."},
        ],
    },
    {
        "id": "seq_enterprise_outreach",
        "name": "Enterprise Decision Maker",
        "description": "Multi-touch outreach to VP/C-level at enterprise accounts",
        "target_persona": "VP/C-level at companies with 500+ employees",
        "status": "active",
        "steps": [
            {"day": 0, "channel": "linkedin_dm", "action": "connect_request", "template": "Hi {first_name}, I've been following {company}'s growth — impressive work on {recent_achievement}. Would love to connect."},
            {"day": 2, "channel": "linkedin_comment", "action": "engage_post", "template": "Leave a thoughtful comment on their latest post adding genuine value"},
            {"day": 4, "channel": "linkedin_dm", "action": "value_share", "template": "Thought you'd find this interesting — we just published a case study on how {similar_company} reduced churn by 34% using customer success automation."},
            {"day": 7, "channel": "email", "action": "send_email", "template": "Hi {first_name}, teams at {similar_company_1} and {similar_company_2} in {industry} use Successifier to {specific_outcome}. Would you be open to a brief conversation about how this could work for {company}?"},
            {"day": 10, "channel": "linkedin_dm", "action": "soft_ask", "template": "Hi {first_name}, did you get a chance to check out the case study? Happy to do a quick walkthrough if it'd be useful — no pressure."},
            {"day": 14, "channel": "twitter", "action": "engage", "template": "Engage with or retweet their company content"},
        ],
    },
    {
        "id": "seq_competitor_switch",
        "name": "Competitor Displacement",
        "description": "Target users of competing products showing switching signals",
        "target_persona": "Current users of Gainsight, Totango, ChurnZero showing frustration",
        "status": "active",
        "steps": [
            {"day": 0, "channel": "linkedin_dm", "action": "empathy_connect", "template": "Hi {first_name}, I noticed you work in customer success at {company}. We hear from a lot of {competitor} users that {common_pain_point} — curious if that resonates?"},
            {"day": 3, "channel": "email", "action": "comparison_share", "template": "Hi {first_name}, we put together an honest comparison between Successifier and {competitor}. The biggest difference: {key_differentiator}. Here's the full breakdown."},
            {"day": 5, "channel": "linkedin_comment", "action": "thought_leadership", "template": "Share insight about the problem their competitor doesn't solve well"},
            {"day": 7, "channel": "linkedin_dm", "action": "social_proof", "template": "{switcher_name} at {switcher_company} switched from {competitor} last quarter — they cut onboarding time by 60%. Want me to intro you?"},
            {"day": 10, "channel": "linkedin_dm", "action": "offer_demo", "template": "Would it be helpful to see a side-by-side of how {company}'s workflow would look in Successifier vs {competitor}? Takes about 20 min."},
        ],
    },
    {
        "id": "seq_content_engaged",
        "name": "Content Engaged Lead",
        "description": "Nurture leads who engaged with blog posts, webinars, or downloads",
        "target_persona": "Leads who downloaded whitepaper or attended webinar",
        "status": "active",
        "steps": [
            {"day": 0, "channel": "linkedin_dm", "action": "connect_reference", "template": "Hi {first_name}, noticed you checked out our {content_piece} — great topic right? Happy to connect and share more resources on {topic}."},
            {"day": 2, "channel": "email", "action": "deeper_content", "template": "Since you were interested in {topic}, you might also like this: {related_content}. It goes deeper into {specific_angle}."},
            {"day": 4, "channel": "linkedin_comment", "action": "engage_post", "template": "Engage with their content or posts related to the topic"},
            {"day": 7, "channel": "linkedin_dm", "action": "ask_challenge", "template": "Out of curiosity, what's the biggest {topic} challenge at {company} right now? We've been seeing some interesting patterns with {industry} teams."},
            {"day": 10, "channel": "email", "action": "case_study", "template": "Here's how {customer_name} tackled exactly that challenge and saw {result}. Would love to explore if something similar could work for {company}."},
        ],
    },
    {
        "id": "seq_churned_winback",
        "name": "Churned Customer Win-back",
        "description": "Re-engage customers who cancelled in the last 90 days",
        "target_persona": "Former customers who churned in last 90 days",
        "status": "active",
        "steps": [
            {"day": 0, "channel": "linkedin_dm", "action": "personal_reach", "template": "Hi {first_name}, hope all is well at {company}. I wanted to personally reach out — we've made some big improvements since you were with us, especially around {their_pain_point}."},
            {"day": 3, "channel": "email", "action": "whats_new", "template": "Hi {first_name}, since you left we've shipped: {feature_1}, {feature_2}, and {feature_3}. Several teams told us these would've been game-changers. Worth another look?"},
            {"day": 7, "channel": "linkedin_dm", "action": "offer_trial", "template": "Would you be open to a free 30-day trial to see the improvements firsthand? No commitment — just want to show you what's changed."},
            {"day": 14, "channel": "linkedin_dm", "action": "last_touch", "template": "Last message from me on this — just wanted to make sure you know the door is always open. If timing is ever right again, I'd love to help {company} succeed."},
        ],
    },
]


# ── In-memory store (would be Supabase in production) ─────────────────

_sequences_store: List[Dict[str, Any]] = []
_initialized = False

def _ensure_defaults():
    global _sequences_store, _initialized
    if not _initialized:
        _sequences_store = [
            {**seq, "created_at": datetime.utcnow().isoformat()}
            for seq in DEFAULT_SEQUENCES
        ]
        _initialized = True
    return _sequences_store


# ── Endpoints ─────────────────────────────────────────────────────────

@router.get("/sequences")
async def list_sequences():
    """Get all sequences"""
    seqs = _ensure_defaults()
    return {"sequences": seqs, "count": len(seqs)}


@router.get("/sequences/{sequence_id}")
async def get_sequence(sequence_id: str):
    """Get a specific sequence"""
    seqs = _ensure_defaults()
    for s in seqs:
        if s["id"] == sequence_id:
            return {"sequence": s}
    raise HTTPException(status_code=404, detail="Sequence not found")


@router.post("/sequences/generate")
async def generate_sequences(req: GenerateSequencesRequest):
    """AI-generate new sequences using Claude"""
    from shared.config import settings

    count = min(req.count, 10)
    focus = req.focus or "general B2B SaaS outreach"

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

        prompt = f"""Generate {count} LinkedIn/email outreach sequences for Successifier, a B2B SaaS customer success platform.

Focus: {focus}

For each sequence, provide:
- name: short descriptive name
- description: one-liner about the sequence
- target_persona: who this targets
- steps: array of outreach steps, each with:
  - day: number (0 = immediately, then 1, 3, 5, 7, etc.)
  - channel: one of "linkedin_dm", "email", "linkedin_comment", "twitter"
  - action: what type of action (connect_request, send_email, follow_up, engage_post, offer_call, etc.)
  - template: the actual message template with {{first_name}}, {{company}}, {{industry}} placeholders

Make the sequences feel natural, not salesy. Mix channels. Each sequence should have 4-6 steps spread over 7-14 days.

Respond with valid JSON only — an array of sequence objects. No markdown, no explanation."""

        message = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )

        import json
        text = message.content[0].text.strip()
        # Handle potential markdown wrapping
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        generated = json.loads(text)

        seqs = _ensure_defaults()
        new_sequences = []
        for i, seq_data in enumerate(generated):
            seq_id = f"seq_ai_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{i}"
            new_seq = {
                "id": seq_id,
                "name": seq_data.get("name", f"AI Sequence {i+1}"),
                "description": seq_data.get("description", ""),
                "target_persona": seq_data.get("target_persona", ""),
                "status": "draft",
                "steps": seq_data.get("steps", []),
                "created_at": datetime.utcnow().isoformat(),
            }
            seqs.append(new_seq)
            new_sequences.append(new_seq)

        return {
            "success": True,
            "generated": len(new_sequences),
            "sequences": new_sequences,
            "message": f"Generated {len(new_sequences)} new sequences"
        }

    except ImportError:
        raise HTTPException(status_code=500, detail="Anthropic SDK not installed")
    except Exception as e:
        logger.error(f"Sequence generation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Generation failed: {str(e)}")


@router.put("/sequences/{sequence_id}")
async def update_sequence(sequence_id: str, req: SequenceUpdateRequest):
    """Update an existing sequence"""
    seqs = _ensure_defaults()
    for s in seqs:
        if s["id"] == sequence_id:
            if req.name is not None: s["name"] = req.name
            if req.description is not None: s["description"] = req.description
            if req.status is not None: s["status"] = req.status
            if req.steps is not None: s["steps"] = req.steps
            return {"success": True, "sequence": s}
    raise HTTPException(status_code=404, detail="Sequence not found")


@router.delete("/sequences/{sequence_id}")
async def delete_sequence(sequence_id: str):
    """Delete a sequence"""
    global _sequences_store
    seqs = _ensure_defaults()
    _sequences_store = [s for s in seqs if s["id"] != sequence_id]
    return {"success": True}


# ── GTM Dashboard & Strategy endpoints ────────────────────────────────

@router.get("/dashboard")
async def get_gtm_dashboard():
    """GTM dashboard overview"""
    from shared.database import get_supabase

    pipeline = {}
    marketing_summary = {"top_keywords_count": 0, "top_content_count": 0, "has_daily_metrics": False}
    icp = None
    strategy = None

    try:
        sb = get_supabase()

        # Get keyword count
        try:
            r = sb.table("keyword_rankings").select("*", count="exact").execute()
            marketing_summary["top_keywords_count"] = r.count or 0
        except: pass

        # Get content count
        try:
            r = sb.table("content_pieces").select("*", count="exact").execute()
            marketing_summary["top_content_count"] = r.count or 0
        except: pass

    except Exception as e:
        logger.warning(f"GTM dashboard error: {e}")

    seqs = _ensure_defaults()

    return {
        "dashboard": {
            "icp": icp,
            "strategy": strategy,
            "pipeline": pipeline,
            "marketing_summary": marketing_summary,
            "sequences_count": len(seqs),
            "active_sequences": len([s for s in seqs if s.get("status") == "active"]),
        }
    }


@router.post("/icp/analyze")
async def analyze_icp():
    """Analyze and define Ideal Customer Profile"""
    from shared.config import settings

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

        message = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": """Analyze the ICP for Successifier, a B2B SaaS customer success platform.

Based on the product (customer success, churn reduction, onboarding automation), define:
1. Primary segment (company size, industry, role, pain points)
2. Secondary segments (2-3)
3. Key insights
4. Gaps in our targeting

Respond as JSON with: refined_icp (primary_segment, secondary_segments, insights, gaps), recommendations"""}],
        )

        import json
        text = message.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        analysis = json.loads(text)
        return {"success": True, "analysis": analysis}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/strategy/generate")
async def generate_strategy(body: dict = {}):
    """Generate GTM strategy"""
    from shared.config import settings

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

        message = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=3000,
            messages=[{"role": "user", "content": """Generate a GTM strategy for Successifier, a B2B SaaS customer success platform competing with Gainsight, Totango, ChurnZero, Custify, Vitally.

Include:
1. strategy_name
2. time_horizon (e.g., "Q2 2025")
3. priority_segments (array of segments with name, size, approach)
4. channel_priorities (array: channel, budget_pct, expected_roi)
5. outreach_signals (targeting criteria for LinkedIn agent)
6. content_themes (array of strings)
7. kpis (array of metric, target, current)

Respond as JSON only."""}],
        )

        import json
        text = message.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        strategy = json.loads(text)
        return {"success": True, "strategy": strategy}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/signals/generate")
async def generate_signals():
    """Generate agent targeting signals from GTM strategy"""
    return {
        "success": True,
        "signals": {
            "agents": {
                "seo_agent": {
                    "target_keywords": ["customer success platform", "churn reduction software", "onboarding automation"],
                    "content_gaps": ["vs gainsight comparison", "customer success ROI calculator"],
                },
                "content_agent": {
                    "themes": ["customer success best practices", "churn prevention", "B2B SaaS metrics"],
                    "formats": ["comparison pages", "case studies", "ROI calculators"],
                },
                "ads_agent": {
                    "target_audiences": ["CS managers", "VP Customer Success", "Head of Growth"],
                    "competitor_keywords": ["gainsight alternative", "totango competitor"],
                },
                "social_agent": {
                    "topics": ["customer success", "SaaS growth", "churn reduction"],
                    "engage_with": ["customer success leaders", "SaaS founders"],
                },
                "linkedin_agent": {
                    "target_titles": ["VP Customer Success", "Head of CS", "Director of Customer Experience"],
                    "target_industries": ["SaaS", "Technology", "Financial Services"],
                    "company_size": "50-5000 employees",
                },
            }
        }
    }


@router.post("/review")
async def performance_review():
    """GTM performance review"""
    from shared.config import settings

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

        message = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": """Review GTM performance for Successifier, a B2B SaaS customer success platform.

Provide a performance review as JSON:
- overall_health: "strong" | "good" | "needs_attention" | "critical"
- score: 0-100
- working_well: array of things working
- needs_improvement: array of areas to improve
- next_actions: array of recommended next steps"""}],
        )

        import json
        text = message.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        review = json.loads(text)
        return {"success": True, "review": review}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
