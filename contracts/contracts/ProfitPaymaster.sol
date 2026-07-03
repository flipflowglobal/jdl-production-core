// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
// NOTE: OpenZeppelin's MessageHashUtils.sol requires solc ^0.8.24, but this
// project pins solc 0.8.20 everywhere (foundry.toml, every other contract). The
// eth_sign prefix hash is one line and doesn't need a library — hand-rolled below
// (identical to what MessageHashUtils.toEthSignedMessageHash computes) so the
// pinned compiler version stays untouched. ECDSA.sol itself only needs ^0.8.20,
// so signature recovery still goes through OZ's audited implementation.

// ============================================================
// ProfitPaymaster.sol — EIP-4337 Paymaster
// Sponsors gas ONLY when projected flash loan profit >= gas cost
//
// Dynamic ETH/USD pricing via a Chainlink feed (replaces a hardcoded $2000/ETH)
// with oracle-signed profit verification, gated by a paymaster-level nonce.
// ============================================================

interface IEntryPoint {
    function depositTo(address account) external payable;
    function balanceOf(address account) external view returns (uint256);
    function withdrawTo(address payable w, uint256 amount) external;
}

interface IAggregatorV3 {
    function latestRoundData()
        external
        view
        returns (
            uint80 roundId,
            int256 answer,
            uint256 startedAt,
            uint256 updatedAt,
            uint80 answeredInRound
        );
    function decimals() external view returns (uint8);
}

struct UserOperation {
    address sender; uint256 nonce; bytes initCode; bytes callData;
    uint256 callGasLimit; uint256 verificationGasLimit; uint256 preVerificationGas;
    uint256 maxFeePerGas; uint256 maxPriorityFeePerGas; bytes paymasterAndData; bytes signature;
}

