// chain.js — ethers.js v6 reads/writes against the deployed NexusFlashReceiver.
//
// Read-only by default (owner/pause/immutables + chain health). Execution goes
// through the same contract the Python engine and the Gelato relay path use — the
// Node server never re-implements the on-chain logic, it just drives it.
import { ethers } from 'ethers';

// Minimal ABI — only what the server reads/builds. Full ABI lives in the contract.
export const NEXUS_ABI = [
  'function owner() view returns (address)',
  'function paused() view returns (bool)',
  'function AAVE_POOL() view returns (address)',
  'function UNISWAP_V3_ROUTER() view returns (address)',
  'function BALANCER_VAULT() view returns (address)',
  'function initiateFlashLoan(address asset, uint256 amount, bytes encodedSteps)',
  'function initiateFlashLoanRelay(address asset, uint256 amount, bytes encodedSteps, uint256 maxFee)',
];

export function makeProvider(rpcUrl) {
  if (!rpcUrl) throw new Error('RPC_URL is required');
  return new ethers.JsonRpcProvider(rpcUrl);
}

export async function chainHealth(provider, expectedChainId = 42161) {
  const net = await provider.getNetwork();
  const block = await provider.getBlockNumber();
  const chainId = Number(net.chainId);
  return { chainId, block, isArbitrum: chainId === expectedChainId };
}

export async function contractState(provider, address) {
  if (!address) return { configured: false };
  const c = new ethers.Contract(address, NEXUS_ABI, provider);
  const [owner, paused, aave, router, vault, code] = await Promise.all([
    c.owner(),
    c.paused(),
    c.AAVE_POOL(),
    c.UNISWAP_V3_ROUTER(),
    c.BALANCER_VAULT(),
    provider.getCode(address),
  ]);
  const bytecode = code.toLowerCase();
  return {
    configured: true,
    address,
    owner,
    paused,
    aavePool: aave,
    uniswapV3Router: router,
    balancerVault: vault,
    // selector presence: initiateFlashLoan (e95437aa) + relay (d55c394c)
    supportsRelay: bytecode.includes('d55c394c'),
    supportsRescueEth: bytecode.includes('a0558c3f'),
  };
}
