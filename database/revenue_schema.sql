-- ════════════════════════════════════════════════════════════════════════════
-- REVENUE SCHEMA & TRIGGERS
-- Projects: D.L, Aureon, FlipFlow, NEXUS-ARB
-- Deploy to: ~/dl.2/data/aureon.db, ~/flipflow/data/flipflow.db, etc.
-- ════════════════════════════════════════════════════════════════════════════

-- ─────────────────────────────────────────────────────────────────────────
-- 1. FLASH TRADES TABLE (Core arbitrage execution log)
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS flash_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    project TEXT NOT NULL,                     -- 'D.L', 'Aureon', 'FlipFlow', 'NEXUS-ARB'
    chain TEXT NOT NULL,                       -- 'ethereum', 'arbitrum', 'optimism', 'base', 'polygon', 'bsc'
    asset_borrowed TEXT NOT NULL,              -- 'USDC', 'USDT', 'ETH', 'DAI'
    amount_borrowed REAL NOT NULL,             -- Amount in token units (NOT wei)
    fee_paid REAL NOT NULL,                    -- Aave flash loan fee
    intermediate_token TEXT,                   -- Token used in arbitrage leg
    buy_dex TEXT,                              -- 'uniswap_v3', 'curve', 'balancer'
    sell_dex TEXT,                             -- 'uniswap_v3', 'curve', 'balancer'
    gross_profit REAL NOT NULL,                -- Profit before gas
    gas_cost REAL NOT NULL,                    -- Gas spent in USD
    net_profit REAL NOT NULL,                  -- profit - gas_cost
    tx_hash TEXT UNIQUE NOT NULL,              -- On-chain transaction hash
    initiator_address TEXT,                    -- Owner/bot address that called it
    contract_address TEXT,                     -- AureonPayProcessor deployment address
    status TEXT DEFAULT 'success',             -- 'success', 'failed', 'pending', 'reverted'
    error_msg TEXT,                            -- Error message if failed
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_flash_trades_chain ON flash_trades(chain);
CREATE INDEX IF NOT EXISTS idx_flash_trades_project ON flash_trades(project);
CREATE INDEX IF NOT EXISTS idx_flash_trades_status ON flash_trades(status);
CREATE INDEX IF NOT EXISTS idx_flash_trades_tx_hash ON flash_trades(tx_hash);

-- ─────────────────────────────────────────────────────────────────────────
-- 2. WITHDRAWALS TABLE (Profit extraction from contract to wallet)
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS withdrawals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    project TEXT NOT NULL,
    chain TEXT NOT NULL,
    token TEXT NOT NULL,                       -- Token withdrawn
    amount REAL NOT NULL,                      -- Amount in token units
    from_contract TEXT NOT NULL,               -- AureonPayProcessor address
    to_address TEXT NOT NULL,                  -- Owner wallet receiving funds
    tx_hash TEXT UNIQUE NOT NULL,
    gas_cost REAL,
    status TEXT DEFAULT 'success',             -- 'success', 'failed', 'pending'
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_withdrawals_chain ON withdrawals(chain);
CREATE INDEX IF NOT EXISTS idx_withdrawals_token ON withdrawals(token);

-- ─────────────────────────────────────────────────────────────────────────
-- 3. REVENUE SUMMARY TABLE (Aggregated per chain/project)
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS revenue_summary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    chain TEXT NOT NULL,
    total_trades INTEGER DEFAULT 0,            -- Count of flash trades
    total_borrowed REAL DEFAULT 0,
    total_fees_paid REAL DEFAULT 0,
    total_gross_profit REAL DEFAULT 0,
    total_gas_spent REAL DEFAULT 0,
    total_net_profit REAL DEFAULT 0,           -- gross_profit - gas_spent
    successful_trades INTEGER DEFAULT 0,
    failed_trades INTEGER DEFAULT 0,
    total_withdrawn REAL DEFAULT 0,            -- Total amount withdrawn to wallet
    last_trade_timestamp DATETIME,
    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project, chain)
);

CREATE INDEX IF NOT EXISTS idx_revenue_summary_project ON revenue_summary(project);
CREATE INDEX IF NOT EXISTS idx_revenue_summary_chain ON revenue_summary(chain);

