# SAMA 2.0 Quick Start Guide

Get SAMA 2.0 running locally in 5 minutes.

## Prerequisites

- Python 3.12+
- Docker Desktop (for Redis + PostgreSQL)
- Anthropic API key

## Step 1: Start Infrastructure

```bash
# From the sama-agent directory
cd ../shared
docker-compose up -d

# Verify services are running
docker-compose ps
```

You should see:
- `successifier-redis` on port 6379
- `successifier-postgres` on port 5432

## Step 2: Setup Python Environment

```bash
# Back to sama-agent directory
cd ../sama-agent

# Create virtual environment
python -m venv venv

# Activate (Windows)
venv\Scripts\activate

# Activate (Mac/Linux)
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Step 3: Configure Environment

```bash
# Copy example env file
cp .env.example .env.local

# Edit .env.local and add your API key
# Minimum required:
ANTHROPIC_API_KEY=your-key-here
DATABASE_URL=postgresql://sama_user:sama_password@localhost:5432/sama
REDIS_URL=redis://localhost:6379/0
```

## Step 4: Initialize Database

```bash
# Run setup script
python setup.py
```

This will:
- Create database tables
- Initialize 14 target keywords for successifier.com
- Set up SEO tracking

## Step 5: Start SAMA

```bash
# Start the FastAPI server
uvicorn main:app --reload
```

SAMA will start on http://localhost:8000

## Step 6: Test It Works

Open your browser to:
- **API Docs:** http://localhost:8000/docs
- **Health Check:** http://localhost:8000/health

### Test SEO Agent

```bash
# Initialize keywords
curl -X POST http://localhost:8000/api/seo/initialize

# Get keyword status
curl http://localhost:8000/api/seo/keywords

# Run SEO audit (sync for testing)
curl -X POST http://localhost:8000/api/seo/audit/sync
```

## What's Working

✅ **SEO Agent:**
- 14 target keywords initialized
- Weekly audit workflow
- Keyword tracking system
- Event bus integration with LinkedIn Agent

✅ **Infrastructure:**
- FastAPI server with async support
- PostgreSQL with pgvector
- Redis event bus
- Claude Sonnet 4.5 integration

✅ **API Endpoints:**
- `/api/seo/*` - SEO operations
- `/api/orchestrator/*` - Agent coordination
- All other agents (placeholder routes)

## Next Steps

### 1. Enable Google Search Console API

To get real keyword data:
1. Go to https://console.cloud.google.com
2. Enable Google Search Console API
3. Create OAuth credentials
4. Add to `.env.local`:
   ```
   GOOGLE_CLIENT_ID=your-id
   GOOGLE_CLIENT_SECRET=your-secret
   ```

### 2. Add Semrush API

For keyword rankings:
1. Sign up at https://www.semrush.com/api/
2. Get API key
3. Add to `.env.local`:
   ```
   SEMRUSH_API_KEY=your-key
   ```

### 3. Connect LinkedIn Agent

The LinkedIn Agent can send events to SAMA:
1. Make sure Redis is running (shared infrastructure)
2. Both agents will communicate via `sama:events` stream
3. Test event flow in Redis Commander: http://localhost:8081

## Troubleshooting

### "Connection refused" errors

```bash
# Check if infrastructure is running
cd ../shared
docker-compose ps

# Restart if needed
docker-compose restart
```

### "Module not found" errors

```bash
# Make sure venv is activated
venv\Scripts\activate  # Windows
source venv/bin/activate  # Mac/Linux

# Reinstall dependencies
pip install -r requirements.txt
```

### Database errors

```bash
# Reset database
cd ../shared
docker-compose down
docker volume rm shared_postgres-data
docker-compose up -d postgres

# Re-run setup
cd ../sama-agent
python setup.py
```

### Port 8000 already in use

```bash
# Use different port
uvicorn main:app --reload --port 8001
```

## Development Workflow

### Running Tests

```bash
pytest tests/ -v
```

### Code Formatting

```bash
black .
ruff check .
```

### Viewing Logs

```bash
# SAMA logs: Check terminal where uvicorn is running

# Redis logs
docker logs -f successifier-redis

# PostgreSQL logs
docker logs -f successifier-postgres
```

### Monitoring Event Bus

```bash
# Connect to Redis
docker exec -it successifier-redis redis-cli

# View event stream
XREAD COUNT 10 STREAMS sama:events 0

# Monitor in real-time
XREAD BLOCK 0 STREAMS sama:events $
```

## API Examples

### Run SEO Audit

```bash
curl -X POST http://localhost:8000/api/seo/audit/sync \
  -H "Content-Type: application/json" | jq
```

### Get Top Performing Keywords

```bash
curl http://localhost:8000/api/seo/keywords/top-performers | jq
```

### Process Marketing Goal

```bash
curl -X POST http://localhost:8000/api/orchestrator/process-goal \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Improve organic traffic by 50% in 90 days",
    "context": {
      "current_metrics": "1000 monthly visits",
      "constraints": "Budget: $2000/month"
    }
  }' | jq
```

## Architecture

```
SAMA 2.0 (Port 8000)
├── Orchestrator (Claude Sonnet 4.5)
│   └── Coordinates all agents
├── SEO Agent ✅ IMPLEMENTED
│   ├── Keyword tracking (14 keywords)
│   ├── Weekly audits
│   └── Competitor analysis
├── Content Agent (placeholder)
├── Ads Agent (placeholder)
├── Social Agent (placeholder)
├── Review Agent (placeholder)
└── Analytics Agent (placeholder)

Shared Infrastructure
├── PostgreSQL (Port 5432)
├── Redis (Port 6379)
└── Event Bus (Redis Streams)
```

## What's Next

According to the SAMA 2.0 roadmap:

**Phase 2: Content Agent (Weeks 5-6)**
- Brand voice engine
- Blog post generation
- Landing page creation
- SEO validation

**Phase 3: Google Ads Agent (Weeks 7-8)**
- Campaign management
- RSA generation
- Bid optimization

Want to implement the next phase? Let me know!

## Support

- **Documentation:** See README.md
- **Full Spec:** See docs/SAMA_2.0_SPEC.md
- **Event Bus:** See ../shared/README.md
