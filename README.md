# SAMA 2.0 - Successifier Autonomous Marketing Agent

Complete autonomous marketing system for successifier.com covering SEO, Google Ads, Social Media, Review Management, and Analytics.

## Architecture

SAMA 2.0 uses a hierarchical multi-agent architecture:

```
Orchestrator (Claude Sonnet 4.5)
    ├── SEO Agent ✅
    ├── Content Agent ✅
    ├── Ads Agent ✅
    ├── Social Agent ✅
    ├── Review Agent ✅
    └── Analytics Agent ✅
```

**✅ ALL 6 AGENTS IMPLEMENTED:**

1. **SEO Agent** - Keyword tracking, weekly audits, competitor analysis, GSC/Semrush integration
2. **Content Agent** - Blog posts, landing pages, comparison pages, social content with brand voice
3. **Ads Agent** - Google Ads campaign management, RSA generation, bid optimization, negative keyword harvesting
4. **Social Agent** - X/Twitter post generation, scheduling, engagement monitoring, reply automation
5. **Review Agent** - G2/Capterra/Trustpilot management, review responses, request automation
6. **Analytics Agent** - Cross-channel attribution, ROI calculation, automated insights, weekly reports

## Tech Stack

- **Framework:** Python 3.12 + FastAPI
- **AI:** Anthropic Claude Sonnet 4.5
- **Agent Framework:** LangGraph
- **Workflow Engine:** Temporal.io
- **Database:** PostgreSQL 16 + pgvector
- **Vector Store:** Pinecone
- **Cache/Queue:** Redis + Celery
- **Monitoring:** Sentry + Datadog

## Quick Start

### 1. Install Dependencies

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env.local
# Edit .env.local with your API keys
```

### 3. Start Redis (Event Bus)

```bash
# Using Docker
docker run -d -p 6379:6379 redis:7-alpine

# Or use shared Redis from ../shared/
```

### 4. Run Database Migrations

```bash
alembic upgrade head
```

### 5. Start SAMA

```bash
uvicorn main:app --reload --port 8000
```

SAMA will be available at http://localhost:8000

## API Documentation

Once running, visit:
- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc

## Agent Endpoints

### Orchestrator
- `POST /api/orchestrator/process-goal` - Process marketing goal
- `GET /api/orchestrator/status` - Agent status

### SEO Agent
- `POST /api/seo/audit` - Run technical SEO audit
- `GET /api/seo/keywords` - Get keyword rankings
- `GET /api/seo/status` - Agent status

### Content Agent
- `POST /api/content/generate` - Generate content
- `GET /api/content/status` - Agent status

### Ads Agent
- `GET /api/ads/campaigns` - List campaigns
- `GET /api/ads/status` - Agent status

### Social Agent
- `POST /api/social/schedule` - Schedule post
- `GET /api/social/status` - Agent status

### Review Agent
- `GET /api/reviews/platforms` - List platforms
- `GET /api/reviews/status` - Agent status

### Analytics Agent
- `GET /api/analytics/report` - Get reports
- `GET /api/analytics/status` - Agent status

## Event Bus Integration

SAMA communicates with the LinkedIn Agent via Redis Streams:

```python
from shared.event_bus import event_bus

# Publish event to LinkedIn Agent
await event_bus.publish(
    event_type="content_published",
    target_agent="linkedin_agent",
    data={
        "topic": "churn_prevention",
        "url": "https://successifier.com/blog/reduce-saas-churn"
    }
)

# Subscribe to events
await event_bus.subscribe("keyword_discovered", handle_keyword)
```

## Development

### Project Structure

```
sama-agent/
├── agents/              # Agent implementations
│   ├── orchestrator.py
│   ├── seo.py
│   ├── content.py
│   ├── ads.py
│   ├── social.py
│   ├── reviews.py
│   └── analytics.py
├── api/                 # FastAPI routes
│   └── routes/
├── workflows/           # Temporal workflows
├── shared/              # Shared utilities
│   ├── config.py
│   ├── database.py
│   ├── event_bus.py
│   └── monitoring.py
├── docs/                # Documentation
├── tests/               # Test suite
└── main.py              # Application entry point
```

### Running Tests

```bash
pytest tests/ -v --cov=.
```

### Code Quality

```bash
# Format code
black .

# Lint
ruff check .

# Type check
mypy .
```

## Deployment

SAMA 2.0 is designed to run on AWS ECS Fargate:

```bash
# Build Docker image
docker build -t sama-agent:latest .

# Push to ECR
aws ecr get-login-password | docker login --username AWS --password-stdin <ecr-url>
docker tag sama-agent:latest <ecr-url>/sama-agent:latest
docker push <ecr-url>/sama-agent:latest

# Deploy via Terraform
cd terraform/
terraform apply
```

## Environment Variables

See `.env.example` for all required variables. Key ones:

- `ANTHROPIC_API_KEY` - Claude API key
- `DATABASE_URL` - PostgreSQL connection string
- `REDIS_URL` - Redis connection string
- `GOOGLE_ADS_*` - Google Ads API credentials
- `SEMRUSH_API_KEY` - Semrush API key
- `TWITTER_*` - Twitter API credentials

## Documentation

- [Full SAMA 2.0 Specification](./docs/SAMA_2.0_SPEC.md)
- [SEO Agent Guide](./docs/SEO_AGENT.md)
- [Content Agent Guide](./docs/CONTENT_AGENT.md)
- [Event Bus Protocol](../shared/docs/EVENT_BUS.md)

## License

Proprietary - Successifier AB