-- ─────────────────────────────────────────────────────────────────────────
-- 4. RECONCILIATION LOG (Track on-chain vs .db mismatches)
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reconciliation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    project TEXT NOT NULL,
    chain TEXT NOT NULL,
    contract_address TEXT NOT NULL,
    token TEXT NOT NULL,
    on_chain_balance REAL NOT NULL,            -- From etherscan/contract call
    db_recorded_amount REAL NOT NULL,          -- Sum from withdrawals table
    discrepancy REAL NOT NULL,                 -- on_chain_balance - db_recorded_amount
    reconciliation_status TEXT,                -- 'balanced', 'discrepancy', 'needs_review'
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_reconciliation_chain ON reconciliation_log(chain);

-- ─────────────────────────────────────────────────────────────────────────
-- 5. CHAIN HEALTH (RPC health-check results — written by chain_monitor_fixed.py)
-- ─────────────────────────────────────────────────────────────────────────
-- NOTE: chain_monitor_fixed.py also creates this table defensively with
-- CREATE TABLE IF NOT EXISTS. The definition here is the canonical superset so
-- that deploy_termux.sh initializes it up front and the two never drift.
CREATE TABLE IF NOT EXISTS chain_health (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chain TEXT NOT NULL,                       -- 'ethereum', 'arbitrum', ...
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL,                       -- 'healthy', 'degraded', 'unreachable', 'unknown'
    block_number INTEGER,
    block_age_seconds REAL,
    gas_price REAL,                            -- Gwei
    peer_count INTEGER,
    latency_ms REAL,
    error_msg TEXT,
    rpc_used TEXT DEFAULT 'primary'           -- 'primary' or 'backup'
);

CREATE INDEX IF NOT EXISTS idx_chain_health_chain ON chain_health(chain);
CREATE INDEX IF NOT EXISTS idx_chain_health_timestamp ON chain_health(timestamp);

-- ─────────────────────────────────────────────────────────────────────────
-- 6. RPC DIAGNOSTICS (Every RPC failure logged — no more silent "NO DATA")
-- ─────────────────────────────────────────────────────────────────────────
-- Also created defensively by chain_monitor_fixed.py; kept in sync here.
CREATE TABLE IF NOT EXISTS rpc_diagnostics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chain TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    rpc_url TEXT,
    http_status INTEGER,
    response_time_ms REAL,
    error_type TEXT,                           -- 'timeout', 'connection_error', 'json_decode_error', ...
    error_msg TEXT,
    test_method TEXT                           -- JSON-RPC method that was attempted
);

CREATE INDEX IF NOT EXISTS idx_rpc_diagnostics_chain ON rpc_diagnostics(chain);
CREATE INDEX IF NOT EXISTS idx_rpc_diagnostics_timestamp ON rpc_diagnostics(timestamp);

-- ─────────────────────────────────────────────────────────────────────────
-- 7. TRIGGERS: Auto-update revenue_summary on flash_trades INSERT
-- ─────────────────────────────────────────────────────────────────────────

-- Insert trigger: Create summary row if doesn't exist
CREATE TRIGGER IF NOT EXISTS trg_flash_trade_insert
AFTER INSERT ON flash_trades
BEGIN
    INSERT OR IGNORE INTO revenue_summary (project, chain)
    VALUES (NEW.project, NEW.chain);
    
    UPDATE revenue_summary
    SET
        total_trades = (SELECT COUNT(*) FROM flash_trades WHERE project=NEW.project AND chain=NEW.chain),
        total_borrowed = (SELECT COALESCE(SUM(amount_borrowed), 0) FROM flash_trades WHERE project=NEW.project AND chain=NEW.chain AND status='success'),
        total_fees_paid = (SELECT COALESCE(SUM(fee_paid), 0) FROM flash_trades WHERE project=NEW.project AND chain=NEW.chain AND status='success'),
        total_gross_profit = (SELECT COALESCE(SUM(gross_profit), 0) FROM flash_trades WHERE project=NEW.project AND chain=NEW.chain AND status='success'),
        total_gas_spent = (SELECT COALESCE(SUM(gas_cost), 0) FROM flash_trades WHERE project=NEW.project AND chain=NEW.chain AND status='success'),
        total_net_profit = (SELECT COALESCE(SUM(net_profit), 0) FROM flash_trades WHERE project=NEW.project AND chain=NEW.chain AND status='success'),
        successful_trades = (SELECT COUNT(*) FROM flash_trades WHERE project=NEW.project AND chain=NEW.chain AND status='success'),
        failed_trades = (SELECT COUNT(*) FROM flash_trades WHERE project=NEW.project AND chain=NEW.chain AND status IN ('failed', 'reverted')),
        last_trade_timestamp = NEW.timestamp,
        last_updated = CURRENT_TIMESTAMP
    WHERE project=NEW.project AND chain=NEW.chain;
