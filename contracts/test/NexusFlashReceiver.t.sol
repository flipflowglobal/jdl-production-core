// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// Foundry mainnet-fork dry-run for NexusFlashReceiver on Arbitrum One.
// Mirrors test/fork-flash.test.js (Hardhat) so the contracts can be verified
// with either toolchain — Foundry is the recommended path on Termux where
// Hardhat/Node may be awkward.
//
// Run:  ARB_RPC_URL=https://arb1.arbitrum.io/rpc forge test --match-path test/NexusFlashReceiver.t.sol -vv
import {Test} from "forge-std/Test.sol";
import {NexusFlashReceiver} from "../contracts/NexusFlashReceiver.sol";
import {ArbitrageLib} from "../contracts/ArbitrageLib.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

contract NexusFlashReceiverForkTest is Test {
    // ─── Real Arbitrum One addresses ───────────────────────────────────────
    address constant AAVE_V3_POOL   = 0x794a61358D6845594F94dc1DB02A252b5b4814aD;
    address constant UNI_V3_ROUTER  = 0xE592427A0AEce92De3Edee1F18E0157C05861564;
    address constant BALANCER_VAULT = 0xBA12222222228d8Ba445958a75a0704d566BF2C8;
    address constant USDC           = 0xaf88d065e77c8cC2239327C5EDb3A432268e5831; // 6dp
    address constant WETH           = 0x82aF49447D8a07e3bd95BD0d56f35241523fBab1; // 18dp

    NexusFlashReceiver receiver;
    address owner;
    address other = address(0xBEEF);

    function setUp() public {
        // Fork live Arbitrum. Defaults to the public RPC if ARB_RPC_URL is unset.
        string memory rpc = vm.envOr("ARB_RPC_URL", string("https://arb1.arbitrum.io/rpc"));
        vm.createSelectFork(rpc);
        owner = address(this);
        receiver = new NexusFlashReceiver(owner, AAVE_V3_POOL, UNI_V3_ROUTER, BALANCER_VAULT);
    }

    function _steps(bool roundTrip) internal pure returns (bytes memory) {
        uint256 n = roundTrip ? 2 : 1;
        ArbitrageLib.SwapStep[] memory s = new ArbitrageLib.SwapStep[](n);
        s[0] = ArbitrageLib.SwapStep(0, address(0), USDC, WETH, 500, 0, 0, 0, bytes32(0));
        if (roundTrip) {
            s[1] = ArbitrageLib.SwapStep(0, address(0), WETH, USDC, 3000, 0, 0, 0, bytes32(0));
        }
        return abi.encode(s);
    }

    function test_ForksRealArbitrum() public view {
        assertEq(block.chainid, 42161, "not Arbitrum One");
        assertGt(AAVE_V3_POOL.code.length, 0, "Aave Pool has no bytecode on fork");
    }

    function test_DeploysWithCorrectImmutables() public view {
        assertEq(receiver.AAVE_POOL(), AAVE_V3_POOL);
        assertEq(receiver.UNISWAP_V3_ROUTER(), UNI_V3_ROUTER);
        assertEq(receiver.BALANCER_VAULT(), BALANCER_VAULT);
        assertEq(receiver.owner(), owner);
    }

    function test_RejectsNonAaveCaller() public {
        vm.prank(other);
        vm.expectRevert(
            abi.encodeWithSelector(NexusFlashReceiver.OnlyAavePool.selector, other, AAVE_V3_POOL)
        );
        receiver.executeOperation(USDC, 1, 0, address(receiver), "");
    }

    function test_InitiateFlashLoanOwnerOnly() public {
        vm.prank(other);
        vm.expectRevert(); // OwnableUnauthorizedAccount
        receiver.initiateFlashLoan(USDC, 1_000000, _steps(false));
    }

    // CRYPTO-MOVING PATH: a real 100-USDC flash loan runs Aave flashLoanSimple →
    // USDC→WETH→USDC through real Uniswap V3 pools → reverts InsufficientProfit on
    // the unprofitable round-trip. Funds never leave the contract.
    function test_UnprofitableFlashLoanReverts() public {
        vm.expectRevert(); // Aave bubbles the receiver's InsufficientProfit revert
        receiver.initiateFlashLoan(USDC, 100_000000, _steps(true)); // 100 USDC
    }

    function test_PauseBlocksInitiate() public {
        receiver.pause();
        vm.expectRevert(); // EnforcedPause
        receiver.initiateFlashLoan(USDC, 1_000000, _steps(false));
        receiver.unpause();
    }

    function test_OwnerGating() public {
        vm.prank(other);
        vm.expectRevert();
        receiver.rescueTokens(USDC, 1, other);

        vm.prank(other);
        vm.expectRevert();
        receiver.rescueETH(1, payable(other));

        vm.prank(other);
        vm.expectRevert();
        receiver.pause();
    }

    // The Gelato-relay entry point must reject any caller that is not the Gelato
    // ERC-2771 relay forwarder — including the owner calling it directly.
    function test_RelayEntryRejectsNonGelato() public {
        vm.expectRevert(bytes("onlyGelatoRelayERC2771"));
        receiver.initiateFlashLoanRelay(USDC, 1_000000, _steps(false), 1_000000);

        vm.prank(other);
        vm.expectRevert(bytes("onlyGelatoRelayERC2771"));
        receiver.initiateFlashLoanRelay(USDC, 1_000000, _steps(false), 1_000000);
    }

    function test_RescueETH() public {
        vm.deal(address(receiver), 1 ether);
        uint256 before = address(this).balance;
        receiver.rescueETH(1 ether, payable(address(this)));
        assertEq(address(this).balance, before + 1 ether, "ETH not rescued");
        assertEq(address(receiver).balance, 0, "receiver still holds ETH");
    }

    // Needed for test_RescueETH: the test contract is the owner/recipient.
    receive() external payable {}

    // PROPERTY: no fuzzed round-trip can complete while leaving the owner poorer.
    // Either the arb reverts (efficient market — the common case) or, if it somehow
    // completes, the owner's USDC balance must not have decreased. Fuzzes loan size
    // across 1 USDC → 1M USDC and both legs' fee tiers across the three real Uni V3 tiers.
    //
    // Checks the OWNER's balance, not the receiver's: initiateFlashLoan sweeps
    // every trade's outcome straight to owner() before returning (see _sweep,
    // called unconditionally at the end of initiateFlashLoan), so the
    // receiver's own balance is always 0 immediately afterward regardless of
    // whether the trade won or lost — asserting against it was asserting
    // against a constant. The owner's balance is where a real loss (or gain)
    // actually shows up, and is what NexusFlashReceiver.executeOperation's
    // balance-delta profit check (see its comment there) exists to protect.
    function testFuzz_RoundTripNeverLeavesLoss(uint256 loanUsdcRaw, uint8 feeAIdx, uint8 feeBIdx) public {
        uint24[3] memory tiers = [uint24(500), uint24(3000), uint24(10000)];
        uint256 loanAmount = bound(loanUsdcRaw, 1e6, 1_000_000e6);
        ArbitrageLib.SwapStep[] memory s = new ArbitrageLib.SwapStep[](2);
        s[0] = ArbitrageLib.SwapStep(0, address(0), USDC, WETH, tiers[feeAIdx % 3], 0, 0, 0, bytes32(0));
        s[1] = ArbitrageLib.SwapStep(0, address(0), WETH, USDC, tiers[feeBIdx % 3], 0, 0, 0, bytes32(0));
        uint256 ownerBalBefore = IERC20(USDC).balanceOf(owner);
        try receiver.initiateFlashLoan(USDC, loanAmount, abi.encode(s)) {
            assertGe(IERC20(USDC).balanceOf(owner), ownerBalBefore, "loss on 'successful' arb");
        } catch { /* expected: efficient market → most routes revert. Property: no loss ever completes. */ }
    }
}
