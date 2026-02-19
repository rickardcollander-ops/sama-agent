# Dashboard Spec: AI Visibility (GEO) Tab

## Overview

Add a new **"AI Visibility"** tab to the SAMA dashboard alongside the existing SEO, Content, Ads, Social, and Reviews tabs. The tab surfaces data from the `/api/ai-visibility` endpoints and gives the team a clear picture of how Successifier appears in AI-generated answers (ChatGPT, Perplexity, Claude, Gemini).

---

## 1. Navigation

- **Tab label**: `AI Visibility`
- **Tab icon**: Sparkle / robot icon (e.g. `SparklesIcon` from heroicons)
- **Route**: `/dashboard/ai-visibility`
- **Position**: After Reviews, before Analytics

---

## 2. Page Layout (top → bottom)

```
┌─────────────────────────────────────────────────────────┐
│  SUMMARY ROW (4 stat cards)                             │
├─────────────────────────────────────────────────────────┤
│  MENTION TREND CHART  │  TOP COMPETITORS IN AI (table)  │
├─────────────────────────────────────────────────────────┤
│  VISIBILITY GAPS (table with actions)                   │
├─────────────────────────────────────────────────────────┤
│  RECENT CHECKS (expandable list)                        │
├─────────────────────────────────────────────────────────┤
│  GEO RECOMMENDATIONS PANEL                              │
└─────────────────────────────────────────────────────────┘
```

---

## 3. Components

### 3.1 Summary Row — 4 Stat Cards

| Card | Value | Source |
|------|-------|--------|
| **AI Mention Rate** | `{mention_rate * 100}%` | `GET /api/ai-visibility/summary` → `mention_rate` |
| **Avg Mention Rank** | `#{avg_mention_rank}` or `—` | `summary.avg_mention_rank` |
| **Open Gaps** | `{open_gaps}` | `summary.open_gaps` |
| **Checks (30 days)** | `{total_checks}` | `summary.total_checks` |

Trend indicators: compare to previous period if data available.

---

### 3.2 Mention Trend Chart

- **Type**: Line chart (Recharts or Chart.js)
- **X-axis**: Date (group checks by day)
- **Y-axis**: Mention rate (%)
- **Data**: Derived from `GET /api/ai-visibility/checks` — group by `checked_at` date, compute `successifier_mentioned` rate per day
- **Tooltip**: Show mention count / total for that day

---

### 3.3 Top Competitors in AI (Right of chart)

- **Type**: Horizontal bar chart or ranked list
- **Data**: `summary.top_competitors_in_ai` — `{ gainsight: 12, churnzero: 8, ... }`
- **Label**: "Times competitors appeared in AI answers instead of Successifier (last 30 days)"
- **Each row**: competitor name + count + bar

---

### 3.4 Visibility Gaps Table

**Data**: `GET /api/ai-visibility/gaps`

**Columns**:

| Column | Description |
|--------|-------------|
| Priority | Badge: `high` (red) / `medium` (yellow) / `low` (gray) |
| Category | `competitor_alternative` / `tool_recommendation` / `use_case` / `buying_intent` |
| Query | The AI prompt where we're not mentioned (truncated to 80 chars, expand on hover) |
| Competitor Winning | Who IS being mentioned instead |
| Recommended Action | `create_content` / `optimize_page` / `build_reviews` / `forum_engagement` — with icon |
| Status | Dropdown: open / in_progress / resolved |
| Actions | Button: "Mark In Progress", "Resolve" |

**Interactions**:
- Clicking status dropdown calls `POST /api/ai-visibility/gaps/update`
- Filter by: Priority, Category, Action Type
- Sort by: Priority, Identified Date

---

### 3.5 Recent Checks (Expandable List)

**Data**: `GET /api/ai-visibility/checks?limit=30`

**Default view** (collapsed rows):
- Prompt text (truncated)
- Mentioned: ✅ / ❌
- Rank: `#1`, `#2`, or `—`
- Competitors seen: comma-separated names
- Checked at: relative time ("2 hours ago")

**Expanded row** (click to expand):
- Full AI response text (scrollable, monospace)
- Full list of competitors with rank and context
- Category badge

**Filter bar**: Category selector + "Mentioned only" / "Not mentioned only" toggle

---

### 3.6 GEO Recommendations Panel

