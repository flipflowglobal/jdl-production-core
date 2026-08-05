// Plugins are required individually rather than via @nomicfoundation/hardhat-toolbox.
// The toolbox is a meta-package whose `latest` tag (v7) is a deprecation stub that
// works with neither Hardhat 2 nor 3 and exits non-zero on load; requiring only the
// three plugins this project actually uses removes that whole class of breakage and
// drops the unused typechain/ts-node/coverage/gas-reporter peer tree it pulls in.
require("@nomicfoundation/hardhat-ethers");        // hre.ethers (deploy scripts + tests)
require("@nomicfoundation/hardhat-chai-matchers"); // revertedWithCustomError in test/
require("@nomicfoundation/hardhat-network-helpers");

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