contract ProfitPaymaster {
    address public owner;
    address public trustedOracle;       // off-chain oracle that signs profit verification
    IEntryPoint public immutable entryPoint;
    IAggregatorV3 public immutable ethUsdFeed;  // Chainlink ETH/USD feed

    uint256 public minProfitUSDC6   = 5_000_000;  // $5
    uint256 public profitToGasRatio = 3;           // profit >= 3x gas
    uint256 public replenishThreshold = 0.05 ether;
    uint256 public replenishAmount    = 0.1  ether;
    uint256 public maxPriceFeedAge = 1 hours;      // reject stale Chainlink data

    mapping(address => bool) public approvedContracts;
    mapping(bytes32 => bool) public usedNonces;    // paymaster-level replay guard

    event GasSponsored(address indexed sender, uint256 gasCost, uint256 projectedProfit);
    event Replenished(uint256 amount);
    event OracleUpdated(address indexed newOracle);

    modifier onlyOwner()      { require(msg.sender==owner,          "!owner"); _; }
    modifier onlyEntryPoint() { require(msg.sender==address(entryPoint), "!ep");    _; }

    constructor(address _ep, address _ethUsdFeed, address _trustedOracle) {
        require(_ep != address(0), "!ep");
        require(_ethUsdFeed != address(0), "!feed");
        require(_trustedOracle != address(0), "!oracle");
        owner = msg.sender;
        entryPoint = IEntryPoint(_ep);
        ethUsdFeed = IAggregatorV3(_ethUsdFeed);
        trustedOracle = _trustedOracle;
    }
    receive() external payable {}

    function deposit() external payable { entryPoint.depositTo{value:msg.value}(address(this)); }
    function approveContract(address fc) external onlyOwner { approvedContracts[fc]=true;  }
    function revokeContract(address fc)  external onlyOwner { approvedContracts[fc]=false; }

    function setTrustedOracle(address _oracle) external onlyOwner {
        require(_oracle != address(0), "!address");
        trustedOracle = _oracle;
        emit OracleUpdated(_oracle);
    }

    function setMaxPriceFeedAge(uint256 v) external onlyOwner { maxPriceFeedAge = v; }

    /// @notice Current ETH/USD price from the configured Chainlink feed.
    /// @return ethPriceUSD6 ETH price in USD with 6 decimals (e.g. 2500e6 = $2500).
    function getEthPrice() public view returns (uint256 ethPriceUSD6) {
        (uint80 roundId, int256 answer, , uint256 updatedAt, uint80 answeredInRound) =
            ethUsdFeed.latestRoundData();
        require(answer > 0, "invalid price");
        require(block.timestamp - updatedAt <= maxPriceFeedAge, "stale price");
        require(answeredInRound >= roundId, "stale round");

        uint8 decimals = ethUsdFeed.decimals();
        if (decimals == 8) {
            ethPriceUSD6 = uint256(answer) / 100;       // 8dp -> 6dp
        } else if (decimals == 18) {
            ethPriceUSD6 = uint256(answer) / 1e12;       // 18dp -> 6dp
        } else {
            ethPriceUSD6 = (uint256(answer) * 1e6) / (10 ** uint256(decimals));
        }
    }

    /// paymasterAndData layout:
    ///   [0:20]    paymaster address (this)
    ///   [20:40]   flash contract address
    ///   [40:72]   minProfitOverride (uint256)
    ///   [72:104]  projectedProfit   (uint256)
    ///   [104:136] nonce (uint256)          — paymaster-level replay guard
    ///   [136:201] oracle signature (65 bytes)
    function validatePaymasterUserOp(
        UserOperation calldata userOp,
        bytes32,
        uint256 maxCost
    ) external view onlyEntryPoint returns (bytes memory context, uint256 validationData) {
        require(userOp.paymasterAndData.length >= 201, "bad pmd");

        address fc    = address(bytes20(userOp.paymasterAndData[20:40]));
        uint256 mpo   = uint256(bytes32(userOp.paymasterAndData[40:72]));
        uint256 pp    = uint256(bytes32(userOp.paymasterAndData[72:104]));
        uint256 nonce = uint256(bytes32(userOp.paymasterAndData[104:136]));
        bytes memory signature = userOp.paymasterAndData[136:201];

        require(approvedContracts[fc], "!approved");

        uint256 minP = mpo > 0 ? mpo : minProfitUSDC6;
        require(pp >= minP, "profit too low");

        uint256 ethPriceUSD6 = getEthPrice();
        uint256 costUSD6 = (maxCost * ethPriceUSD6) / 1e18;
        require(pp >= costUSD6 * profitToGasRatio, "profit:gas ratio low");

        // The SAME (fc, pp, sender, nonce) tuple is what gets signed off-chain and
        // what postOp marks used below — a mismatch here would let a signature be
        // replayed indefinitely (usedNonces would guard a hash that never actually
        // gets marked, since only postOp writes state).
        bytes32 nonceKey = keccak256(abi.encodePacked(fc, pp, userOp.sender, nonce));
        require(!usedNonces[nonceKey], "nonce used");

        bytes32 messageHash = keccak256(abi.encodePacked(fc, pp, userOp.sender, nonce));
        bytes32 digest = keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", messageHash));
        require(ECDSA.recover(digest, signature) == trustedOracle, "invalid signature");

        context = abi.encode(userOp.sender, maxCost, pp, nonce, fc);
        validationData = 0;
    }

    function postOp(uint8, bytes calldata context, uint256 actualGasCost) external onlyEntryPoint {
        (address sender, , uint256 pp, uint256 nonce, address fc) =
            abi.decode(context, (address, uint256, uint256, uint256, address));

        bytes32 nonceKey = keccak256(abi.encodePacked(fc, pp, sender, nonce));
        usedNonces[nonceKey] = true;

        emit GasSponsored(sender, actualGasCost, pp);
        _replenishIfLow();
    }

    function _replenishIfLow() internal {
        if (
            entryPoint.balanceOf(address(this)) < replenishThreshold &&
            address(this).balance >= replenishAmount
        ) {
            entryPoint.depositTo{value: replenishAmount}(address(this));
            emit Replenished(replenishAmount);
        }
    }

    function withdrawFromEntryPoint(uint256 a) external onlyOwner { entryPoint.withdrawTo(payable(owner),a); }
    function setMinProfit(uint256 v)          external onlyOwner { minProfitUSDC6=v; }
    function setProfitToGasRatio(uint256 v)   external onlyOwner { profitToGasRatio=v; }
    function setReplenishThreshold(uint256 v) external onlyOwner { replenishThreshold=v; }
    function setReplenishAmount(uint256 v)    external onlyOwner { replenishAmount=v; }
    function transferOwnership(address n)     external onlyOwner { owner=n; }
}
