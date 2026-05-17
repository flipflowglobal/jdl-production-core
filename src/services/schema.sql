CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY,
  clerk_user_id VARCHAR NOT NULL,
  email VARCHAR,
  name VARCHAR,
  subscription_tier VARCHAR,
  subscription_expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_clerk_user_id ON users (clerk_user_id);

CREATE TABLE IF NOT EXISTS wallet_vault (
  id SERIAL PRIMARY KEY,
  user_id VARCHAR NOT NULL,
  wallet_type VARCHAR NOT NULL,
  label VARCHAR,
  address VARCHAR NOT NULL,
  encrypted_private_key TEXT,
  encrypted_mnemonic TEXT,
  iv VARCHAR,
  auth_tag VARCHAR,
  chain VARCHAR DEFAULT 'multi-chain',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (user_id, address)
);

CREATE INDEX IF NOT EXISTS idx_wallet_vault_user_id ON wallet_vault (user_id);

CREATE TABLE IF NOT EXISTS agents (
  id VARCHAR PRIMARY KEY,
  user_id VARCHAR NOT NULL,
  name VARCHAR,
  strategy_id VARCHAR,
  strategy VARCHAR,
  strategy_category VARCHAR,
  algorithm VARCHAR,
  status VARCHAR,
  capital NUMERIC,
  risk_profile VARCHAR,
  chains TEXT[],
  parameters JSONB,
  wallet_address VARCHAR,
  performance JSONB,
  health JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agents_user_id ON agents (user_id);

CREATE TABLE IF NOT EXISTS agent_wallets (
  id SERIAL PRIMARY KEY,
  agent_id VARCHAR NOT NULL,
  user_id VARCHAR NOT NULL,
  address VARCHAR NOT NULL,
  encrypted_private_key TEXT,
  encrypted_mnemonic TEXT,
  iv VARCHAR,
  auth_tag VARCHAR,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (address)
);

CREATE INDEX IF NOT EXISTS idx_agent_wallets_agent_id ON agent_wallets (agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_wallets_user_id ON agent_wallets (user_id);

CREATE TABLE IF NOT EXISTS trades (
  id UUID PRIMARY KEY,
  agent_id VARCHAR,
  user_id VARCHAR NOT NULL,
  tx_hash VARCHAR,
  chain VARCHAR,
  from_token VARCHAR,
  to_token VARCHAR,
  from_amount NUMERIC,
  to_amount NUMERIC,
  pnl NUMERIC,
  fee_paid NUMERIC,
  system_fee NUMERIC,
  algorithm VARCHAR,
  confidence NUMERIC,
  status VARCHAR,
  error TEXT,
  metadata JSONB,
  executed_at TIMESTAMPTZ DEFAULT NOW(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trades_agent_id ON trades (agent_id);
CREATE INDEX IF NOT EXISTS idx_trades_user_id ON trades (user_id);

CREATE TABLE IF NOT EXISTS system_fees (
  id SERIAL PRIMARY KEY,
  type VARCHAR,
  amount VARCHAR,
  currency VARCHAR,
  from_address VARCHAR,
  to_address VARCHAR,
  tx_hash VARCHAR,
  chain VARCHAR,
  metadata JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS gc_subscriptions (
  id VARCHAR PRIMARY KEY,
  clerk_user_id VARCHAR NOT NULL,
  email VARCHAR,
  plan VARCHAR,
  gc_billing_request_id VARCHAR,
  gc_mandate_id VARCHAR,
  gc_customer_id VARCHAR,
  gc_subscription_id VARCHAR,
  status VARCHAR DEFAULT 'pending',
  amount_pence NUMERIC,
  is_recurring BOOLEAN DEFAULT TRUE,
  expires_at TIMESTAMP,
  next_charge_date DATE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gc_subscriptions_clerk_user_id ON gc_subscriptions (clerk_user_id);
CREATE INDEX IF NOT EXISTS idx_gc_subscriptions_billing_request ON gc_subscriptions (gc_billing_request_id);
CREATE INDEX IF NOT EXISTS idx_gc_subscriptions_mandate_id ON gc_subscriptions (gc_mandate_id);
CREATE INDEX IF NOT EXISTS idx_gc_subscriptions_subscription_id ON gc_subscriptions (gc_subscription_id);

CREATE TABLE IF NOT EXISTS user_preferences (
  clerk_user_id TEXT PRIMARY KEY,
  preferences JSONB NOT NULL DEFAULT '{}',
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
