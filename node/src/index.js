// index.js — JDL Node.js API + orchestration server.
//
// Ties the three languages together:
//   • Rust  (jdl-hotpath)          — fast arbitrage-cycle detection
//   • Solidity (NexusFlashReceiver) — on-chain execution, read via ethers.js
//   • Node  (this)                 — HTTP API + orchestration
//
// It does NOT hold private keys or broadcast by default: reads are open, and the
// /scan endpoint is pure compute. Execution stays with the Python engine / Gelato
// relay path unless you explicitly wire a signer (see EXECUTION below).
import express from 'express';
import { scan, hotpathAvailable, HOTPATH_BIN } from './hotpath.js';
import { makeProvider, chainHealth, contractState } from './chain.js';

const PORT = Number(process.env.PORT || 8787);
const RPC_URL = process.env.RPC_URL || process.env.ARB_RPC_URL || 'https://arb1.arbitrum.io/rpc';
const CONTRACT = process.env.FLASH_CONTRACT_ADDRESS || '';
const CHAIN_ID = Number(process.env.CHAIN_ID || 42161);

const app = express();
app.use(express.json({ limit: '1mb' }));

let provider;
try {
  provider = makeProvider(RPC_URL);
} catch (e) {
  console.error('provider init failed:', e.message);
}

// ── Health ────────────────────────────────────────────────────────────────
app.get('/health', async (_req, res) => {
  const out = { ok: true, hotpath: hotpathAvailable(), hotpathBin: HOTPATH_BIN, rpc: RPC_URL };
  try {
    out.chain = await chainHealth(provider, CHAIN_ID);
  } catch (e) {
    out.ok = false;
    out.chainError = e.message;
  }
  res.status(out.ok ? 200 : 503).json(out);
});

// ── Contract state (read-only) ───────────────────────────────────────────
app.get('/contract', async (_req, res) => {
  try {
    res.json(await contractState(provider, CONTRACT));
  } catch (e) {
    res.status(502).json({ error: e.message });
  }
});

// ── Scan: run the Rust hot-path over supplied quotes ──────────────────────
// POST body = ScanRequest {edges, base, loan_usd, gas_usd, min_profit_usd?}
const isNonNegFinite = (v) => Number.isFinite(v) && v >= 0;

app.post('/scan', async (req, res) => {
  const body = req.body || {};
  if (!Array.isArray(body.edges) || body.edges.length === 0 || !body.base) {
    return res.status(400).json({ error: 'body must include {edges:[non-empty], base, loan_usd, gas_usd}' });
  }
  // Numbers must be finite & non-negative; NaN would serialize to null and
  // make the Rust deserialize fail with an opaque 500 instead of a clean 400.
  const loan_usd = Number(body.loan_usd || 0);
  const gas_usd = Number(body.gas_usd || 0);
  const min_profit_usd = Number(body.min_profit_usd || 0);
  if (!isNonNegFinite(loan_usd) || !isNonNegFinite(gas_usd) || !isNonNegFinite(min_profit_usd)) {
    return res.status(400).json({ error: 'loan_usd, gas_usd and min_profit_usd must be finite, non-negative numbers' });
  }
  // Each edge needs finite rate/fee_bps or the hot-path can't score it.
  for (const e of body.edges) {
    if (!e || typeof e !== 'object' || !Number.isFinite(Number(e.rate)) || !Number.isFinite(Number(e.fee_bps))) {
      return res.status(400).json({ error: 'each edge must have finite numeric rate and fee_bps' });
    }
  }
  try {
    const result = await scan({
      edges: body.edges,
      base: body.base,
      loan_usd,
      gas_usd,
      min_profit_usd,
    });
    res.json(result);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// ── EXECUTION (opt-in) ────────────────────────────────────────────────────
// Broadcasting is intentionally NOT wired here. To execute, either:
//   • let the Python engine / Gelato relay path own execution (recommended), or
//   • add a signer + build calldata and call initiateFlashLoan / *Relay via ethers.
// Keeping the server read+compute-only means it can run without holding a key.

// Only listen when run directly (not when imported by tests).
const isMain = process.argv[1] && import.meta.url.endsWith(process.argv[1].split('/').pop());
if (isMain) {
  app.listen(PORT, () => {
    console.log(`JDL server on :${PORT}  rpc=${RPC_URL}  hotpath=${hotpathAvailable()}  contract=${CONTRACT || '(none)'}`);
  });
}

export { app };
