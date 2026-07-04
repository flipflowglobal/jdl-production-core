// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// Self-contained tests for ProfitPaymaster — no mainnet fork needed (no DeFi
// protocol calls; just an EntryPoint + a Chainlink-style price feed + ECDSA).
//
// Run:  forge test --match-path test/ProfitPaymaster.t.sol -vv
import {Test} from "forge-std/Test.sol";
import {ProfitPaymaster, UserOperation, IAggregatorV3} from "../contracts/ProfitPaymaster.sol";

contract MockEntryPoint {
    mapping(address => uint256) public balanceOf_;
    function depositTo(address account) external payable { balanceOf_[account] += msg.value; }
    function balanceOf(address account) external view returns (uint256) { return balanceOf_[account]; }
    function withdrawTo(address payable w, uint256 amount) external {
        balanceOf_[msg.sender] -= amount;
        w.transfer(amount);
    }
}

contract MockAggregatorV3 is IAggregatorV3 {
    int256 public answer = 2500e8;   // $2500, 8 decimals (Chainlink's real ETH/USD convention)
    uint8  public dec = 8;
    uint256 public updatedAt_;
    uint80 public roundId_ = 1;
    uint80 public answeredInRound_ = 1;

    constructor() { updatedAt_ = block.timestamp; }

    function setPrice(int256 a) external { answer = a; updatedAt_ = block.timestamp; }
    function setStale(uint256 secondsAgo) external { updatedAt_ = block.timestamp > secondsAgo ? block.timestamp - secondsAgo : 0; }
    function setDecimals(uint8 d) external { dec = d; }
    function setRoundMismatch() external { answeredInRound_ = roundId_ - 1 < roundId_ ? 0 : roundId_; roundId_ = 5; answeredInRound_ = 1; }

    function latestRoundData() external view returns (uint80, int256, uint256, uint256, uint80) {
        return (roundId_, answer, 0, updatedAt_, answeredInRound_);
    }
    function decimals() external view returns (uint8) { return dec; }
}

