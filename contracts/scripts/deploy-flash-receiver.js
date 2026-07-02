const hre = require("hardhat");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  console.log("Deploying NexusFlashReceiver with account:", deployer.address);

  const AAVE_POOL       = process.env.AAVE_POOL       || "";
  const UNISWAP_V3_ROUTER = process.env.UNISWAP_V3_ROUTER || "";
  const BALANCER_VAULT   = process.env.BALANCER_VAULT   || "";

  if (!AAVE_POOL || !UNISWAP_V3_ROUTER || !BALANCER_VAULT) {
    console.error("Missing env: AAVE_POOL, UNISWAP_V3_ROUTER, BALANCER_VAULT");
    process.exit(1);
  }

  // Constructor takes an explicit owner first; the deploying signer owns it here
  // (override with RECEIVER_OWNER to assign a different owner).
  const OWNER = process.env.RECEIVER_OWNER || deployer.address;

  const NexusFlashReceiver = await hre.ethers.getContractFactory("NexusFlashReceiver");
  const contract = await NexusFlashReceiver.deploy(OWNER, AAVE_POOL, UNISWAP_V3_ROUTER, BALANCER_VAULT);

  await contract.waitForDeployment();
  console.log("NexusFlashReceiver deployed to:", await contract.getAddress());
}

main().catch((err) => { console.error(err); process.exit(1); });
