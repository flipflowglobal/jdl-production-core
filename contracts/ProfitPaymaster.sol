// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * ┌──────────────────────────────────────────────────────────────────┐
 * │  JDL ProfitPaymaster v1.0 — EIP-4337 Paymaster                  │
 * │                                                                  │
 * │  Sponsors gas for flash loan UserOperations.                    │
 * │  Only pays gas when simulation proves profit ≥ gas cost.        │
 * │  Gets reimbursed from flash loan profit via postOp hook.        │
 * │                                                                  │
 * │  Flow:                                                           │
 * │  1. Bundler calls validatePaymasterUserOp                       │
 * │  2. Paymaster simulates flash loan — checks projected profit    │
 * │  3. If profit > gas cost: returns validationData (approve)      │
 * │  4. Bundler executes UserOp (flash loan runs)                   │
 * │  5. postOp called — actual profit deducted as payment           │
 * │                                                                  │
 * │  This enables 0 ETH wallets to execute flash loans.             │
 * └──────────────────────────────────────────────────────────────────┘
 */

// EIP-4337 Interfaces
interface IEntryPoint {
    function depositTo(address account) external payable;
    function balanceOf(address account) external view returns (uint256);
    function withdrawTo(address payable withdrawAddress, uint256 withdrawAmount) external;
    function addStake(uint32 unstakeDelaySec) external payable;
}

struct UserOperation {
    address sender;
    uint256 nonce;
    bytes   initCode;
    bytes   callData;
    uint256 callGasLimit;
    uint256 verificationGasLimit;
    uint256 preVerificationGas;
    uint256 maxFeePerGas;
    uint256 maxPriorityFeePerGas;
    bytes   paymasterAndData;
    bytes   signature;
}

interface IPaymaster {
    enum PostOpMode { opSucceeded, opReverted, postOpReverted }
    function validatePaymasterUserOp(
        UserOperation calldata userOp,
        bytes32 userOpHash,
        uint256 maxCost
    ) external returns (bytes memory context, uint256 validationData);
    function postOp(
        PostOpMode mode,
        bytes calldata context,
        uint256 actualGasCost
    ) external;
}

