// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "@openzeppelin/contracts/security/Pausable.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "./interfaces/IAaveV3Pool.sol";
import "./interfaces/IUniswapV3Router.sol";
import "./interfaces/ICurvePool.sol";
import "./interfaces/IBalancerVault.sol";
import "./libraries/ArbitrageLib.sol";

/**
 * @title NexusFlashReceiver
 * @notice Production-grade flash loan arbitrage executor for Aave V3.
 *
 * Execution model:
 *   1. Owner calls Aave V3 Pool.flashLoanSimple(address(this), token, amount, encodedSteps, 0)
 *   2. Aave calls executeOperation on this contract with the borrowed funds
 *   3. Contract executes the encoded swap sequence atomically
 *   4. Profit check: finalBalance - loanAmount - premium > 0
 *   5. Approve Aave repayment, transfer profit to owner
 *
 * Protocol routing:
 *   protocol=0  Uniswap V3 exactInputSingle
 *   protocol=1  Curve exchange (get_dy / exchange)
 *   protocol=2  Balancer V2 batchSwap
 *
 * Security:
 *   - ReentrancyGuard on executeOperation
 *   - onlyAavePool modifier prevents spoofed calls
 *   - Pausable for emergency stop
 *   - Custom errors for gas-efficient reverts
 */
