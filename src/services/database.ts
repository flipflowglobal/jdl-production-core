import { Pool } from "pg";
import { encryptPrivateKey, decryptPrivateKey } from "./encryption.js";

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: process.env.NODE_ENV === "production" ? { rejectUnauthorized: false } : undefined,
  max: 10,
  idleTimeoutMillis: 30000,
  connectionTimeoutMillis: 8000,
});

pool.on("error", (err) => {
  console.error("[DB] Unexpected pool error:", err.message);
});

export async function query(sql: string, params?: any[]) {
  let retries = 2;
  while (retries >= 0) {
    try {
      const client = await pool.connect();
      try {
        const result = await client.query(sql, params);
        return result;
      } finally {
        client.release();
      }
    } catch (err: any) {
      if (retries > 0 && (err.code === "ECONNRESET" || err.code === "57P01" || err.code === "08006")) {
        retries--;
        await new Promise((r) => setTimeout(r, 500));
        continue;
      }
      throw err;
    }
  }
  throw new Error("DB query failed after retries");
}

// ─── Users ──────────────────────────────────────────────────────────────────

export async function upsertUser(clerkUserId: string, email: string, name?: string) {
  const result = await query(
    `INSERT INTO users (id, clerk_user_id, email, name)
     VALUES ($1, $1, $2, $3)
     ON CONFLICT (clerk_user_id) DO UPDATE SET email = $2, name = COALESCE($3, users.name), updated_at = NOW()
     RETURNING *`,
    [clerkUserId, email, name || null]
  );
  return result.rows[0];
}

export async function getUserByClerkId(clerkUserId: string) {
  const result = await query("SELECT * FROM users WHERE clerk_user_id = $1", [clerkUserId]);
  return result.rows[0] || null;
}

export async function updateSubscription(clerkUserId: string, tier: string, expiresAt?: Date) {
  await query(
    "UPDATE users SET subscription_tier = $2, subscription_expires_at = $3, updated_at = NOW() WHERE clerk_user_id = $1",
    [clerkUserId, tier, expiresAt || null]
  );
}

// ─── Wallet Vault ────────────────────────────────────────────────────────────

export async function saveWallet(
  userId: string,
  address: string,
  privateKey: string,
  mnemonic: string | undefined,
  walletType: "personal" | "system" | "agent",
  label?: string,
  chain?: string
) {
  const encPk = encryptPrivateKey(privateKey);
  const encMnemonic = mnemonic ? encryptPrivateKey(mnemonic) : null;

  const result = await query(
    `INSERT INTO wallet_vault (user_id, wallet_type, label, address, encrypted_private_key, encrypted_mnemonic, iv, auth_tag, chain)
     VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
     ON CONFLICT (user_id, address) DO UPDATE SET label = COALESCE($3, wallet_vault.label), updated_at = NOW() WHERE false
     RETURNING id`,
    [
      userId,
      walletType,
      label || null,
      address,
      JSON.stringify(encPk),
      encMnemonic ? JSON.stringify(encMnemonic) : null,
      encPk.iv,
      encPk.authTag,
      chain || "multi-chain",
    ]
  );
  return result.rows[0];
}

export async function getUserWallets(userId: string) {
  const result = await query(
    "SELECT id, wallet_type, label, address, chain, created_at FROM wallet_vault WHERE user_id = $1 ORDER BY created_at DESC",
    [userId]
  );
  return result.rows;
}

export async function getWalletPrivateKey(userId: string, address: string): Promise<string | null> {
  const result = await query(
    "SELECT encrypted_private_key, iv, auth_tag FROM wallet_vault WHERE user_id = $1 AND address = $2",
    [userId, address]
  );
  if (!result.rows[0]) return null;
  const row = result.rows[0];
  const encData = JSON.parse(row.encrypted_private_key);
  return decryptPrivateKey(encData);
}

// ─── Agents ──────────────────────────────────────────────────────────────────