contract ProfitPaymasterTest is Test {
    ProfitPaymaster pm;
    MockEntryPoint ep;
    MockAggregatorV3 feed;
    uint256 oraclePk = 0xA11CE;
    address oracle;
    address flashContract = address(0xFEED);
    address user = address(0xBEEF);

    function setUp() public {
        // Foundry's default block.timestamp is 1. MockAggregatorV3.setStale()
        // subtracts a "seconds ago" offset from block.timestamp, so with the
        // default clock any offset over a second (e.g. the 2 hours used by
        // test_RejectsStalePriceFeed) would underflow-guard to 0 instead of
        // producing a genuinely stale updatedAt_ — silently defeating that
        // test. Warp to a realistic timestamp so staleness math behaves.
        vm.warp(1_700_000_000);
        oracle = vm.addr(oraclePk);
        ep = new MockEntryPoint();
        feed = new MockAggregatorV3();
        pm = new ProfitPaymaster(address(ep), address(feed), oracle);
        pm.approveContract(flashContract);
    }

    // ─── helpers ──────────────────────────────────────────────────────────────
    // Mirrors ProfitPaymaster.verifyProfitSignature: chainId + this paymaster +
    // fc + pp + sender + nonce + deadline + callDataHash, eth-signed-message hashed.
    function _sign(
        uint256 pk, address fc, uint256 pp, address sender, uint256 nonce,
        uint256 deadline, bytes32 callDataHash
    ) internal view returns (bytes memory sig) {
        bytes32 messageHash = keccak256(abi.encodePacked(
            block.chainid, address(pm), fc, pp, sender, nonce, deadline, callDataHash
        ));
        bytes32 digest = MessageHashLib(messageHash);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, digest);
        sig = abi.encodePacked(r, s, v);
    }

    // mirrors MessageHashUtils.toEthSignedMessageHash for a bytes32 input
    function MessageHashLib(bytes32 messageHash) internal pure returns (bytes32) {
        return keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", messageHash));
    }

    function _pmd(address fc, uint256 mpo, uint256 pp, uint256 nonce, uint256 deadline, bytes memory sig)
        internal view returns (bytes memory)
    {
        return abi.encodePacked(address(pm), fc, mpo, pp, nonce, deadline, sig);
    }

    // Default deadline (an hour out) and callDataHash (matching the empty
    // callData every _userOp() in this file uses) for tests that don't care
    // about exercising those two fields specifically.
    function _defaultDeadline() internal view returns (uint256) { return block.timestamp + 1 hours; }
    function _emptyCallDataHash() internal pure returns (bytes32) { return keccak256(bytes("")); }

    function _userOp(address sender, bytes memory paymasterAndData) internal pure returns (UserOperation memory) {
        return UserOperation({
            sender: sender, nonce: 0, initCode: "", callData: "",
            callGasLimit: 0, verificationGasLimit: 0, preVerificationGas: 0,
            maxFeePerGas: 0, maxPriorityFeePerGas: 0,
            paymasterAndData: paymasterAndData, signature: ""
        });
    }

    // ─── tests ────────────────────────────────────────────────────────────────

    function test_ValidSignatureAndSufficientProfitPasses() public {
        uint256 pp = 20_000_000; // $20 projected profit (way above min + ratio)
        bytes memory sig = _sign(oraclePk, flashContract, pp, user, 1, _defaultDeadline(), _emptyCallDataHash());
        bytes memory pmd = _pmd(flashContract, 0, pp, 1, _defaultDeadline(), sig);
        vm.prank(address(ep));
        (bytes memory context, uint256 validationData) =
            pm.validatePaymasterUserOp(_userOp(user, pmd), bytes32(0), 100_000);
        assertEq(validationData, 0);
        assertTrue(context.length > 0);
    }

    function test_RejectsWrongSigner() public {
        uint256 wrongPk = 0xBAD;
        uint256 pp = 20_000_000;
        bytes memory sig = _sign(wrongPk, flashContract, pp, user, 1, _defaultDeadline(), _emptyCallDataHash());
        bytes memory pmd = _pmd(flashContract, 0, pp, 1, _defaultDeadline(), sig);
        vm.prank(address(ep));
        vm.expectRevert(bytes("invalid signature"));
        pm.validatePaymasterUserOp(_userOp(user, pmd), bytes32(0), 100_000);
    }

    function test_RejectsUnapprovedContract() public {
        address notApproved = address(0xDEAD);
        uint256 pp = 20_000_000;
        bytes memory sig = _sign(oraclePk, notApproved, pp, user, 1, _defaultDeadline(), _emptyCallDataHash());
        bytes memory pmd = _pmd(notApproved, 0, pp, 1, _defaultDeadline(), sig);
        vm.prank(address(ep));
        vm.expectRevert(bytes("!approved"));
        pm.validatePaymasterUserOp(_userOp(user, pmd), bytes32(0), 100_000);
    }

    function test_RejectsBelowMinProfit() public {
        uint256 pp = 1_000_000; // $1, below the $5 default minimum
        bytes memory sig = _sign(oraclePk, flashContract, pp, user, 1, _defaultDeadline(), _emptyCallDataHash());
        bytes memory pmd = _pmd(flashContract, 0, pp, 1, _defaultDeadline(), sig);
        vm.prank(address(ep));
        vm.expectRevert(bytes("profit too low"));
        pm.validatePaymasterUserOp(_userOp(user, pmd), bytes32(0), 100_000);
    }

    function test_RejectsInsufficientProfitToGasRatio() public {
        // pp just above minProfitUSDC6 ($5) but maxCost implies a gas cost whose
        // 3x ratio requirement it can't clear.
        uint256 pp = 6_000_000; // $6
        uint256 maxCost = 1 ether; // absurdly large gas cost vs $6 profit
        bytes memory sig = _sign(oraclePk, flashContract, pp, user, 1, _defaultDeadline(), _emptyCallDataHash());
        bytes memory pmd = _pmd(flashContract, 0, pp, 1, _defaultDeadline(), sig);
        vm.prank(address(ep));
        vm.expectRevert(bytes("profit:gas ratio low"));
        pm.validatePaymasterUserOp(_userOp(user, pmd), bytes32(0), maxCost);
    }

    function test_RejectsExpiredDeadline() public {
        uint256 pp = 20_000_000;
        uint256 deadline = block.timestamp == 0 ? 0 : block.timestamp - 1; // already past
        bytes memory sig = _sign(oraclePk, flashContract, pp, user, 1, deadline, _emptyCallDataHash());
        bytes memory pmd = _pmd(flashContract, 0, pp, 1, deadline, sig);
        vm.prank(address(ep));
        vm.expectRevert(bytes("sig expired"));
        pm.validatePaymasterUserOp(_userOp(user, pmd), bytes32(0), 100_000);
    }

    function test_RejectsStalePriceFeed() public {
        feed.setStale(2 hours); // maxPriceFeedAge default is 1 hour
        uint256 pp = 20_000_000;
        bytes memory sig = _sign(oraclePk, flashContract, pp, user, 1, _defaultDeadline(), _emptyCallDataHash());
        bytes memory pmd = _pmd(flashContract, 0, pp, 1, _defaultDeadline(), sig);
        vm.prank(address(ep));
        vm.expectRevert(bytes("stale price"));
        pm.validatePaymasterUserOp(_userOp(user, pmd), bytes32(0), 100_000);
    }

    function test_RejectsNonPositivePrice() public {
        feed.setPrice(0);
        uint256 pp = 20_000_000;
        bytes memory sig = _sign(oraclePk, flashContract, pp, user, 1, _defaultDeadline(), _emptyCallDataHash());
        bytes memory pmd = _pmd(flashContract, 0, pp, 1, _defaultDeadline(), sig);
        vm.prank(address(ep));
        vm.expectRevert(bytes("invalid price"));
        pm.validatePaymasterUserOp(_userOp(user, pmd), bytes32(0), 100_000);
    }

    // THE regression test: this is the exact bug found in the uploaded reference
    // implementation — postOp recomputed profitHash with address(0) instead of the
    // real flash-contract address, so the nonce that gated the signature check was
    // never actually the one marked used, making every signature replayable
    // indefinitely. This proves the fix: the SAME signed request is rejected the
    // second time, once postOp has run.
    function test_ReplayIsRejectedAfterPostOp() public {
        uint256 pp = 20_000_000;
        uint256 deadline = _defaultDeadline();
        bytes memory sig = _sign(oraclePk, flashContract, pp, user, 1, deadline, _emptyCallDataHash());
        bytes memory pmd = _pmd(flashContract, 0, pp, 1, deadline, sig);

        vm.prank(address(ep));
        (bytes memory context, ) = pm.validatePaymasterUserOp(_userOp(user, pmd), bytes32(0), 100_000);

        // Simulate the EntryPoint calling postOp after successful execution.
        vm.prank(address(ep));
        pm.postOp(0, context, 50_000);

        // Replaying the EXACT same signed request must now fail — the whole point
        // of the nonce.
        vm.prank(address(ep));
        vm.expectRevert(bytes("nonce used"));
        pm.validatePaymasterUserOp(_userOp(user, pmd), bytes32(0), 100_000);
    }

    // A different nonce with the same underlying profit claim is a DIFFERENT
    // signed message (the oracle must sign per-nonce), so an unsigned reused
    // nonce value alone can't be substituted — confirms nonces are bound into
    // what's actually signed, not just checked as a side channel.
    function test_DifferentNonceRequiresDifferentSignature() public {
        uint256 pp = 20_000_000;
        uint256 deadline = _defaultDeadline();
        bytes memory sigForNonce1 = _sign(oraclePk, flashContract, pp, user, 1, deadline, _emptyCallDataHash());
        // Attempt to reuse nonce=1's signature while claiming nonce=2 in the payload.
        bytes memory pmd = _pmd(flashContract, 0, pp, 2, deadline, sigForNonce1);
        vm.prank(address(ep));
        vm.expectRevert(bytes("invalid signature"));
        pm.validatePaymasterUserOp(_userOp(user, pmd), bytes32(0), 100_000);
    }

    function test_GetEthPriceHandles8And18DecimalFeeds() public {
        feed.setDecimals(8);
        feed.setPrice(3000e8);
        assertEq(pm.getEthPrice(), 3000e6);

        feed.setDecimals(18);
        feed.setPrice(3000e18);
        assertEq(pm.getEthPrice(), 3000e6);
    }

    function test_OnlyOwnerCanApproveOrRevoke() public {
        vm.prank(user);
        vm.expectRevert(bytes("!owner"));
        pm.approveContract(address(0x1234));

        vm.prank(user);
        vm.expectRevert(bytes("!owner"));
        pm.revokeContract(flashContract);
    }

    function test_OnlyEntryPointCanValidate() public {
        uint256 pp = 20_000_000;
        bytes memory sig = _sign(oraclePk, flashContract, pp, user, 1, _defaultDeadline(), _emptyCallDataHash());
        bytes memory pmd = _pmd(flashContract, 0, pp, 1, _defaultDeadline(), sig);
        vm.expectRevert(bytes("!ep"));
        pm.validatePaymasterUserOp(_userOp(user, pmd), bytes32(0), 100_000);
    }

    function test_ConstructorRejectsZeroAddresses() public {
        vm.expectRevert(bytes("!ep"));
        new ProfitPaymaster(address(0), address(feed), oracle);
        vm.expectRevert(bytes("!feed"));
        new ProfitPaymaster(address(ep), address(0), oracle);
        vm.expectRevert(bytes("!oracle"));
        new ProfitPaymaster(address(ep), address(feed), address(0));
    }
}
