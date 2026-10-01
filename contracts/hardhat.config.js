// Plugins are required individually rather than via @nomicfoundation/hardhat-toolbox.
// The toolbox is a meta-package whose `latest` tag (v7) is a deprecation stub that
// works with neither Hardhat 2 nor 3 and exits non-zero on load; requiring only the
// three plugins this project actually uses removes that whole class of breakage and
// drops the unused typechain/ts-node/coverage/gas-reporter peer tree it pulls in.
require("@nomicfoundation/hardhat-ethers");        // hre.ethers (deploy scripts + tests)
require("@nomicfoundation/hardhat-chai-matchers"); // revertedWithCustomError in test/
require("@nomicfoundation/hardhat-network-helpers");

// The previous default was an all-zero key, which is NOT a valid secp256k1
// scalar: ethers rejects it with "Expected valid bigint: 0 < bigint < curve.n"
// the moment any network with a URL is constructed. That made every live
// network fail to initialize unless PRIVATE_KEY happened to be exported, so
// `npx hardhat run --network arbitrum <anything>` — including the connectivity
// probe and the deploy scripts — died before doing any work.
//
// An all-zero key is also the one value guaranteed never to control real funds,
// so it was never a useful fallback. Accounts are now populated only when a
// genuinely valid key is present; read-only work (fork tests, the connectivity
// probe, ABI inspection) needs no signer and is the common case.
const RAW_PRIVATE_KEY = (process.env.PRIVATE_KEY || "").trim();
const SECP256K1_N = BigInt(
  "0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141"
);
function isUsablePrivateKey(hex) {
  if (!/^0x?[0-9a-fA-F]{64}$/.test(hex)) return false;
  const scalar = BigInt(hex.startsWith("0x") ? hex : "0x" + hex);
  return scalar > 0n && scalar < SECP256K1_N;
}
const PRIVATE_KEY = isUsablePrivateKey(RAW_PRIVATE_KEY) ? RAW_PRIVATE_KEY : "";
if (RAW_PRIVATE_KEY && !PRIVATE_KEY) {
  console.warn(
    "[hardhat.config] PRIVATE_KEY is set but is not a valid secp256k1 scalar; " +
      "continuing with no signer. Read-only tasks are unaffected; deploying will fail."
  );
}
const SIGNER_ACCOUNTS = PRIVATE_KEY ? [PRIVATE_KEY] : [];

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
      accounts: SIGNER_ACCOUNTS,
    },
    arbitrum: {
      url: ARB_RPC_URL,
      accounts: SIGNER_ACCOUNTS,
    },
    polygon: {
      url: process.env.POLYGON_RPC_URL || "",
      accounts: SIGNER_ACCOUNTS,
    },
    bsc: {
      url: process.env.BSC_RPC_URL || "",
      accounts: SIGNER_ACCOUNTS,
    },
    optimism: {
      url: process.env.OPTIMISM_RPC_URL || "",
      accounts: SIGNER_ACCOUNTS,
    },
    avalanche: {
      url: process.env.AVALANCHE_RPC_URL || "",
      accounts: SIGNER_ACCOUNTS,
    },
  },
};
