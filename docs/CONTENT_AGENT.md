# Content Agent Guide

Complete guide to using SAMA's Content Agent for generating SEO-optimized content.

## Overview

The Content Agent generates high-quality, on-brand content for Successifier across multiple formats:

- **Blog Posts** (1,500-2,500 words)
- **Landing Pages** (800-1,200 words)
- **Comparison Pages** (2,000-3,000 words)
- **Social Media Posts** (Twitter/X, LinkedIn)

All content follows Successifier's brand voice and includes SEO optimization.

## Brand Voice

### Messaging Pillars

1. **AI-Native (Not AI-Bolted-On)**
   - Built from the ground up with AI at the core
   - Not retrofitted onto legacy software

2. **Affordable (Enterprise Features at Startup Pricing)**
   - Enterprise-grade capabilities without enterprise pricing
   - From $79/month

3. **Fast Time-to-Value**
   - 30-minute setup
   - ROI in 30 days

### Proof Points (Always Cite)

- 40% churn reduction
- 25% NRR improvement
- 85% less manual work
- From $79/month
- 14-day free trial

### Target Persona

- **Title:** VP/Director of Customer Success
- **Company:** B2B SaaS, 500-10,000 customers
- **Team Size:** 3-15 CS team members
- **Pain Points:** Manual work, scaling challenges, churn visibility

## Content Pillars

1. **Churn Prevention** - Detecting and reducing churn
2. **Health Scoring** - Customer health scoring frameworks
3. **CS Automation** - Automating workflows and playbooks
4. **NRR Growth** - Expansion revenue strategies
5. **Platform Comparisons** - Gainsight, Totango, ChurnZero alternatives
6. **Onboarding** - Customer onboarding best practices

## API Usage

### Generate Blog Post

```bash
curl -X POST http://localhost:8000/api/content/blog \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "How to Reduce SaaS Churn by 40% Using AI",
    "target_keyword": "reduce SaaS churn",
    "word_count": 2000,
    "pillar": "churn_prevention"
  }'
```

**Response:**
```json
{
  "success": true,
  "content": {
    "id": "uuid",
    "title": "How to Reduce SaaS Churn by 40% Using AI",
    "content": "# Full markdown content...",
    "meta_description": "Learn how AI-native platforms...",
    "word_count": 2043,
    "validation": {
      "score": 95,
      "issues": [],
      "proof_points_used": 4,
      "passed": true
    },
    "status": "draft"
  }
}
```

### Generate Landing Page

```bash
curl -X POST http://localhost:8000/api/content/landing-page \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "AI-Powered Customer Health Scoring",
    "target_keyword": "customer health score tool",
    "use_case": "SaaS companies with 500+ customers"
  }'
```

### Generate Comparison Page

```bash
curl -X POST http://localhost:8000/api/content/comparison \
  -H "Content-Type: application/json" \
  -d '{
    "competitor": "gainsight"
  }'
```

Generates: "Successifier vs Gainsight" comparison page

### Generate Social Post

```bash
curl -X POST http://localhost:8000/api/content/social \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "Why AI-native beats AI-bolted-on for CS",
    "platform": "twitter",
    "style": "educational"
  }'
```

**Platforms:**
- `twitter` - X/Twitter posts (1-3 tweets)
- `linkedin` - LinkedIn posts (150-300 words)

**Styles:**
- `educational` - Teach something valuable
- `announcement` - Product updates, launches
- `engagement` - Questions, polls, discussion

### Get Content Library

```bash
curl http://localhost:8000/api/content/library
```

Lists all generated content with metadata.

### Get Specific Content

```bash
curl http://localhost:8000/api/content/library/{content_id}
```

Returns full content including markdown.

### Get Brand Voice Guidelines

```bash
curl http://localhost:8000/api/content/brand-voice
```

Returns complete brand voice profile.

## Content Workflow

### 1. SEO Agent Discovers Keyword Opportunity

```
SEO Agent: "Keyword 'customer health score tool' moving up to position 12"
↓
Event Bus: keyword_opportunity
↓
Content Agent: Receives event
```

### 2. Content Agent Generates Blog Post

```python
# Automatically triggered or manual
result = await content_agent.generate_blog_post(
    topic="Building Effective Customer Health Scores",
    target_keyword="customer health score tool",
    pillar="health_scoring"
)
```

### 3. Content Validation

```python
validation = brand_voice.validate_content(result['content'])

# Checks:
# - Proof points cited (40% churn reduction, etc.)
# - Avoided terms not used (client success, headcount reduction)
# - Word count appropriate
# - Keyword density optimal (0.5-2.5%)
```

### 4. Human Approval (Configurable)

```env
# In .env.local
AUTO_PUBLISH_BLOG_POSTS=false  # Requires approval
AUTO_PUBLISH_LANDING_PAGES=false
AUTO_PUBLISH_SOCIAL_POSTS=true  # Auto-publish
```

### 5. SEO Optimization

```bash
curl -X POST http://localhost:8000/api/content/optimize-seo \
  -H "Content-Type: application/json" \
  -d '{
    "content_id": "uuid",
    "target_keyword": "customer health score tool"
  }'
```

Returns SEO analysis:
```json
{
  "keyword_count": 8,
  "keyword_density": 1.2,
  "in_title": true,
  "in_first_paragraph": true,
  "optimal": true
}
```

### 6. Publish & Track

Content is published to successifier.com and tracked for:
- Organic impressions (30-day)
- Clicks (30-day)
- Average position
- Conversion rate

## Examples

### Example 1: Blog Post for Keyword Gap

