const hre = require("hardhat");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  console.log("Deploying ArbitrageLib with account:", deployer.address);

  // Libraries are linked — deploy and link if used by a referencing contract.
  const ArbitrageLib = await hre.ethers.getContractFactory("ArbitrageLib");
  const lib = await ArbitrageLib.deploy();

  await lib.waitForDeployment();
  console.log("ArbitrageLib deployed to:", await lib.getAddress());
}

main().catch((err) => { console.error(err); process.exit(1); });
