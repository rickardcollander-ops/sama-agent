"""Test Anthropic API with real key"""
import asyncio
from agents.content import content_agent

async def test_content_generation():
    print("🧪 Testing Content Agent with Anthropic API...\n")
    
    # Test 1: Short blog post
    print("1️⃣ Generating short blog post...")
    try:
        result = await content_agent.generate_blog_post(
            topic="5 Early Warning Signs of Customer Churn",
            target_keyword="customer churn signs",
            word_count=800,
            pillar="churn_prevention"
        )
        
        print("✅ Blog post generated!")
        print(f"   Title: {result.get('title')}")
        print(f"   Word count: {result.get('word_count')}")
        print(f"   Status: {result.get('status')}")
        print(f"   Validation score: {result.get('validation', {}).get('score')}")
        print(f"   Content preview: {result.get('content', '')[:150]}...\n")
        
    except Exception as e:
        print(f"❌ Error: {e}\n")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 2: Social post
    print("2️⃣ Generating Twitter post...")
    try:
        result = await content_agent.generate_social_post(
            topic="Why AI-native beats AI-bolted-on for Customer Success",
            platform="twitter",
            style="educational"
        )
        
        print("✅ Social post generated!")
        print(f"   Platform: {result.get('platform')}")
        print(f"   Post: {result.get('content')}\n")
        
    except Exception as e:
        print(f"❌ Error: {e}\n")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 3: Landing page
    print("3️⃣ Generating landing page...")
    try:
        result = await content_agent.generate_landing_page(
            topic="AI-Powered Customer Health Scoring",
            target_keyword="customer health score tool",
            use_case="SaaS companies with 500+ customers"
        )
        
        print("✅ Landing page generated!")
        print(f"   Title: {result.get('title')}")
        print(f"   Word count: {result.get('word_count')}")
        print(f"   Meta description: {result.get('meta_description')[:100]}...\n")
        
    except Exception as e:
        print(f"❌ Error: {e}\n")
        import traceback
        traceback.print_exc()
        return False
    
    print("🎉 All Content Agent tests passed!")
    return True

if __name__ == "__main__":
    success = asyncio.run(test_content_generation())
    exit(0 if success else 1)
