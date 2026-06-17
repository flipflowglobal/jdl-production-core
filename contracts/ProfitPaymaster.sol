// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// ============================================================
// ProfitPaymaster.sol — EIP-4337 Paymaster
// Sponsors gas ONLY when projected flash loan profit >= gas cost
// ============================================================

interface IEntryPoint {
    function depositTo(address account) external payable;
    function balanceOf(address account) external view returns (uint256);
    function withdrawTo(address payable w, uint256 amount) external;
}

struct UserOperation {
    address sender; uint256 nonce; bytes initCode; bytes callData;
    uint256 callGasLimit; uint256 verificationGasLimit; uint256 preVerificationGas;
    uint256 maxFeePerGas; uint256 maxPriorityFeePerGas; bytes paymasterAndData; bytes signature;
}

contract ProfitPaymaster {
    address public owner;
    IEntryPoint public immutable entryPoint;

    uint256 public minProfitUSDC6   = 5_000_000;  // $5
    uint256 public profitToGasRatio = 3;           // profit >= 3x gas
    uint256 public replenishThreshold = 0.05 ether;
    uint256 public replenishAmount    = 0.1  ether;

    mapping(address => bool) public approvedContracts;

    event GasSponsored(address indexed sender, uint256 gasCost, uint256 projectedProfit);
    event Replenished(uint256 amount);

    modifier onlyOwner()      { require(msg.sender==owner,          "!owner"); _; }
    modifier onlyEntryPoint() { require(msg.sender==address(entryPoint), "!ep");    _; }

    constructor(address _ep) { owner=msg.sender; entryPoint=IEntryPoint(_ep); }
    receive() external payable {}

    function deposit() external payable { entryPoint.depositTo{value:msg.value}(address(this)); }
    function approveContract(address fc) external onlyOwner { approvedContracts[fc]=true;  }
    function revokeContract(address fc)  external onlyOwner { approvedContracts[fc]=false; }

    /// paymasterAndData layout:
    ///   [0:20]   paymaster address (this)
    ///   [20:40]  flash contract address
    ///   [40:72]  minProfitOverride (uint256)
    ///   [72:104] projectedProfit   (uint256)
    function validatePaymasterUserOp(
        UserOperation calldata userOp,
        bytes32,
        uint256 maxCost
    ) external onlyEntryPoint returns (bytes memory context, uint256 validationData) {
        require(userOp.paymasterAndData.length>=104,"bad pmd");
        address fc  = address(bytes20(userOp.paymasterAndData[20:40]));
        uint256 mpo = uint256(bytes32(userOp.paymasterAndData[40:72]));
        uint256 pp  = uint256(bytes32(userOp.paymasterAndData[72:104]));
        require(approvedContracts[fc],"!approved");
        uint256 minP = mpo>0 ? mpo : minProfitUSDC6;
        require(pp>=minP,"profit too low");
        uint256 costUSD6 = (maxCost*2000*1e6)/1e18;  // ~$2000/ETH
        require(pp>=costUSD6*profitToGasRatio,"profit:gas ratio low");
        context = abi.encode(userOp.sender, maxCost, pp);
        validationData = 0;
    }

    function postOp(uint8, bytes calldata context, uint256 actualGasCost) external onlyEntryPoint {
        (address sender,,uint256 pp) = abi.decode(context,(address,uint256,uint256));
        emit GasSponsored(sender, actualGasCost, pp);
        _replenishIfLow();
    }

    function _replenishIfLow() internal {
        if (entryPoint.balanceOf(address(this))<replenishThreshold && address(this).balance>=replenishAmount) {
            entryPoint.depositTo{value:replenishAmount}(address(this));
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
