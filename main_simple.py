"""
SAMA 2.0 - Simplified Version (No Database Required)
For testing without Docker/PostgreSQL/Redis
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import logging
from anthropic import Anthropic
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv('.env.local')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="SAMA 2.0 (Simplified)",
    description="Successifier Autonomous Marketing Agent - No Database Version",
    version="2.0.0-simple"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Anthropic client
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    logger.warning("⚠️  ANTHROPIC_API_KEY not set. Agent features will be limited.")
    client = None
else:
    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    logger.info("✅ Anthropic client initialized")


# Request models
class BlogPostRequest(BaseModel):
    topic: str
    target_keyword: Optional[str] = None
    word_count: int = 2000


class RSARequest(BaseModel):
    campaign: str
    ad_group: str
    target_keyword: Optional[str] = None


# Root endpoint
@app.get("/")
async def root():
    return {
        "service": "SAMA 2.0 (Simplified)",
        "status": "operational",
        "version": "2.0.0-simple",
        "note": "Running without database. Data is not persisted.",
        "agents": {
            "seo": "limited",
            "content": "active",
            "ads": "active"
        }
    }


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "database": "not connected (simplified mode)",
        "anthropic": "connected" if client else "not configured"
    }


# SEO Agent endpoints
@app.get("/api/seo/status")
async def seo_status():
    return {
        "agent": "seo",
        "status": "limited",
        "note": "Running without database. Use full version for persistence."
    }


@app.get("/api/seo/keywords")
async def get_keywords():
    # Return static keyword list
    keywords = [
        {"keyword": "customer success platform", "priority": "P0"},
        {"keyword": "AI customer success software", "priority": "P0"},
        {"keyword": "churn prediction software", "priority": "P0"},
        {"keyword": "reduce SaaS churn", "priority": "P1"},
        {"keyword": "Gainsight alternative", "priority": "P0"},
    ]
    return {"total": len(keywords), "keywords": keywords}


# Content Agent endpoints
@app.get("/api/content/status")
async def content_status():
    return {
        "agent": "content",
        "status": "operational",
        "note": "Generating content without persistence"
    }


@app.post("/api/content/blog")
async def generate_blog_post(request: BlogPostRequest):
    if not client:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")
    
    logger.info(f"📝 Generating blog post: {request.topic}")
    
    system_prompt = """You are a content writer for Successifier, an AI-native Customer Success Platform.

Write professional, data-driven content that includes:
- 40% churn reduction
- 25% NRR improvement  
- 85% less manual work
- From $79/month
- 14-day free trial

Tone: Professional but approachable. Expert without being academic."""

    user_prompt = f"""Write a blog post about: {request.topic}

Target word count: {request.word_count} words
"""
    
    if request.target_keyword:
        user_prompt += f"\nPrimary keyword: {request.target_keyword}"
    
    user_prompt += """

Structure:
1. Compelling headline
2. Hook paragraph
3. Main content with subheadings
4. Key takeaways
5. CTA

Format as markdown."""
    
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=8192,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        
        content = response.content[0].text
        lines = content.split('\n')
        title = lines[0].replace('#', '').strip() if lines else request.topic
        
        logger.info(f"✅ Blog post generated: {title}")
        
        return {
            "success": True,
            "content": {
                "title": title,
                "content": content,
                "word_count": len(content.split()),
                "status": "generated (not saved)"
            }
        }
    except Exception as e:
        logger.error(f"❌ Error generating blog post: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/content/brand-voice")
async def get_brand_voice():
    return {
        "messaging_pillars": {
            "ai_native": "Built from ground up with AI at the core",
            "affordable": "Enterprise features at startup pricing ($79/mo)",
            "fast_value": "30-minute setup, ROI in 30 days"
        },
        "proof_points": {
            "churn_reduction": "40% churn reduction",
            "nrr_improvement": "25% NRR improvement",
            "efficiency": "85% less manual work",
            "pricing": "from $79/month",
            "trial": "14-day free trial"
        }
    }


# Google Ads Agent endpoints
@app.get("/api/ads/status")
async def ads_status():
    return {
        "agent": "ads",
        "status": "operational",
        "campaigns": 5,
        "note": "Generating RSAs without persistence"
    }


@app.post("/api/ads/rsa/generate")
async def generate_rsa(request: RSARequest):
    if not client:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")
    
    logger.info(f"📢 Generating RSA for {request.campaign}")
    
    system_prompt = """You are a Google Ads copywriter for Successifier.