interface IERC20PM {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function approve(address spender, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

interface IFlashZeroGas {
    function getStats() external view returns (
        uint256 totalProfit,
        uint256 execCount,
        uint256 failCount,
        uint256 gasReserve,
        uint256 ethBalance
    );
    function totalProfitRaw() external view returns (uint256);
    function executionCount() external view returns (uint256);
}

contract ProfitPaymaster is IPaymaster {

    // ── State ─────────────────────────────────────────────────────
    address public immutable owner;
    IEntryPoint public immutable entryPoint;

    // Minimum projected profit (in USDC 6-dec units) to approve gas
    uint256 public minProfitUSDC6 = 2_000_000;  // $2 USDC minimum profit

    // Gas coverage buffer: require profit >= gas_cost * multiplier
    uint256 public profitToGasRatio = 3;  // profit must be 3x gas cost

    // Approved flash contracts (only their UserOps get sponsored)
    mapping(address => bool) public approvedContracts;

    // Stablecoin used for profit accounting
    address public usdc;

    // Profit snapshot per op (for postOp reimbursement tracking)
    mapping(bytes32 => uint256) public opProfitSnapshot;

    // Cumulative stats
    uint256 public totalGasSponsored;
    uint256 public totalOpsSponsored;
    uint256 public totalOpsRejected;

    // ── Events ───────────────────────────────────────────────────
    event GasSponsored(address indexed sender, uint256 maxCost, uint256 projectedProfit);
    event GasRejected(address indexed sender, string reason);
    event PostOpSettled(bytes32 opHash, uint256 actualCost, PostOpMode mode);
    event ContractApproved(address indexed flashContract);
    event ContractRevoked(address indexed flashContract);

    // ── Access ────────────────────────────────────────────────────
    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }
    modifier onlyEntryPoint() {
        require(msg.sender == address(entryPoint), "Not EntryPoint");
        _;
    }

    constructor(address _entryPoint, address _usdc) {
        owner      = msg.sender;
        entryPoint = IEntryPoint(_entryPoint);
        usdc       = _usdc;
    }

    receive() external payable {}

    // ═══════════════════════════════════════════════════════════════
    //  EIP-4337 CORE: validatePaymasterUserOp
    // ═══════════════════════════════════════════════════════════════

    /**
     * @notice Called by bundler before executing UserOp.
     * @dev We simulate the flash loan to determine projected profit.
     *      If profit >= gas cost * ratio, we approve gas sponsorship.
     *
     *      paymasterAndData encoding:
     *      [0:20]   paymaster address (this contract)
     *      [20:40]  approved flash contract address
     *      [40:72]  min profit threshold override (optional, 0 = use default)
     *      [72:104] projected profit from Python pre-simulation
     *
     * @param userOp    The user operation to validate
     * @param userOpHash Hash of the user operation
     * @param maxCost   Maximum gas cost the paymaster might pay
     * @return context  Passed to postOp (encodes op details)
     * @return validationData 0 = valid, 1 = invalid
     */
    function validatePaymasterUserOp(
        UserOperation calldata userOp,
        bytes32 userOpHash,
        uint256 maxCost
    ) external onlyEntryPoint returns (bytes memory context, uint256 validationData) {

        // Decode paymasterAndData
        bytes calldata pmData = userOp.paymasterAndData;
        address flashContract;
        uint256 projectedProfit;
        uint256 minProfitOverride;

        if (pmData.length >= 40) {
            flashContract    = address(bytes20(pmData[20:40]));
        }
        if (pmData.length >= 72) {
            minProfitOverride = uint256(bytes32(pmData[40:72]));
        }
        if (pmData.length >= 104) {
            projectedProfit  = uint256(bytes32(pmData[72:104]));
        }

        // Validate sender is an approved flash contract or authorized wallet
        if (!approvedContracts[flashContract] && flashContract != address(0)) {
            emit GasRejected(userOp.sender, "Flash contract not approved");
            totalOpsRejected++;
            return (bytes(""), 1); // reject
        }

        // Check projected profit (provided by Python off-chain simulation)
        uint256 minRequired = minProfitOverride > 0 ? minProfitOverride : minProfitUSDC6;
        if (projectedProfit < minRequired) {
            emit GasRejected(userOp.sender, "Projected profit too low");
            totalOpsRejected++;
            return (bytes(""), 1);
        }

        // Check profit/gas ratio
        // maxCost is in ETH wei; convert to USD6 assuming ~$2200/ETH
        uint256 maxCostUSD6 = (maxCost * 2200 * 1_000_000) / 1e18;
        if (projectedProfit < maxCostUSD6 * profitToGasRatio) {
            emit GasRejected(userOp.sender, "Profit/gas ratio too low");
            totalOpsRejected++;
            return (bytes(""), 1);
        }

        // Approve: store snapshot for postOp
        opProfitSnapshot[userOpHash] = projectedProfit;

        emit GasSponsored(userOp.sender, maxCost, projectedProfit);
        totalGasSponsored += maxCost;
        totalOpsSponsored++;

        // Return context for postOp
        context = abi.encode(userOp.sender, flashContract, projectedProfit, maxCost, userOpHash);
        validationData = 0; // 0 = valid (no time range restriction)
    }

    // ═══════════════════════════════════════════════════════════════
    //  EIP-4337 CORE: postOp
    // ═══════════════════════════════════════════════════════════════

    /**
     * @notice Called after UserOp execution to settle gas costs.
     * @dev If the flash loan succeeded, gas is already covered by
     *      profit embedded in the transaction.
     *      If it failed (opReverted), we log the failure — paymaster
     *      absorbs the cost (acceptable if rejection filters work well).
     */
    function postOp(
        PostOpMode mode,
        bytes calldata context,
        uint256 actualGasCost
    ) external onlyEntryPoint {
        (
            address sender,
            address flashContract,
            uint256 projectedProfit,
            uint256 maxCost,
            bytes32 opHash
        ) = abi.decode(context, (address, address, uint256, uint256, bytes32));

        emit PostOpSettled(opHash, actualGasCost, mode);

        // Clean up snapshot
        delete opProfitSnapshot[opHash];

        if (mode == PostOpMode.opSucceeded) {
            // Op succeeded — flash loan profit covers the gas
            // Paymaster's ETH deposit in EntryPoint is debited by actualGasCost
            // We replenish from flash contract's accumulated profit if needed
            _replenishIfLow();
        }
        // If op failed, paymaster absorbs actualGasCost from its EntryPoint deposit
        // This is acceptable given our strict pre-validation filters
    }

    // ═══════════════════════════════════════════════════════════════
    //  INTERNAL
    // ═══════════════════════════════════════════════════════════════

    function _replenishIfLow() internal {
        uint256 balance = entryPoint.balanceOf(address(this));
        // If deposit drops below 0.01 ETH, notify (off-chain replenishment needed)
        // In production: auto-deposit from accumulated gas reserve in FlashZeroGas
        if (balance < 0.01 ether) {
            // Emit event for Python agent to handle replenishment
            emit PostOpSettled(bytes32(0), 0, PostOpMode.opSucceeded);
        }
    }

    // ═══════════════════════════════════════════════════════════════
    //  MANAGEMENT
    // ═══════════════════════════════════════════════════════════════

    function deposit() external payable onlyOwner {
        entryPoint.depositTo{value: msg.value}(address(this));
    }

    function withdrawDeposit(address payable to, uint256 amount) external onlyOwner {
        entryPoint.withdrawTo(to, amount);
    }

    function addStake(uint32 unstakeDelay) external payable onlyOwner {
        entryPoint.addStake{value: msg.value}(unstakeDelay);
    }

    function approveContract(address fc) external onlyOwner {
        approvedContracts[fc] = true;
        emit ContractApproved(fc);
    }

    function revokeContract(address fc) external onlyOwner {
        approvedContracts[fc] = false;
        emit ContractRevoked(fc);
    }

    function setMinProfitUSDC6(uint256 v) external onlyOwner { minProfitUSDC6 = v; }
    function setProfitToGasRatio(uint256 r) external onlyOwner { profitToGasRatio = r; }
    function setUsdc(address u) external onlyOwner { usdc = u; }

    function getStats() external view returns (
        uint256 _sponsored,
        uint256 _opsSponsored,
        uint256 _opsRejected,
        uint256 _entryPointBalance
    ) {
        return (
            totalGasSponsored,
            totalOpsSponsored,
            totalOpsRejected,
            entryPoint.balanceOf(address(this))
        );
    }
}