END;

-- Update trigger: Recalc on status change
CREATE TRIGGER IF NOT EXISTS trg_flash_trade_update
AFTER UPDATE ON flash_trades
BEGIN
    UPDATE revenue_summary
    SET
        total_trades = (SELECT COUNT(*) FROM flash_trades WHERE project=NEW.project AND chain=NEW.chain),
        total_gross_profit = (SELECT COALESCE(SUM(gross_profit), 0) FROM flash_trades WHERE project=NEW.project AND chain=NEW.chain AND status='success'),
        total_gas_spent = (SELECT COALESCE(SUM(gas_cost), 0) FROM flash_trades WHERE project=NEW.project AND chain=NEW.chain AND status='success'),
        total_net_profit = (SELECT COALESCE(SUM(net_profit), 0) FROM flash_trades WHERE project=NEW.project AND chain=NEW.chain AND status='success'),
        successful_trades = (SELECT COUNT(*) FROM flash_trades WHERE project=NEW.project AND chain=NEW.chain AND status='success'),
        failed_trades = (SELECT COUNT(*) FROM flash_trades WHERE project=NEW.project AND chain=NEW.chain AND status IN ('failed', 'reverted')),
        last_updated = CURRENT_TIMESTAMP
    WHERE project=NEW.project AND chain=NEW.chain;
END;

-- Withdrawal insert trigger
CREATE TRIGGER IF NOT EXISTS trg_withdrawal_insert
AFTER INSERT ON withdrawals
BEGIN
    UPDATE revenue_summary
    SET
        total_withdrawn = (SELECT COALESCE(SUM(amount), 0) FROM withdrawals WHERE project=NEW.project AND chain=NEW.chain AND status='success'),
        last_updated = CURRENT_TIMESTAMP
    WHERE project=NEW.project AND chain=NEW.chain;
END;

-- ─────────────────────────────────────────────────────────────────────────
-- 8. HELPER VIEWS (For easy querying)
-- ─────────────────────────────────────────────────────────────────────────

CREATE VIEW IF NOT EXISTS vw_profit_by_chain AS
SELECT
    project,
    chain,
    total_trades,
    total_gross_profit,
    total_gas_spent,
    total_net_profit,
    ROUND(total_net_profit, 2) AS net_profit_usd,
    CASE WHEN total_trades > 0 THEN ROUND(total_net_profit / total_trades, 2) ELSE 0 END AS avg_profit_per_trade,
    successful_trades,
    failed_trades,
    ROUND(100.0 * successful_trades / NULLIF(total_trades, 0), 1) AS success_rate_pct
FROM revenue_summary
ORDER BY total_net_profit DESC;

CREATE VIEW IF NOT EXISTS vw_all_projects_total AS
SELECT
    project,
    SUM(total_trades) AS total_trades,
    SUM(total_borrowed) AS total_borrowed,
    SUM(total_fees_paid) AS total_fees_paid,
    SUM(total_gross_profit) AS total_gross_profit,
    SUM(total_gas_spent) AS total_gas_spent,
    SUM(total_net_profit) AS total_net_profit,
    SUM(successful_trades) AS successful_trades,
    SUM(failed_trades) AS failed_trades,
    SUM(total_withdrawn) AS total_withdrawn,
    MAX(last_updated) AS last_updated
FROM revenue_summary
GROUP BY project;

CREATE VIEW IF NOT EXISTS vw_pending_withdrawals AS
SELECT
    w.id,
    w.project,
    w.chain,
    w.token,
    w.amount,
    w.tx_hash,
    w.status,
    w.timestamp
FROM withdrawals w
WHERE w.status != 'success'
ORDER BY w.timestamp DESC;
