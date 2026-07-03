// Node test for the Rust hot-path bridge. Run: npm test  (node --test)
// Requires the release binary: cd rust/hotpath && cargo build --release
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { scan, hotpathAvailable } from '../src/hotpath.js';

test('hot-path finds the profitable USDC->WETH->USDC loop', async (t) => {
  if (!hotpathAvailable()) {
    t.skip('jdl-hotpath binary not built (cd rust/hotpath && cargo build --release)');
    return;
  }
  const result = await scan({
    edges: [
      { from: 'USDC', to: 'WETH', rate: 0.0005, fee_bps: 5 },
      { from: 'WETH', to: 'USDC', rate: 2020, fee_bps: 5 },
    ],
    base: 'USDC',
    loan_usd: 100000,
    gas_usd: 1,
  });
  assert.ok(result.opportunity, 'expected an opportunity');
  assert.deepEqual(result.opportunity.path, ['USDC', 'WETH', 'USDC']);
  assert.ok(result.opportunity.net_profit_usd > 0);
});

test('hot-path returns null on a non-profitable set', async (t) => {
  if (!hotpathAvailable()) {
    t.skip('binary not built');
    return;
  }
  const result = await scan({
    edges: [
      { from: 'USDC', to: 'WETH', rate: 0.0005, fee_bps: 5 },
      { from: 'WETH', to: 'USDC', rate: 2000.2, fee_bps: 5 },
    ],
    base: 'USDC',
    loan_usd: 100000,
    gas_usd: 50,
  });
  assert.equal(result.opportunity, null);
});
