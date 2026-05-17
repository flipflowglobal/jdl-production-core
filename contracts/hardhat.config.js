require("@nomicfoundation/hardhat-toolbox");

const PRIVATE_KEY = process.env.PRIVATE_KEY || "0000000000000000000000000000000000000000000000000000000000000000";

module.exports = {
  solidity: {
    version: "0.8.20",
    settings: {
      optimizer: { enabled: true, runs: 200 },
      viaIR: true,
    },
  },
  networks: {
    ethereum: {
      url: process.env.ETH_RPC_URL || "",
      accounts: [PRIVATE_KEY],
    },
    arbitrum: {
      url: process.env.ARB_RPC_URL || "",
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