- **Trigger**: Button "Generate GEO Recommendations" → calls `POST /api/ai-visibility/recommendations`
- **Loading state**: Spinner + "Analyzing visibility data with Claude..."
- **Output**: Rendered markdown text (5 numbered recommendations)
- **Cache**: Store result in component state; show timestamp of last generation
- **UI**: Collapsible panel at bottom of page

---

## 4. Actions / Toolbar

Top-right of page:

```
[ Run Visibility Check ]   [ Generate Recommendations ]
```

- **Run Visibility Check**: calls `POST /api/ai-visibility/check` (background) → shows toast "Check started, results will appear in ~3 minutes"
- **Generate Recommendations**: triggers GEO Recommendations panel (3.6)

---

## 5. Database Tables to Create (Supabase)

Run these migrations before deploying the dashboard:

```sql
-- AI monitoring check results
CREATE TABLE ai_visibility_checks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    checked_at TIMESTAMPTZ DEFAULT NOW(),
    prompt TEXT NOT NULL,
    prompt_category VARCHAR(100),
    ai_response TEXT,
    successifier_mentioned BOOLEAN DEFAULT FALSE,
    mention_rank INTEGER,
    mention_context TEXT,
    mention_sentiment VARCHAR(20),
    competitors_mentioned JSONB DEFAULT '[]',
    sources_cited JSONB DEFAULT '[]',
    check_source VARCHAR(50) DEFAULT 'claude_proxy'
);

CREATE INDEX idx_ai_visibility_checks_checked_at ON ai_visibility_checks(checked_at DESC);
CREATE INDEX idx_ai_visibility_checks_mentioned ON ai_visibility_checks(successifier_mentioned);

-- Visibility gaps / opportunities
CREATE TABLE ai_visibility_gaps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    identified_at TIMESTAMPTZ DEFAULT NOW(),
    prompt TEXT NOT NULL,
    prompt_category VARCHAR(100),
    competitor_winning VARCHAR(100),
    gap_type VARCHAR(50) DEFAULT 'not_mentioned',
    recommended_action TEXT,
    action_type VARCHAR(50),
    priority VARCHAR(10) DEFAULT 'medium',
    status VARCHAR(20) DEFAULT 'open'
);

CREATE INDEX idx_ai_visibility_gaps_status ON ai_visibility_gaps(status);
CREATE INDEX idx_ai_visibility_gaps_priority ON ai_visibility_gaps(priority);

-- Citation sources (future use)
CREATE TABLE ai_citations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    discovered_at TIMESTAMPTZ DEFAULT NOW(),
    source_url VARCHAR(1000),
    source_domain VARCHAR(255),
    source_type VARCHAR(50),
    favors_successifier BOOLEAN,
    competitors_mentioned JSONB DEFAULT '[]',
    times_cited INTEGER DEFAULT 1,
    last_cited_at TIMESTAMPTZ
);
```

---

## 6. API Endpoints Used by Dashboard

| Method | Endpoint | Used By |
|--------|----------|---------|
| `GET` | `/api/ai-visibility/status` | Health check on load |
| `GET` | `/api/ai-visibility/summary` | Stat cards + competitor chart |
| `GET` | `/api/ai-visibility/checks?limit=30` | Recent checks list + trend chart |
| `GET` | `/api/ai-visibility/gaps?limit=50` | Gaps table |
| `POST` | `/api/ai-visibility/check` | "Run Check" button |
| `POST` | `/api/ai-visibility/gaps/update` | Status dropdown in gaps table |
| `POST` | `/api/ai-visibility/recommendations` | GEO recommendations panel |

---

## 7. Scheduling

Add to the weekly automation schedule (same pattern as SEO audit):

- **Frequency**: Weekly (Mondays at 08:00 UTC)
- **Endpoint**: `POST /api/ai-visibility/check`
- **Purpose**: Track visibility trends over time

Optionally also run after major content publishes (triggered via event bus: `content_published` → queue a check 24h later).

---

## 8. Empty States

| Situation | Message |
|-----------|---------|
| No checks run yet | "No visibility data yet. Run your first AI check to see how Successifier appears in AI answers." + CTA button |
| All gaps resolved | "🎉 No open gaps! Run a new check to discover fresh opportunities." |
| Recommendations not generated | "Click 'Generate Recommendations' to get AI-powered GEO advice based on your visibility data." |
