// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// Stateful invariant fuzzing for NexusFlashReceiver on an Arbitrum One fork.
// forge-std/Test inherits StdInvariant, so targetContract() is available here.
//
// A Handler drives fuzzed flash-loan attempts (bounded loan size + real Uni V3
// fee tiers), swallowing the expected reverts of unprofitable routes. After every
// call sequence the harness asserts the two invariants below. This complements the
// example tests in NexusFlashReceiver.t.sol — different harness shape, own file.
//
// Run:  ARB_RPC_URL=https://arb1.arbitrum.io/rpc \
//         forge test --match-path test/NexusFlashReceiverInvariant.t.sol -vv
import {Test} from "forge-std/Test.sol";
import {NexusFlashReceiver} from "../contracts/NexusFlashReceiver.sol";
import {ArbitrageLib} from "../contracts/ArbitrageLib.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

// ─── Handler: the only contract the invariant fuzzer pokes ──────────────────────
contract FlashHandler is Test {
    NexusFlashReceiver public immutable receiver;
    address constant USDC = 0xaf88d065e77c8cC2239327C5EDb3A432268e5831; // 6dp
    address constant WETH = 0x82aF49447D8a07e3bd95BD0d56f35241523fBab1; // 18dp

    constructor(NexusFlashReceiver _receiver) {
        receiver = _receiver;
    }

    // Fuzzed entrypoint. Bounds inputs to realistic ranges and absorbs the
    // (expected) reverts so the invariant is still evaluated after each call.
    function tryArb(uint256 loanUsdcRaw, uint8 feeAIdx, uint8 feeBIdx) external {
        uint24[3] memory tiers = [uint24(500), uint24(3000), uint24(10000)];
        uint256 loanAmount = bound(loanUsdcRaw, 1e6, 1_000_000e6);
        ArbitrageLib.SwapStep[] memory s = new ArbitrageLib.SwapStep[](2);
        s[0] = ArbitrageLib.SwapStep(0, address(0), USDC, WETH, tiers[feeAIdx % 3], 0, 0, 0, bytes32(0));
        s[1] = ArbitrageLib.SwapStep(0, address(0), WETH, USDC, tiers[feeBIdx % 3], 0, 0, 0, bytes32(0));
        try receiver.initiateFlashLoan(USDC, loanAmount, abi.encode(s)) {} catch {}
    }
}

contract NexusFlashReceiverInvariantTest is Test {
    // ─── Real Arbitrum One addresses ───────────────────────────────────────────
    address constant AAVE_V3_POOL   = 0x794a61358D6845594F94dc1DB02A252b5b4814aD;
    address constant UNI_V3_ROUTER  = 0xE592427A0AEce92De3Edee1F18E0157C05861564;
    address constant BALANCER_VAULT = 0xBA12222222228d8Ba445958a75a0704d566BF2C8;
    address constant USDC           = 0xaf88d065e77c8cC2239327C5EDb3A432268e5831;
    address constant WETH           = 0x82aF49447D8a07e3bd95BD0d56f35241523fBab1;

    NexusFlashReceiver receiver;
    FlashHandler handler;
    address deployer;

    function setUp() public {
        string memory rpc = vm.envOr("ARB_RPC_URL", string("https://arb1.arbitrum.io/rpc"));
        vm.createSelectFork(rpc);
        deployer = address(this);
        receiver = new NexusFlashReceiver(deployer, AAVE_V3_POOL, UNI_V3_ROUTER, BALANCER_VAULT);
        handler = new FlashHandler(receiver);
        // Only the handler's tryArb() is fuzzed — never the raw receiver.
        targetContract(address(handler));
    }

    // The receiver is a pass-through executor: completed arbs sweep profit to the
    // owner and repay Aave; reverted arbs roll back entirely. Either way the receiver
    // must never sit on borrowed/intermediate tokens — a nonzero balance would mean
    // funds are stuck in the contract.
    function invariant_receiverNeverRetainsTokenBalance() public view {
        assertEq(IERC20(USDC).balanceOf(address(receiver)), 0, "receiver retains USDC");
        assertEq(IERC20(WETH).balanceOf(address(receiver)), 0, "receiver retains WETH");
    }

    // No flash-loan path can change ownership.
    function invariant_ownerNeverChanges() public view {
        assertEq(receiver.owner(), deployer, "owner changed");
    }
}
