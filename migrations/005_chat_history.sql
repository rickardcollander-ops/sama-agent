-- Chat History Table
-- Stores conversation history for each agent

CREATE TABLE IF NOT EXISTS chat_history (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  agent_name TEXT NOT NULL,
  user_id TEXT DEFAULT 'default_user',
  role TEXT NOT NULL,
  message TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  
  CONSTRAINT chat_history_role_check CHECK (role IN ('user', 'agent'))
);

CREATE INDEX idx_chat_history_agent_user ON chat_history(agent_name, user_id, created_at DESC);
CREATE INDEX idx_chat_history_created_at ON chat_history(created_at DESC);

COMMENT ON TABLE chat_history IS 'Stores conversation history between users and agents';
