// Validation / robustness tests for the /scan endpoint and scan() bridge.
// Hermetic: no real network, no signer; the Rust binary may or may not exist.
// Run: npm test  (node --test)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { app } from '../src/index.js';
import { scan } from '../src/hotpath.js';

// Boot the express app on an ephemeral port, run fn, then close.
async function withServer(fn) {
  const server = app.listen(0);
  await new Promise((r) => server.once('listening', r));
  const { port } = server.address();
  try {
    return await fn(`http://127.0.0.1:${port}`);
  } finally {
    await new Promise((r) => server.close(r));
  }
}

const validEdges = [
  { from: 'USDC', to: 'WETH', rate: 0.0005, fee_bps: 5 },
  { from: 'WETH', to: 'USDC', rate: 2020, fee_bps: 5 },
];

test('POST /scan rejects NaN loan_usd with 400 (not 500)', async () => {
  await withServer(async (base) => {
    const res = await fetch(`${base}/scan`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ edges: validEdges, base: 'USDC', loan_usd: 'not-a-number', gas_usd: 1 }),
    });
    assert.equal(res.status, 400);
    const j = await res.json();
    assert.match(j.error, /finite, non-negative/);
  });
});

test('POST /scan rejects negative gas_usd with 400', async () => {
  await withServer(async (base) => {
    const res = await fetch(`${base}/scan`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ edges: validEdges, base: 'USDC', loan_usd: 100, gas_usd: -5 }),
    });
    assert.equal(res.status, 400);
  });
});

test('POST /scan rejects empty edges array with 400', async () => {
  await withServer(async (base) => {
    const res = await fetch(`${base}/scan`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ edges: [], base: 'USDC', loan_usd: 100, gas_usd: 1 }),
    });
    assert.equal(res.status, 400);
  });
});

test('POST /scan rejects edge with non-finite rate with 400', async () => {
  await withServer(async (base) => {
    const res = await fetch(`${base}/scan`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        edges: [{ from: 'USDC', to: 'WETH', rate: 'oops', fee_bps: 5 }],
        base: 'USDC', loan_usd: 100, gas_usd: 1,
      }),
    });
    assert.equal(res.status, 400);
  });
});

test('scan() rejects cleanly when the binary path exits immediately (EPIPE-safe)', async () => {
  // Point the bridge at a binary that exits without reading stdin. The write
  // triggers EPIPE on child.stdin; scan() must reject, not crash the process.
  const prev = process.env.HOTPATH_BIN;
  process.env.HOTPATH_BIN = '/bin/true'; // exists, reads nothing, exits 0
  try {
    // Re-import with the new env by using a fresh module query is overkill;
    // HOTPATH_BIN is read at import time, so import a fresh copy.
    const mod = await import(`../src/hotpath.js?epipe=${Date.now()}`);
    await assert.rejects(
      mod.scan({ edges: validEdges, base: 'USDC', loan_usd: 100, gas_usd: 1 }),
      (err) => err instanceof Error,
    );
  } finally {
    if (prev === undefined) delete process.env.HOTPATH_BIN;
    else process.env.HOTPATH_BIN = prev;
  }
});