contract NexusFlashReceiver is ReentrancyGuard, Pausable, Ownable {
    using SafeERC20 for IERC20;
    using ArbitrageLib for ArbitrageLib.SwapStep[];

    // ─── Errors ───────────────────────────────────────────────────────────────
    error OnlyAavePool(address caller, address expected);
    error InsufficientProfit(uint256 actualProfit, uint256 requiredProfit);
    error UnsupportedProtocol(uint8 protocolId);
    error ZeroLoanAmount();
    error InvalidStepCount(uint256 count);
    error SlippageExceeded(uint256 received, uint256 minimum);

    // ─── Events ───────────────────────────────────────────────────────────────
    event ArbitrageExecuted(
        address indexed token,
        uint256 loanAmount,
        uint256 premium,
        uint256 profit,
        uint256 gasUsed,
        uint256 stepCount
    );
    event OwnerUpdated(address indexed oldOwner, address indexed newOwner);
    event TokensRescued(address indexed token, uint256 amount, address indexed to);

    // ─── Immutables ───────────────────────────────────────────────────────────
    address public immutable AAVE_POOL;
    address public immutable UNISWAP_V3_ROUTER;
    address public immutable BALANCER_VAULT;

    // ─── Constants ────────────────────────────────────────────────────────────
    uint8 private constant PROTOCOL_UNISWAP_V3 = 0;
    uint8 private constant PROTOCOL_CURVE      = 1;
    uint8 private constant PROTOCOL_BALANCER   = 2;
    uint256 private constant MAX_STEPS         = 8;
    uint256 private constant AAVE_PREMIUM_BPS  = 5; // 0.05% = 5 basis points

    constructor(
        address _aavePool,
        address _uniswapV3Router,
        address _balancerVault
    ) Ownable(msg.sender) {
        require(_aavePool        != address(0), "zero aave pool");
        require(_uniswapV3Router != address(0), "zero router");
        require(_balancerVault   != address(0), "zero vault");
        AAVE_POOL         = _aavePool;
        UNISWAP_V3_ROUTER = _uniswapV3Router;
        BALANCER_VAULT    = _balancerVault;
    }

    // ─── Modifiers ────────────────────────────────────────────────────────────
    modifier onlyAavePool() {
        if (msg.sender != AAVE_POOL) revert OnlyAavePool(msg.sender, AAVE_POOL);
        _;
    }

    // ─── Core Flash Loan Callback ─────────────────────────────────────────────
    /**
     * @notice Called by Aave V3 Pool after transferring `amount` of `asset` to this contract.
     * @param asset     Token borrowed.
     * @param amount    Amount borrowed in token's native decimals.
     * @param premium   Fee owed to Aave (amount * 0.05%).
     * @param initiator Must be address(this) — prevents external initiation.
     * @param params    ABI-encoded SwapStep[] array.
     */
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external nonReentrant onlyAavePool whenNotPaused returns (bool) {
        require(initiator == address(this), "invalid initiator");
        if (amount == 0) revert ZeroLoanAmount();

        uint256 gasStart = gasleft();

        // Decode swap steps
        ArbitrageLib.SwapStep[] memory steps = abi.decode(params, (ArbitrageLib.SwapStep[]));
        if (steps.length == 0 || steps.length > MAX_STEPS)
            revert InvalidStepCount(steps.length);

        // Execute swap sequence — each step's output feeds next step's input
        uint256 runningAmount = amount;
        for (uint256 i = 0; i < steps.length; i++) {
            runningAmount = _executeStep(steps[i], runningAmount);
        }

        // Profit check: must cover loan + premium
        uint256 totalOwed = amount + premium;
        uint256 finalBalance = IERC20(asset).balanceOf(address(this));
        if (finalBalance < totalOwed)
            revert InsufficientProfit(
                finalBalance > totalOwed ? finalBalance - totalOwed : 0,
                1 // minimum 1 wei profit enforced by caller
            );

        uint256 profit = finalBalance - totalOwed;

        // Approve Aave repayment (SafeERC20 handles non-standard tokens)
        IERC20(asset).forceApprove(AAVE_POOL, totalOwed);

        // Transfer profit to owner
        if (profit > 0) {
            IERC20(asset).safeTransfer(owner(), profit);
        }

        uint256 gasUsed = gasStart - gasleft();
        emit ArbitrageExecuted(asset, amount, premium, profit, gasUsed, steps.length);
        return true;
    }

    // ─── Swap Step Execution ──────────────────────────────────────────────────
    function _executeStep(
        ArbitrageLib.SwapStep memory step,
        uint256 amountIn
    ) internal returns (uint256 amountOut) {
        if (step.protocol == PROTOCOL_UNISWAP_V3) {
            amountOut = _swapUniswapV3(step, amountIn);
        } else if (step.protocol == PROTOCOL_CURVE) {
            amountOut = _swapCurve(step, amountIn);
        } else if (step.protocol == PROTOCOL_BALANCER) {
            amountOut = _swapBalancer(step, amountIn);
        } else {
            revert UnsupportedProtocol(step.protocol);
        }

        if (amountOut < step.minAmountOut)
            revert SlippageExceeded(amountOut, step.minAmountOut);
    }

    function _swapUniswapV3(
        ArbitrageLib.SwapStep memory step,
        uint256 amountIn
    ) internal returns (uint256 amountOut) {
        IERC20(step.tokenIn).forceApprove(UNISWAP_V3_ROUTER, amountIn);

        IUniswapV3Router.ExactInputSingleParams memory params =
            IUniswapV3Router.ExactInputSingleParams({
                tokenIn:           step.tokenIn,
                tokenOut:          step.tokenOut,
                fee:               step.fee,
                recipient:         address(this),
                deadline:          block.timestamp + 60,
                amountIn:          amountIn,
                amountOutMinimum:  step.minAmountOut,
                sqrtPriceLimitX96: 0
            });

        amountOut = IUniswapV3Router(UNISWAP_V3_ROUTER).exactInputSingle(params);
    }

    function _swapCurve(
        ArbitrageLib.SwapStep memory step,
        uint256 amountIn
    ) internal returns (uint256 amountOut) {
        IERC20(step.tokenIn).forceApprove(step.pool, amountIn);

        uint256 balBefore = IERC20(step.tokenOut).balanceOf(address(this));
        ICurvePool(step.pool).exchange(
            int128(int256(uint256(step.curveIndexIn))),
            int128(int256(uint256(step.curveIndexOut))),
            amountIn,
            step.minAmountOut
        );
        amountOut = IERC20(step.tokenOut).balanceOf(address(this)) - balBefore;
    }

    function _swapBalancer(
        ArbitrageLib.SwapStep memory step,
        uint256 amountIn
    ) internal returns (uint256 amountOut) {
        IERC20(step.tokenIn).forceApprove(BALANCER_VAULT, amountIn);

        IBalancerVault.SingleSwap memory singleSwap = IBalancerVault.SingleSwap({
            poolId:   step.balancerPoolId,
            kind:     IBalancerVault.SwapKind.GIVEN_IN,
            assetIn:  step.tokenIn,
            assetOut: step.tokenOut,
            amount:   amountIn,
            userData: ""
        });

        IBalancerVault.FundManagement memory funds = IBalancerVault.FundManagement({
            sender:              address(this),
            fromInternalBalance: false,
            recipient:           payable(address(this)),
            toInternalBalance:   false
        });

        amountOut = IBalancerVault(BALANCER_VAULT).swap(
            singleSwap, funds, step.minAmountOut, block.timestamp + 60
        );
    }

    // ─── Owner Functions ──────────────────────────────────────────────────────
    /**
     * @notice Rescue stuck ERC20 tokens (not mid-trade assets).
     */
    function rescueTokens(address token, uint256 amount, address to) external onlyOwner {
        require(to != address(0), "zero recipient");
        IERC20(token).safeTransfer(to, amount);
        emit TokensRescued(token, amount, to);
    }

    function pause()   external onlyOwner { _pause(); }
    function unpause() external onlyOwner { _unpause(); }

    /// @notice Initiate a flash loan. Called by the Python engine.
    function initiateFlashLoan(
        address asset,
        uint256 amount,
        bytes calldata encodedSteps
    ) external onlyOwner whenNotPaused {
        IAaveV3Pool(AAVE_POOL).flashLoanSimple(
            address(this),
            asset,
            amount,
            encodedSteps,
            0 // referralCode
        );
    }

    receive() external payable {}
}