Include proof points:
- 40% churn reduction
- 25% NRR improvement
- 85% less manual work
- From $79/month
- 14-day free trial"""

    user_prompt = f"""Generate a Google Ads RSA for:

Campaign: {request.campaign}
Ad Group: {request.ad_group}
"""
    
    if request.target_keyword:
        user_prompt += f"Keyword: {request.target_keyword}\n"
    
    user_prompt += """

Generate:
- 10 headlines (max 30 characters each)
- 4 descriptions (max 90 characters each)

Format as JSON:
{
  "headlines": ["headline 1", ...],
  "descriptions": ["description 1", ...]
}"""
    
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        
        import json
        try:
            rsa_data = json.loads(response.content[0].text)
        except:
            # Fallback
            rsa_data = {
                "headlines": [
                    "AI-Native Customer Success Platform",
                    "Reduce Churn by 40% With AI",
                    "From $79/Month — Cancel Anytime",
                    "Setup in 30 Minutes",
                    "25% NRR Improvement",
                    "85% Less Manual Work",
                    "14-Day Free Trial",
                    "Enterprise Features. Startup Pricing.",
                    "Predict Churn Before It Happens",
                    "Automate Your Customer Success"
                ],
                "descriptions": [
                    "AI-native platform that predicts churn and automates CS. 40% churn reduction, 25% NRR improvement.",
                    "From $79/month. Setup in 30 minutes, see ROI in 30 days. 14-day free trial.",
                    "Built for small-to-mid CS teams. AI health scoring and automated playbooks.",
                    "Better than Gainsight at 1/10th the cost. Perfect for growing SaaS companies."
                ]
            }
        
        # Validate character limits
        rsa_data["headlines"] = [h[:30] for h in rsa_data["headlines"][:15]]
        rsa_data["descriptions"] = [d[:90] for d in rsa_data["descriptions"][:4]]
        
        logger.info(f"✅ RSA generated: {len(rsa_data['headlines'])} headlines")
        
        return {
            "success": True,
            "rsa": {
                "campaign": request.campaign,
                "ad_group": request.ad_group,
                "headlines": rsa_data["headlines"],
                "descriptions": rsa_data["descriptions"],
                "status": "generated (not saved)"
            }
        }
    except Exception as e:
        logger.error(f"❌ Error generating RSA: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/ads/campaigns")
async def get_campaigns():
    return {
        "campaigns": {
            "brand": {"name": "Brand Campaign", "keywords": ["successifier"]},
            "core_product": {"name": "Core Product", "keywords": ["customer success platform"]},
            "churn_prevention": {"name": "Churn Prevention", "keywords": ["reduce SaaS churn"]},
            "health_scoring": {"name": "Health Scoring", "keywords": ["customer health score"]},
            "competitor_conquest": {"name": "Competitor Conquest", "keywords": ["gainsight alternative"]}
        }
    }


if __name__ == "__main__":
    import uvicorn
    
    print("\n" + "="*60)
    print("🚀 SAMA 2.0 - Simplified Version")
    print("="*60)
    print("\n📋 Setup:")
    print("1. Create .env.local with ANTHROPIC_API_KEY")
    print("2. This version runs WITHOUT database")
    print("3. Data is NOT persisted")
    print("\n🌐 Starting server...")
    print("="*60 + "\n")
    
    uvicorn.run(
        "main_simple:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
