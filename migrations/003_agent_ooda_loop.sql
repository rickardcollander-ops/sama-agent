-- Agent OODA Loop Tracking
-- Enables agents to track Observe → Orient → Decide → Act → Reflect cycles

CREATE TABLE IF NOT EXISTS agent_cycles (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  agent_name TEXT NOT NULL,
  cycle_number INTEGER NOT NULL,
  
  -- OBSERVE: Fetch data from external sources
  observe_started_at TIMESTAMPTZ,
  observe_completed_at TIMESTAMPTZ,
  observations JSONB, -- Raw data fetched (GSC, Ads API, Twitter, etc.)
  
  -- ORIENT: Analyze and understand the data
  orient_started_at TIMESTAMPTZ,
  orient_completed_at TIMESTAMPTZ,
  analysis JSONB, -- Insights, patterns, anomalies detected
  
  -- DECIDE: Determine what actions to take
  decide_started_at TIMESTAMPTZ,
  decide_completed_at TIMESTAMPTZ,
  decisions JSONB, -- Actions to take with reasoning
  
  -- ACT: Execute the decided actions
  act_started_at TIMESTAMPTZ,
  act_completed_at TIMESTAMPTZ,
  actions_taken JSONB, -- What was actually executed
  
  -- REFLECT: Evaluate outcomes and learn
  reflect_started_at TIMESTAMPTZ,
  reflect_completed_at TIMESTAMPTZ,
  reflection JSONB, -- Outcomes, learnings, adjustments for next cycle
  
  -- Metadata
  status TEXT NOT NULL DEFAULT 'observing', -- 'observing', 'orienting', 'deciding', 'acting', 'reflecting', 'completed', 'failed'
  error_message TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  completed_at TIMESTAMPTZ,
  
  CONSTRAINT agent_cycles_status_check CHECK (status IN ('observing', 'orienting', 'deciding', 'acting', 'reflecting', 'completed', 'failed'))
);

CREATE INDEX idx_agent_cycles_agent_name ON agent_cycles(agent_name, created_at DESC);
CREATE INDEX idx_agent_cycles_status ON agent_cycles(status, created_at DESC);

-- Agent Learnings: Track what agents learn over time
CREATE TABLE IF NOT EXISTS agent_learnings (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  agent_name TEXT NOT NULL,
  cycle_id UUID REFERENCES agent_cycles(id) ON DELETE CASCADE,
  
  learning_type TEXT NOT NULL, -- 'success', 'failure', 'insight', 'pattern', 'anomaly'
  context JSONB NOT NULL, -- Situation/conditions when this learning occurred
  action_taken JSONB, -- What action was taken
  expected_outcome JSONB, -- What we predicted would happen
  actual_outcome JSONB, -- What actually happened
  
  confidence_score FLOAT DEFAULT 0.5, -- 0.0 to 1.0, how confident are we in this learning
  times_validated INTEGER DEFAULT 0, -- How many times this pattern has been confirmed
  
  created_at TIMESTAMPTZ DEFAULT NOW(),
  
  CONSTRAINT agent_learnings_confidence_check CHECK (confidence_score >= 0.0 AND confidence_score <= 1.0),
  CONSTRAINT agent_learnings_type_check CHECK (learning_type IN ('success', 'failure', 'insight', 'pattern', 'anomaly'))
);

CREATE INDEX idx_agent_learnings_agent_name ON agent_learnings(agent_name, created_at DESC);
CREATE INDEX idx_agent_learnings_cycle_id ON agent_learnings(cycle_id);
CREATE INDEX idx_agent_learnings_type ON agent_learnings(learning_type, confidence_score DESC);

-- Agent Goals: What each agent is trying to achieve
CREATE TABLE IF NOT EXISTS agent_goals (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  agent_name TEXT NOT NULL,
  
  goal_type TEXT NOT NULL, -- 'metric_target', 'optimization', 'exploration'
  description TEXT NOT NULL,
  target_metric TEXT, -- e.g., 'ctr', 'conversions', 'ranking_position'
  target_value FLOAT, -- Target value for the metric
  current_value FLOAT, -- Current value
  
  priority TEXT DEFAULT 'medium', -- 'critical', 'high', 'medium', 'low'
  status TEXT DEFAULT 'active', -- 'active', 'achieved', 'abandoned', 'paused'
  
  created_at TIMESTAMPTZ DEFAULT NOW(),
  achieved_at TIMESTAMPTZ,
  
  CONSTRAINT agent_goals_priority_check CHECK (priority IN ('critical', 'high', 'medium', 'low')),
  CONSTRAINT agent_goals_status_check CHECK (status IN ('active', 'achieved', 'abandoned', 'paused'))
);

CREATE INDEX idx_agent_goals_agent_name ON agent_goals(agent_name, status, priority);

COMMENT ON TABLE agent_cycles IS 'Tracks each OODA loop cycle execution for all agents';
COMMENT ON TABLE agent_learnings IS 'Stores learnings and patterns discovered by agents over time';
COMMENT ON TABLE agent_goals IS 'Defines what each agent is trying to achieve';