export async function saveAgent(userId: string, agent: any) {
  const encPk = encryptPrivateKey(agent.wallet.privateKey);
  const encMnemonic = encryptPrivateKey(agent.wallet.mnemonic);

  await query(
    `INSERT INTO agents (id, user_id, name, strategy_id, strategy, strategy_category, algorithm, status, capital, risk_profile, chains, parameters, wallet_address, performance, health)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
     ON CONFLICT (id) DO UPDATE SET status = $8, performance = $14, health = $15, updated_at = NOW()`,
    [
      agent.id, userId, agent.name, agent.strategyId, agent.strategy,
      agent.strategyCategory, agent.algorithm, agent.status, agent.capital,
      agent.riskProfile, agent.chains, JSON.stringify(agent.parameters),
      agent.wallet.address, JSON.stringify(agent.performance), JSON.stringify(agent.health),
    ]
  );

  await query(
    `INSERT INTO agent_wallets (agent_id, user_id, address, encrypted_private_key, encrypted_mnemonic, iv, auth_tag)
     VALUES ($1,$2,$3,$4,$5,$6,$7)
     ON CONFLICT (address) DO NOTHING`,
    [
      agent.id, userId, agent.wallet.address,
      JSON.stringify(encPk),
      JSON.stringify(encMnemonic),
      encPk.iv, encPk.authTag,
    ]
  );
}

export async function getUserAgents(userId: string) {
  const result = await query(
    "SELECT * FROM agents WHERE user_id = $1 ORDER BY created_at DESC",
    [userId]
  );
  return result.rows.map((r: any) => ({
    ...r,
    chains: r.chains || [],
    parameters: r.parameters || {},
    performance: r.performance || {},
    health: r.health || {},
  }));
}

export async function getAgentWalletKey(agentId: string, userId: string): Promise<string | null> {
  const result = await query(
    "SELECT encrypted_private_key FROM agent_wallets WHERE agent_id = $1 AND user_id = $2",
    [agentId, userId]
  );
  if (!result.rows[0]) return null;
  return decryptPrivateKey(JSON.parse(result.rows[0].encrypted_private_key));
}

// ─── Trades ──────────────────────────────────────────────────────────────────

export async function recordTrade(trade: {
  agentId: string;
  userId: string;
  txHash?: string;
  chain: string;
  fromToken: string;
  toToken: string;
  fromAmount: number;
  toAmount: number;
  pnl: number;
  feePaid: number;
  systemFee: number;
  status: string;
  algorithm: string;
  confidence: number;
}) {
  const result = await query(
    `INSERT INTO trades (agent_id, user_id, tx_hash, chain, from_token, to_token, from_amount, to_amount, pnl, fee_paid, system_fee, status, algorithm, confidence)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14) RETURNING id`,
    [
      trade.agentId, trade.userId, trade.txHash || null, trade.chain,
      trade.fromToken, trade.toToken, trade.fromAmount, trade.toAmount,
      trade.pnl, trade.feePaid, trade.systemFee, trade.status,
      trade.algorithm, trade.confidence,
    ]
  );
  return result.rows[0];
}

export async function getUserTrades(userId: string, limit = 50) {
  const result = await query(
    "SELECT * FROM trades WHERE user_id = $1 ORDER BY executed_at DESC LIMIT $2",
    [userId, limit]
  );
  return result.rows;
}

// ─── GoCardless Subscriptions ────────────────────────────────────────────────

export async function ensureGcSubscriptionColumns() {
  try {
    await query(`ALTER TABLE gc_subscriptions ADD COLUMN IF NOT EXISTS is_recurring BOOLEAN DEFAULT TRUE`);
    await query(`ALTER TABLE gc_subscriptions ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP`);
  } catch {
    // columns may already exist — safe to ignore
  }
}

