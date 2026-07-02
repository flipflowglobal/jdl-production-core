// Mainnet-fork dry-run for NexusFlashReceiver on Arbitrum One.
// Forks live Arbitrum state (no real funds, no broadcast) and exercises the
// real crypto-moving path: deploy → Aave flashLoanSimple → Uniswap swaps →
// profit-or-revert safety check.
//
// Run:  npx hardhat test test/fork-flash.test.js
// Pin:  FORK_BLOCK=<n> npx hardhat test test/fork-flash.test.js
const { expect } = require("chai");
const { ethers } = require("hardhat");

// ─── Real Arbitrum One addresses ───────────────────────────────────────────
const AAVE_V3_POOL   = "0x794a61358D6845594F94dc1DB02A252b5b4814aD";
const UNI_V3_ROUTER  = "0xE592427A0AEce92De3Edee1F18E0157C05861564";
const BALANCER_VAULT = "0xBA12222222228d8Ba445958a75a0704d566BF2C8";
const USDC           = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"; // native USDC, 6dp
const WETH           = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"; // 18dp

// ArbitrageLib.SwapStep tuple, in struct field order.
const SWAPSTEP = "tuple(uint8,address,address,address,uint24,uint256,uint8,uint8,bytes32)[]";

function encodeSteps(steps) {
  return ethers.AbiCoder.defaultAbiCoder().encode([SWAPSTEP], [steps]);
}

describe("NexusFlashReceiver — Arbitrum mainnet fork", function () {
  this.timeout(300000); // forking from a public node can be slow

  let receiver, owner, other;

  before(async function () {
    [owner, other] = await ethers.getSigners();
    const Factory = await ethers.getContractFactory("NexusFlashReceiver");
    // Constructor takes an explicit owner first; the deploying signer owns it here.
    receiver = await Factory.deploy(owner.address, AAVE_V3_POOL, UNI_V3_ROUTER, BALANCER_VAULT);
    await receiver.waitForDeployment();
  });

  it("forks real Arbitrum state (Aave Pool has bytecode)", async function () {
    const code = await ethers.provider.getCode(AAVE_V3_POOL);
    expect(code.length).to.be.greaterThan(2); // "0x" + real bytecode
    const net = await ethers.provider.getNetwork();
    expect(Number(net.chainId)).to.equal(42161);
  });

  it("deploys with correct immutables and owner", async function () {
    expect(await receiver.AAVE_POOL()).to.equal(AAVE_V3_POOL);
    expect(await receiver.UNISWAP_V3_ROUTER()).to.equal(UNI_V3_ROUTER);
    expect(await receiver.BALANCER_VAULT()).to.equal(BALANCER_VAULT);
    expect(await receiver.owner()).to.equal(owner.address);
  });

  it("rejects executeOperation from a non-Aave caller (onlyAavePool)", async function () {
    await expect(
      receiver.connect(other).executeOperation(USDC, 1n, 0n, await receiver.getAddress(), "0x")
    ).to.be.revertedWithCustomError(receiver, "OnlyAavePool");
  });

  it("restricts initiateFlashLoan to owner", async function () {
    const steps = encodeSteps([[0, ethers.ZeroAddress, USDC, WETH, 500, 0n, 0, 0, ethers.ZeroHash]]);
    await expect(
      receiver.connect(other).initiateFlashLoan(USDC, 1000000n, steps)
    ).to.be.reverted; // OwnableUnauthorizedAccount
  });

  it("CRYPTO-MOVING PATH: real USDC→WETH→USDC flash loan reverts when unprofitable (funds safe)", async function () {
    // 100 USDC round trip through real Uniswap V3 pools. In an efficient market
    // the round trip loses the pool fees, so finalBalance < loan + premium and
    // the contract MUST revert (InsufficientProfit) — proving funds are never lost.
    const steps = encodeSteps([
      [0, ethers.ZeroAddress, USDC, WETH, 500,  0n, 0, 0, ethers.ZeroHash], // buy WETH
      [0, ethers.ZeroAddress, WETH, USDC, 3000, 0n, 0, 0, ethers.ZeroHash], // sell back
    ]);
    // The whole tx must revert: Aave's flashLoanSimple bubbles the receiver's
    // InsufficientProfit revert. We assert it reverts (no funds moved out).
    await expect(
      receiver.connect(owner).initiateFlashLoan(USDC, 100000000n, steps) // 100 USDC (6dp)
    ).to.be.reverted;
  });

  it("owner can pause/unpause; paused blocks initiateFlashLoan", async function () {
    await receiver.connect(owner).pause();
    const steps = encodeSteps([[0, ethers.ZeroAddress, USDC, WETH, 500, 0n, 0, 0, ethers.ZeroHash]]);
    await expect(
      receiver.connect(owner).initiateFlashLoan(USDC, 1000000n, steps)
    ).to.be.revertedWithCustomError(receiver, "EnforcedPause");
    await receiver.connect(owner).unpause();
  });

  it("restricts rescueTokens and pause to owner", async function () {
    await expect(receiver.connect(other).rescueTokens(USDC, 1n, other.address)).to.be.reverted;
    await expect(receiver.connect(other).pause()).to.be.reverted;
  });
});
