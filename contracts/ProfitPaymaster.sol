// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// ============================================================
// ProfitPaymaster.sol — EIP-4337 Paymaster
// Sponsors gas ONLY when projected flash loan profit >= gas cost
// ============================================================

interface IEntryPoint {
    function depositTo(address account) external payable;
    function balanceOf(address account) external view returns (uint256);
    function withdrawTo(address payable withdrawAddress, uint256 withdrawAmount) external;
    function getNonce(address sender, uint192 key) external view returns (uint256);
}

struct UserOperation {
    address sender;
    uint256 nonce;
    bytes initCode;
    bytes callData;
    uint256 callGasLimit;
    uint256 verificationGasLimit;
    uint256 preVerificationGas;
    uint256 maxFeePerGas;
    uint256 maxPriorityFeePerGas;
    bytes paymasterAndData;
    bytes signature;
}

contract ProfitPaymaster {
    address public owner;
    IEntryPoint public immutable entryPoint;

    uint256 public minProfitUSDC6     = 5_000_000;  // $5 minimum profit
    uint256 public profitToGasRatio   = 3;           // profit must be 3x gas cost
    uint256 public replenishThreshold = 0.05 ether;
    uint256 public replenishAmount    = 0.1 ether;

    mapping(address => bool) public approvedContracts;

    event GasSponsored(address indexed sender, uint256 gasUsed, uint256 projectedProfit);
    event Replenished(uint256 amount);

    modifier onlyOwner() { require(msg.sender == owner, "not owner"); _; }
    modifier onlyEntryPoint() { require(msg.sender == address(entryPoint), "not entry point"); _; }

    constructor(address _entryPoint) {
        owner = msg.sender;
        entryPoint = IEntryPoint(_entryPoint);
    }

    receive() external payable {}

    function deposit() external payable {
        entryPoint.depositTo{value: msg.value}(address(this));
    }

    function approveContract(address fc) external onlyOwner { approvedContracts[fc] = true; }
    function revokeContract(address fc)  external onlyOwner { approvedContracts[fc] = false; }

    /// @notice Called by EntryPoint to validate whether we sponsor this UserOp.
    ///         paymasterAndData layout:
    ///           [0:20]   = paymaster address (this)
    ///           [20:40]  = flash contract address
    ///           [40:72]  = minProfitOverride (uint256)
    ///           [72:104] = projectedProfit   (uint256)
    function validatePaymasterUserOp(
        UserOperation calldata userOp,
        bytes32 /*userOpHash*/,
        uint256 maxCost
    ) external onlyEntryPoint returns (bytes memory context, uint256 validationData) {
        require(userOp.paymasterAndData.length >= 104, "bad paymasterData");

        address flashContract  = address(bytes20(userOp.paymasterAndData[20:40]));
        uint256 minProfitOverride = uint256(bytes32(userOp.paymasterAndData[40:72]));
        uint256 projectedProfit   = uint256(bytes32(userOp.paymasterAndData[72:104]));

        require(approvedContracts[flashContract], "unapproved contract");

        uint256 minP = minProfitOverride > 0 ? minProfitOverride : minProfitUSDC6;
        require(projectedProfit >= minP, "profit too low");

        // Convert maxCost (ETH wei) to approximate USDC-6 (assume 2000 USD/ETH)
        uint256 maxCostUSD6 = (maxCost * 2000 * 1e6) / 1e18;
        require(projectedProfit >= maxCostUSD6 * profitToGasRatio, "profit:gas ratio too low");

        context = abi.encode(userOp.sender, maxCost, projectedProfit);
        validationData = 0; // valid
    }

    function postOp(
        uint8 /*mode*/,
        bytes calldata context,
        uint256 actualGasCost
    ) external onlyEntryPoint {
        (address sender, , uint256 projectedProfit) = abi.decode(context, (address, uint256, uint256));
        emit GasSponsored(sender, actualGasCost, projectedProfit);
        _replenishIfLow();
    }

    function _replenishIfLow() internal {
        if (entryPoint.balanceOf(address(this)) < replenishThreshold && address(this).balance >= replenishAmount) {
            entryPoint.depositTo{value: replenishAmount}(address(this));
            emit Replenished(replenishAmount);
        }
    }

    function withdrawFromEntryPoint(uint256 amount) external onlyOwner {
        entryPoint.withdrawTo(payable(owner), amount);
    }

    function setMinProfit(uint256 usd6) external onlyOwner { minProfitUSDC6 = usd6; }
    function setProfitToGasRatio(uint256 r) external onlyOwner { profitToGasRatio = r; }
    function setReplenishThreshold(uint256 t) external onlyOwner { replenishThreshold = t; }
    function setReplenishAmount(uint256 a) external onlyOwner { replenishAmount = a; }
    function transferOwnership(address newOwner) external onlyOwner { owner = newOwner; }
}