export async function createGcSubscription(data: {
  clerkUserId: string;
  email: string;
  plan: string;
  gcBillingRequestId: string;
  amountPence: number;
  isRecurring?: boolean;
}) {
  const id = `gcsub_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  const isRecurring = data.isRecurring !== false;
  const result = await query(
    `INSERT INTO gc_subscriptions (id, clerk_user_id, email, plan, gc_billing_request_id, status, amount_pence, is_recurring)
     VALUES ($1,$2,$3,$4,$5,'pending',$6,$7) RETURNING *`,
    [id, data.clerkUserId, data.email, data.plan, data.gcBillingRequestId, data.amountPence, isRecurring]
  );
  return result.rows[0];
}

export async function updateGcSubByBillingRequest(billingRequestId: string, updates: {
  gcMandateId?: string;
  gcCustomerId?: string;
  status?: string;
}) {
  const { gcMandateId, gcCustomerId, status } = updates;
  await query(
    `UPDATE gc_subscriptions SET
       gc_mandate_id = COALESCE($2, gc_mandate_id),
       gc_customer_id = COALESCE($3, gc_customer_id),
       status = COALESCE($4, status),
       updated_at = NOW()
     WHERE gc_billing_request_id = $1`,
    [billingRequestId, gcMandateId || null, gcCustomerId || null, status || null]
  );
}

export async function updateGcSubByMandateId(mandateId: string, updates: {
  gcSubscriptionId?: string;
  status?: string;
  nextChargeDate?: string;
}) {
  await query(
    `UPDATE gc_subscriptions SET
       gc_subscription_id = COALESCE($2, gc_subscription_id),
       status = COALESCE($3, status),
       next_charge_date = COALESCE($4::date, next_charge_date),
       updated_at = NOW()
     WHERE gc_mandate_id = $1`,
    [mandateId, updates.gcSubscriptionId || null, updates.status || null, updates.nextChargeDate || null]
  );
}

export async function updateGcSubBySubscriptionId(subscriptionId: string, updates: {
  status?: string;
  nextChargeDate?: string;
}) {
  await query(
    `UPDATE gc_subscriptions SET
       status = COALESCE($2, status),
       next_charge_date = COALESCE($3::date, next_charge_date),
       updated_at = NOW()
     WHERE gc_subscription_id = $1`,
    [subscriptionId, updates.status || null, updates.nextChargeDate || null]
  );
}

export async function getGcSubscriptionByClerkId(clerkUserId: string) {
  const result = await query(
    `SELECT * FROM gc_subscriptions WHERE clerk_user_id = $1 ORDER BY created_at DESC LIMIT 1`,
    [clerkUserId]
  );
  return result.rows[0] || null;
}

export async function getGcSubByBillingRequest(billingRequestId: string) {
  const result = await query(
    `SELECT * FROM gc_subscriptions WHERE gc_billing_request_id = $1`,
    [billingRequestId]
  );
  return result.rows[0] || null;
}

export async function activateUserSubscription(clerkUserId: string, plan: string) {
  const expiresAt = new Date();
  expiresAt.setMonth(expiresAt.getMonth() + 1);
  await query(
    `UPDATE users SET subscription_tier = $2, subscription_expires_at = $3, updated_at = NOW()
     WHERE clerk_user_id = $1`,
    [clerkUserId, plan, expiresAt]
  );
}

// ─── User Preferences ────────────────────────────────────────────────────────

let _prefTableReady = false;

async function ensurePreferencesTable() {
  if (_prefTableReady) return;
  await query(`
    CREATE TABLE IF NOT EXISTS user_preferences (
      clerk_user_id TEXT PRIMARY KEY,
      preferences   JSONB NOT NULL DEFAULT '{}',
      updated_at    TIMESTAMP NOT NULL DEFAULT NOW()
    )
  `);
  _prefTableReady = true;
}

export interface UserPreferences {
  notifications:    boolean;
  autoTrade:        boolean;
  biometric:        boolean;
  darkMode:         boolean;
  soundEffects:     boolean;
  priceAlerts:      boolean;
  emailReports:     boolean;
  flashLoanAlerts:  boolean;
  agentHealth:      boolean;
  slippage:         number;
  gasLimit:         number;
  maxGasPrice:      number;
  defaultChain:     string;
  twoFaEnabled:     boolean;
  sessionTimeout:   number;
}

const DEFAULT_PREFERENCES: UserPreferences = {
  notifications:    true,
  autoTrade:        true,
  biometric:        false,
  darkMode:         true,
  soundEffects:     true,
  priceAlerts:      true,
  emailReports:     false,
  flashLoanAlerts:  true,
  agentHealth:      true,
  slippage:         0.5,
  gasLimit:         300000,
  maxGasPrice:      50,
  defaultChain:     "ethereum",
  twoFaEnabled:     false,
  sessionTimeout:   30,
};

export async function getUserPreferences(clerkUserId: string): Promise<UserPreferences> {
  await ensurePreferencesTable();
  const result = await query(
    `SELECT preferences FROM user_preferences WHERE clerk_user_id = $1`,
    [clerkUserId]
  );
  if (!result.rows[0]) return { ...DEFAULT_PREFERENCES };
  return { ...DEFAULT_PREFERENCES, ...result.rows[0].preferences };
}

export async function saveUserPreferences(clerkUserId: string, prefs: Partial<UserPreferences>): Promise<UserPreferences> {
  await ensurePreferencesTable();
  const current = await getUserPreferences(clerkUserId);
  const merged = { ...current, ...prefs };
  await query(
    `INSERT INTO user_preferences (clerk_user_id, preferences, updated_at)
     VALUES ($1, $2::jsonb, NOW())
     ON CONFLICT (clerk_user_id) DO UPDATE SET preferences = $2::jsonb, updated_at = NOW()`,
    [clerkUserId, JSON.stringify(merged)]
  );
  return merged;
}
