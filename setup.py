"""
SAMA 2.0 Setup Script
Creates tables in Supabase and initializes data
"""

import sys
from pathlib import Path
from dotenv import load_dotenv

# Load env first
load_dotenv('.env.local', override=True)

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))


def setup():
    """Run initial setup"""
    print("🚀 Setting up SAMA 2.0...\n")
    
    # 1. Test Supabase connection
    print("1️⃣ Testing Supabase connection...")
    try:
        from shared.database import get_supabase
        sb = get_supabase()
        print("✅ Supabase connected!\n")
    except Exception as e:
        print(f"❌ Supabase connection failed: {e}")
        print("\nMake sure SUPABASE_URL and SUPABASE_KEY are set in .env.local")
        print("Get them from Supabase Dashboard → Project Settings → API Keys")
        return
    
    # 2. Create tables
    print("2️⃣ Creating database tables...")
    tables_sql = """
    -- Keywords table
    CREATE TABLE IF NOT EXISTS keywords (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        keyword VARCHAR(255) NOT NULL,
        intent VARCHAR(50),
        priority VARCHAR(10),
        target_page VARCHAR(500),
        current_position INTEGER,
        current_clicks INTEGER DEFAULT 0,
        current_impressions INTEGER DEFAULT 0,
        current_ctr FLOAT DEFAULT 0.0,
        position_history JSONB DEFAULT '[]'::jsonb,
        added_at TIMESTAMPTZ DEFAULT NOW(),
        last_checked_at TIMESTAMPTZ,
        auto_discovered BOOLEAN DEFAULT FALSE
    );

    -- SEO Audits table
    CREATE TABLE IF NOT EXISTS seo_audits (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        audit_date TIMESTAMPTZ DEFAULT NOW(),
        critical_issues JSONB DEFAULT '[]'::jsonb,
        high_issues JSONB DEFAULT '[]'::jsonb,
        medium_issues JSONB DEFAULT '[]'::jsonb,
        low_issues JSONB DEFAULT '[]'::jsonb,
        lcp_score FLOAT,
        inp_score FLOAT,
        cls_score FLOAT,
        total_pages INTEGER,
        pages_with_issues INTEGER,
        broken_links INTEGER,
        duplicate_content INTEGER,
        auto_fixed JSONB DEFAULT '[]'::jsonb,
        summary TEXT,
        recommendations JSONB DEFAULT '[]'::jsonb
    );

    -- Content Pieces table
    CREATE TABLE IF NOT EXISTS content_pieces (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        title VARCHAR(500) NOT NULL,
        content_type VARCHAR(50),
        content TEXT,
        meta_title VARCHAR(200),
        meta_description VARCHAR(500),
        target_keyword VARCHAR(255),
        target_url VARCHAR(500),
        word_count INTEGER,
        status VARCHAR(50) DEFAULT 'draft',
        published_at TIMESTAMPTZ,
        impressions_30d INTEGER DEFAULT 0,
        clicks_30d INTEGER DEFAULT 0,
        avg_position FLOAT,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        created_by VARCHAR(50) DEFAULT 'sama_content',
        approved_by VARCHAR(100)
    );

    -- Backlink Profiles table
    CREATE TABLE IF NOT EXISTS backlink_profiles (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        checked_at TIMESTAMPTZ DEFAULT NOW(),
        total_backlinks INTEGER,
        referring_domains INTEGER,
        domain_rating FLOAT,
        toxic_links JSONB DEFAULT '[]'::jsonb,
        new_links JSONB DEFAULT '[]'::jsonb,
        lost_links JSONB DEFAULT '[]'::jsonb,
        top_domains JSONB DEFAULT '[]'::jsonb
    );

    -- Competitor Analyses table
    CREATE TABLE IF NOT EXISTS competitor_analyses (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        competitor VARCHAR(100) NOT NULL,
        analyzed_at TIMESTAMPTZ DEFAULT NOW(),
        keyword_gaps JSONB DEFAULT '[]'::jsonb,
        keyword_wins JSONB DEFAULT '[]'::jsonb,
        estimated_traffic INTEGER,
        domain_rating FLOAT,
        content_opportunities JSONB DEFAULT '[]'::jsonb
    );
    """
    
    try:
        sb.postgrest.auth(sb.supabase_key)
        # Use Supabase SQL via rpc
        for statement in tables_sql.split(';'):
            stmt = statement.strip()
            if stmt and not stmt.startswith('--'):
                try:
                    sb.rpc("exec_sql", {"query": stmt + ";"}).execute()
                except:
                    pass
        print("✅ Tables created (or already exist)\n")
        print("   ⚠️  If tables weren't created automatically, run the SQL")
        print("   in Supabase Dashboard → SQL Editor. See SETUP_SQL.sql\n")
    except Exception as e:
        print(f"⚠️  Auto-create not available. Run SQL manually in Supabase SQL Editor.")
        print(f"   See file: SETUP_SQL.sql\n")
    
    # 3. Initialize keywords
    print("3️⃣ Initializing SEO keywords...")
    keywords = [
        {"keyword": "customer success platform", "intent": "commercial", "priority": "P0", "target_page": "/"},
        {"keyword": "customer success software", "intent": "commercial", "priority": "P0", "target_page": "/"},
        {"keyword": "reduce churn SaaS", "intent": "informational", "priority": "P0", "target_page": "/blog/reduce-churn"},
        {"keyword": "customer health score", "intent": "informational", "priority": "P1", "target_page": "/features/health-scoring"},
        {"keyword": "net revenue retention", "intent": "informational", "priority": "P1", "target_page": "/blog/nrr-guide"},
        {"keyword": "gainsight alternative", "intent": "commercial", "priority": "P1", "target_page": "/vs/gainsight"},
        {"keyword": "totango alternative", "intent": "commercial", "priority": "P1", "target_page": "/vs/totango"},
        {"keyword": "churnzero alternative", "intent": "commercial", "priority": "P1", "target_page": "/vs/churnzero"},
        {"keyword": "customer onboarding software", "intent": "commercial", "priority": "P1", "target_page": "/features/onboarding"},
        {"keyword": "customer success metrics", "intent": "informational", "priority": "P2", "target_page": "/blog/cs-metrics"},
        {"keyword": "churn prediction", "intent": "informational", "priority": "P2", "target_page": "/features/churn-prediction"},
        {"keyword": "customer expansion revenue", "intent": "informational", "priority": "P2", "target_page": "/blog/expansion-revenue"},
        {"keyword": "AI customer success", "intent": "commercial", "priority": "P0", "target_page": "/features/ai"},
        {"keyword": "customer success automation", "intent": "commercial", "priority": "P1", "target_page": "/features/automation"},
    ]
    
    try:
        # Check if keywords already exist
        existing = sb.table("keywords").select("keyword").execute()
        existing_keywords = [k["keyword"] for k in (existing.data or [])]
        
        new_keywords = [k for k in keywords if k["keyword"] not in existing_keywords]
        
        if new_keywords:
            sb.table("keywords").insert(new_keywords).execute()
            print(f"✅ {len(new_keywords)} keywords initialized")
        else:
            print(f"✅ All {len(keywords)} keywords already exist")
    except Exception as e:
        print(f"⚠️  Keywords not inserted (tables may need to be created first): {e}")
        print("   Run the SQL in SETUP_SQL.sql first, then run this script again.")
    
    print("\n✅ SAMA 2.0 setup complete!")
    print("\nNext steps:")
    print("1. Start SAMA: uvicorn main:app --reload")
    print("2. Visit API docs: http://localhost:8000/docs")
    print("3. Test health: http://localhost:8000/health")


if __name__ == "__main__":
    setup()
