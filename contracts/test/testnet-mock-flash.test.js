// Local, no-fork sanity check for the Sepolia-testnet mock stack
// (MockAavePool + MockV3Router) before spending real effort deploying and
// seeding it live on Arbitrum Sepolia. Proves the receiver's real
// borrow -> swap -> swap -> repay path can actually SUCCEED (not just safely
// revert, which is what the mainnet-fork test proves) when a genuine price
// gap exists between the two swap legs — exactly the shape of transaction
// the Sepolia deployment is meant to let you broadcast for real.
//
// Run: npx hardhat test test/testnet-mock-flash.test.js
const { expect } = require("chai");
const { ethers } = require("hardhat");

const SWAPSTEP = "tuple(uint8,address,address,address,uint24,uint256,uint8,uint8,bytes32)[]";
function encodeSteps(steps) {
  return ethers.AbiCoder.defaultAbiCoder().encode([SWAPSTEP], [steps]);
}

describe("NexusFlashReceiver — Sepolia mock-stack dry run (no fork, no real funds)", function () {
  let owner, receiver, router, pool, usdc, weth;

  const USDC_1 = 1_000_000n; // 1 USDC, 6dp
  const WETH_1 = 10n ** 18n; // 1 WETH, 18dp

  before(async function () {
    [owner] = await ethers.getSigners();

    const ERC20 = await ethers.getContractFactory("MockERC20");
    usdc = await ERC20.deploy("Mock USDC", "mUSDC", 6);
    weth = await ERC20.deploy("Mock WETH", "mWETH", 18);
    await usdc.waitForDeployment();
    await weth.waitForDeployment();

    const Router = await ethers.getContractFactory("MockV3Router");
    router = await Router.deploy();
    await router.waitForDeployment();

    const Pool = await ethers.getContractFactory("MockAavePool");
    pool = await Pool.deploy();
    await pool.waitForDeployment();

    const Receiver = await ethers.getContractFactory("NexusFlashReceiver");
    receiver = await Receiver.deploy(
      owner.address,
      await pool.getAddress(),
      await router.getAddress(),
      owner.address // Balancer vault unused on this path — any non-zero address satisfies the constructor.
    );
    await receiver.waitForDeployment();

    // Mint enough USDC up front to cover pool funding + both router seeds
    // (500,000 + 3,000,000 + 3,300,000), plus headroom.
    await usdc.mint(owner.address, 10_000_000n * USDC_1);
    await weth.mint(owner.address, 2000n * WETH_1);

    // Fund the mock Aave pool with 500,000 USDC of lendable liquidity.
    await usdc.connect(owner).approve(await pool.getAddress(), ethers.MaxUint256);
    await pool.connect(owner).fund(await usdc.getAddress(), 500_000n * USDC_1);

    // Seed a deliberate price gap across two fee tiers, same shape as real
    // Uniswap fee tiers having different prices:
    //   fee=500  pool: ~3,000 USDC per WETH (cheap  — where we BUY)
    //   fee=3000 pool: ~3,300 USDC per WETH (rich   — where we SELL)
    await usdc.connect(owner).approve(await router.getAddress(), ethers.MaxUint256);
    await weth.connect(owner).approve(await router.getAddress(), ethers.MaxUint256);

    await router
      .connect(owner)
      .seedLiquidity(await usdc.getAddress(), 3_000_000n * USDC_1, await weth.getAddress(), 1000n * WETH_1, 500);
    await router
      .connect(owner)
      .seedLiquidity(await weth.getAddress(), 1000n * WETH_1, await usdc.getAddress(), 3_300_000n * USDC_1, 3000);
  });

  it("CRYPTO-MOVING PATH: a real, profitable flash loan actually completes and pays the owner", async function () {
    const loan = 100_000n * USDC_1; // 100,000 USDC
    const steps = encodeSteps([
      [0, ethers.ZeroAddress, await usdc.getAddress(), await weth.getAddress(), 500, 0n, 0, 0, ethers.ZeroHash], // buy WETH cheap
      [0, ethers.ZeroAddress, await weth.getAddress(), await usdc.getAddress(), 3000, 0n, 0, 0, ethers.ZeroHash], // sell WETH rich
    ]);

    const ownerBalBefore = await usdc.balanceOf(owner.address);

    const tx = await receiver.connect(owner).initiateFlashLoan(await usdc.getAddress(), loan, steps);
    const receipt = await tx.wait();
    expect(receipt.status).to.equal(1);

    const ownerBalAfter = await usdc.balanceOf(owner.address);
    const profit = ownerBalAfter - ownerBalBefore;

    // ~2,740 USDC expected (2,790 gross spread minus Aave's 5bps premium on the
    // 100,000 USDC loan = 50 USDC); assert strictly positive and in a sane band
    // rather than pinning the exact figure to AMM-math rounding.
    expect(profit).to.be.greaterThan(2_000n * USDC_1);
    expect(profit).to.be.lessThan(3_500n * USDC_1);

    const event = receipt.logs
      .map((l) => {
        try {
          return receiver.interface.parseLog(l);
        } catch {
          return null;
        }
      })
      .find((e) => e && e.name === "ArbitrageExecuted");
    expect(event).to.not.equal(undefined);
    expect(event.args.profit).to.be.greaterThan(0n);
  });
});
