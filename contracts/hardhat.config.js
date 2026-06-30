require("@nomicfoundation/hardhat-toolbox");

const PRIVATE_KEY = process.env.PRIVATE_KEY || "0000000000000000000000000000000000000000000000000000000000000000";
const ARB_RPC_URL = process.env.ARB_RPC_URL || "https://arb1.arbitrum.io/rpc";

// Optional pinned fork block for reproducible mainnet-fork tests.
// Override with FORK_BLOCK=<n>; falls back to latest when unset.
const FORK_BLOCK = process.env.FORK_BLOCK ? parseInt(process.env.FORK_BLOCK, 10) : undefined;

module.exports = {
  solidity: {
    version: "0.8.20",
    settings: {
      optimizer: { enabled: true, runs: 200 },
      viaIR: true,
    },
  },
  networks: {
    // Forked Arbitrum One for crypto-moving dry-run tests (no real funds).
    hardhat: {
      chainId: 42161,
      forking: {
        url: ARB_RPC_URL,
        ...(FORK_BLOCK ? { blockNumber: FORK_BLOCK } : {}),
      },
    },
    ethereum: {
      url: process.env.ETH_RPC_URL || "",
      accounts: [PRIVATE_KEY],
    },
    arbitrum: {
      url: ARB_RPC_URL,
      accounts: [PRIVATE_KEY],
    },
    polygon: {
      url: process.env.POLYGON_RPC_URL || "",
      accounts: [PRIVATE_KEY],
    },
    bsc: {
      url: process.env.BSC_RPC_URL || "",
      accounts: [PRIVATE_KEY],
    },
    optimism: {
      url: process.env.OPTIMISM_RPC_URL || "",
      accounts: [PRIVATE_KEY],
    },
    avalanche: {
      url: process.env.AVALANCHE_RPC_URL || "",
      accounts: [PRIVATE_KEY],
    },
  },
};