**Scenario:** SEO Agent finds "reduce SaaS churn" ranking at position 15

```bash
# Generate blog post
curl -X POST http://localhost:8000/api/content/blog \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "10 Proven Strategies to Reduce SaaS Churn in 2026",
    "target_keyword": "reduce SaaS churn",
    "word_count": 2500,
    "pillar": "churn_prevention"
  }'
```

**Output:**
- 2,500-word comprehensive guide
- Includes all Successifier proof points
- SEO-optimized for "reduce SaaS churn"
- Internal links to product pages
- Clear CTA to try Successifier

### Example 2: Comparison Page

**Scenario:** Competitor analysis shows opportunity for "Gainsight alternative"

```bash
curl -X POST http://localhost:8000/api/content/comparison \
  -H "Content-Type: application/json" \
  -d '{"competitor": "gainsight"}'
```

**Output:**
- Fair but favorable comparison
- Feature-by-feature breakdown
- Pricing comparison (Gainsight: $$$, Successifier: $79/mo)
- Use case examples
- Migration guide
- Strong CTA

### Example 3: Social Content from Blog

**Scenario:** Blog post published, create social promotion

```bash
# Generate Twitter thread
curl -X POST http://localhost:8000/api/content/social \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "3 churn signals most CS teams miss (and how AI catches them)",
    "platform": "twitter",
    "style": "educational"
  }'
```

**Output:**
```
Tweet 1: Most CS teams only see churn when it's too late.

Here are 3 early signals that AI can catch (but humans miss):

Tweet 2: 1. Declining feature adoption

Not just "are they logging in" but "are they using the features that drive value?"

AI tracks 50+ usage patterns. Humans track 3-5.

Tweet 3: 2. Support ticket sentiment shift

A customer going from "how do I..." to "why doesn't..." is a red flag.

AI analyzes every ticket. Humans read a sample.

Tweet 4: 3. Engagement pattern changes

Meeting frequency drops. Response times increase. Email tone shifts.

AI detects these micro-signals before they become macro problems.

Want to see how? → [link]
```

## Content Templates

### Blog Post Structure

```markdown
# [Compelling Headline with Keyword]

[Hook paragraph - grab attention immediately]

## Introduction

[Set up the problem, establish credibility]

## [Main Section 1]

[Content with data, examples]

## [Main Section 2]

[Content with data, examples]

## [Main Section 3]

[Content with data, examples]

## Key Takeaways

- Takeaway 1
- Takeaway 2
- Takeaway 3

## How Successifier Helps

[Product pitch with proof points]

## Conclusion

[Summary + CTA]
```

### Landing Page Structure

```markdown
# [Value Proposition Headline]

## [Subheadline - Expand on Value]

### The Problem

[Pain points your persona faces]

### The Solution

[How Successifier solves it]

### Key Features

- Feature 1 (with benefit)
- Feature 2 (with benefit)
- Feature 3 (with benefit)

### Proof Points

- 40% churn reduction
- 25% NRR improvement
- 85% less manual work

### Get Started

[CTA - 14-day free trial, from $79/month]

### FAQ

**Q: [Common question]**
A: [Clear answer]
```

## Best Practices

### DO

✅ Use specific data and metrics  
✅ Cite Successifier proof points  
✅ Address persona pain points directly  
✅ Keep sentences concise and scannable  
✅ Use active voice  
✅ Include clear CTAs  

### DON'T

❌ Use buzzwords without substance  
❌ Make claims without data  
❌ Use "client success" (say "customer success")  
❌ Say "headcount reduction" (say "less manual work")  
❌ Use clichés like "game-changer"  
❌ Keyword stuff  

## Integration with Other Agents

### SEO Agent → Content Agent

```
SEO Agent discovers keyword opportunity
↓
Publishes event: keyword_opportunity
↓
Content Agent receives event
↓
Generates blog post targeting keyword
↓
Publishes event: content_published
↓
SEO Agent tracks new content performance
```

### Content Agent → Social Agent

```
Content Agent publishes blog post
↓
Publishes event: content_published
↓
Social Agent receives event
↓
Generates social posts promoting blog
↓
Schedules posts across platforms
```

### Content Agent → LinkedIn Agent

```
Content Agent identifies trending topic
↓
Publishes event: topic_trending
↓
LinkedIn Agent receives event
↓
Creates LinkedIn content on same topic
↓
Cross-platform amplification
```

## Monitoring & Analytics

Track content performance:

```bash
# Get content library with performance metrics
curl http://localhost:8000/api/content/library
```

**Metrics tracked:**
- Impressions (30-day)
- Clicks (30-day)
- Average position
- Keyword rankings
- Conversion rate

## Troubleshooting

### Content doesn't match brand voice

Check validation score:
```python
validation = brand_voice.validate_content(content)
if validation['score'] < 70:
    print(validation['issues'])
```

### Keyword density too high/low

Use SEO optimization:
```bash
curl -X POST http://localhost:8000/api/content/optimize-seo \
  -d '{"content_id": "uuid", "target_keyword": "keyword"}'
```

### Content not auto-publishing

Check settings:
```env
AUTO_PUBLISH_BLOG_POSTS=true
```

## Next Steps

- **Phase 3:** Google Ads Agent (RSA generation, ad copy)
- **Phase 4:** Social Agent (scheduling, engagement)
- **Phase 5:** Review Agent (review responses)

---

**Documentation:** See main README.md  
**API Docs:** http://localhost:8000/docs  
**Brand Voice:** GET /api/content/brand-voice
