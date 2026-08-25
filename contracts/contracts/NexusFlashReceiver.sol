// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import "@openzeppelin/contracts/utils/Pausable.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "./interfaces/IAaveV3Pool.sol";
import "./interfaces/IUniswapV3Router.sol";
import "./interfaces/ICurvePool.sol";
import "./interfaces/IBalancerVault.sol";
import "./ArbitrageLib.sol";

/**
 * @title GelatoRelayERC2771Context
 * @notice Minimal, self-contained Gelato Relay (ERC-2771) context.
 * @dev Vendored instead of importing the gelatonetwork/relay-context package because
 *      that package (v4.1.1) uses SafeERC20.safePermit, which OpenZeppelin v5 removed —
 *      it will not compile against this project's OZ ^5.6.1. The relay forwarder
 *      address and the appended-calldata offsets below are copied verbatim from
 *      gelatonetwork/relay-context v4.1.1:
 *        - GELATO_RELAY_ERC2771_V1 (constants/GelatoRelay.sol) is the forwarder used
 *          on Arbitrum One (42161) and Arbitrum Sepolia (421614), both of which map to
 *          the V1 relay in the package's GelatoRelayContractsUtils.
 *        - Gelato appends [feeCollector(20B)][feeToken(20B)][fee(32B)][msgSender(20B)]
 *          to calldata, read from the tail at offsets 92/72/52/20.
 *      This contract is Arbitrum-only, so the single V1 address is exact — no
 *      per-chain resolution is needed.
 */
abstract contract GelatoRelayERC2771Context {
    using SafeERC20 for IERC20;

    address internal constant GELATO_RELAY_ERC2771 =
        0xb539068872230f20456CF38EC52EF2f91AF4AE49;

    modifier onlyGelatoRelayERC2771() {
        require(msg.sender == GELATO_RELAY_ERC2771, "onlyGelatoRelayERC2771");
        _;
    }

    function _getFeeCollector() internal pure returns (address r) {
        assembly { r := shr(96, calldataload(sub(calldatasize(), 92))) }
    }
    function _getFeeToken() internal pure returns (address r) {
        assembly { r := shr(96, calldataload(sub(calldatasize(), 72))) }
    }
    function _getFee() internal pure returns (uint256 r) {
        assembly { r := calldataload(sub(calldatasize(), 52)) }
    }
    function _getMsgSender() internal pure returns (address r) {
        assembly { r := shr(96, calldataload(sub(calldatasize(), 20))) }
    }

    /// @dev Reimburse Gelato from this contract's balance, bounded by `maxFee`.
    function _transferRelayFeeCapped(uint256 maxFee) internal {
        uint256 fee = _getFee();
        require(fee <= maxFee, "relay fee > maxFee");
        IERC20(_getFeeToken()).safeTransfer(_getFeeCollector(), fee);
    }
}

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
contract NexusFlashReceiver is ReentrancyGuard, Pausable, Ownable, GelatoRelayERC2771Context {
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

    // `_owner` is explicit (not msg.sender) so the contract can be deployed by a
    // Gelato relayer or a CREATE2 factory while ownership still lands on the operator.
    constructor(
        address _owner,
        address _aavePool,
        address _uniswapV3Router,
        address _balancerVault
    ) Ownable(_owner) {
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

        // Captured before the swap loop runs. Aave has already transferred
        // `amount` into this contract by the time executeOperation is called,
        // so balanceBefore = amount + whatever balance the contract already
        // held. Gating the profit check on this — the balance DELTA since
        // entry — rather than on the absolute post-trade balance is the fix:
        // the old absolute check (finalBalance >= amount+premium) could pass
        // on a genuinely losing route whenever pre-existing/leftover contract
        // balance happened to cover the shortfall, silently spending that
        // balance and reporting the loss as "profit". That breaks the one
        // safety guarantee this whole system is built on — an unprofitable
        // route must revert (only gas lost), never quietly succeed at the
        // owner's own balance's expense. A delta-based check can't be
        // gamed that way: it only ever credits what THIS trade actually
        // earned.
        uint256 balanceBefore = IERC20(asset).balanceOf(address(this));

        // Decode swap steps
        ArbitrageLib.SwapStep[] memory steps = abi.decode(params, (ArbitrageLib.SwapStep[]));
        if (steps.length == 0 || steps.length > MAX_STEPS)
            revert InvalidStepCount(steps.length);

        // Execute swap sequence — each step's output feeds next step's input
        uint256 runningAmount = amount;
        for (uint256 i = 0; i < steps.length; i++) {
            runningAmount = _executeStep(steps[i], runningAmount);
        }

        // Profit check: this trade's own gain must cover the Aave premium.
        // `gained >= premium` is equivalent to `finalBalance >= balanceBefore
        // + premium`, and since balanceBefore already includes the borrowed
        // `amount`, that is strictly stronger than (and implies) the solvency
        // check the old code performed directly — finalBalance >= amount +
        // premium — so nothing is lost by replacing it outright.
        uint256 finalBalance = IERC20(asset).balanceOf(address(this));
        uint256 gained = finalBalance > balanceBefore ? finalBalance - balanceBefore : 0;
        if (gained < premium)
            revert InsufficientProfit(gained, premium);

        uint256 totalOwed = amount + premium;
        uint256 profit = finalBalance - totalOwed;

        // Approve Aave repayment (SafeERC20 handles non-standard tokens). Aave pulls
        // `totalOwed` via transferFrom after this returns, leaving exactly `profit` in
        // the contract. Profit is intentionally NOT swept here — the initiating call
        // (initiateFlashLoan or initiateFlashLoanRelay) forwards it afterward, so the
        // Gelato-relay path can first deduct the relayer fee from that profit.
        IERC20(asset).forceApprove(AAVE_POOL, totalOwed);

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

        // slither-disable-next-line timestamp
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

        // slither-disable-next-line calls-loop
        amountOut = IUniswapV3Router(UNISWAP_V3_ROUTER).exactInputSingle(params);
    }

    function _swapCurve(
        ArbitrageLib.SwapStep memory step,
        uint256 amountIn
    ) internal returns (uint256 amountOut) {
        IERC20(step.tokenIn).forceApprove(step.pool, amountIn);

        // slither-disable-next-line calls-loop
        uint256 balBefore = IERC20(step.tokenOut).balanceOf(address(this));
        // slither-disable-next-line unused-return,calls-loop
        ICurvePool(step.pool).exchange(
            int128(int256(uint256(step.curveIndexIn))),
            int128(int256(uint256(step.curveIndexOut))),
            amountIn,
            step.minAmountOut
        );
        // slither-disable-next-line calls-loop
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

        // slither-disable-next-line calls-loop
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

    /**
     * @notice Rescue stuck native ETH (the flash-loan path never holds ETH — WETH is
     *         ERC20 — but receive() would otherwise trap any ETH sent by mistake).
     *         NOTE: deployments made before this function exist cannot rescue ETH;
     *         never send ETH to those instances.
     */
    function rescueETH(uint256 amount, address payable to) external onlyOwner {
        require(to != address(0), "zero recipient");
        emit TokensRescued(address(0), amount, to);
        (bool ok, ) = to.call{value: amount}("");
        require(ok, "eth transfer failed");
    }

    function pause()   external onlyOwner { _pause(); }
    function unpause() external onlyOwner { _unpause(); }

    /// @notice Initiate a flash loan directly (owner pays L2 gas in ETH).
    ///         After the atomic flash loan repays Aave, all remaining profit in `asset`
    ///         is swept to the owner.
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
        _sweep(asset, owner());
    }

    /// @notice Gasless flash loan via Gelato Relay (ERC-2771). Gelato pays the L2 gas
    ///         and is reimbursed from trade profit in `asset`; the remainder goes to
    ///         the owner. The wallet never needs to hold ETH.
    /// @dev    `onlyGelatoRelayERC2771` guarantees the caller is Gelato's relay contract,
    ///         which appends the verified signer + fee context to calldata. We require
    ///         that signer to be the owner, and that the fee is charged in `asset` (which
    ///         the contract holds as profit). The fee is bounded by the owner-signed
    ///         `maxFee`. Everything is atomic: if profit can't cover the fee, the trade
    ///         reverts and no funds move.
    /// @param maxFee Upper bound on the Gelato relayer fee, denominated in `asset`.
    function initiateFlashLoanRelay(
        address asset,
        uint256 amount,
        bytes calldata encodedSteps,
        uint256 maxFee
    ) external onlyGelatoRelayERC2771 whenNotPaused {
        require(_getMsgSender() == owner(), "relay: not owner");
        require(_getFeeToken()  == asset,   "relay: fee token != asset");
        IAaveV3Pool(AAVE_POOL).flashLoanSimple(
            address(this),
            asset,
            amount,
            encodedSteps,
            0 // referralCode
        );
        _transferRelayFeeCapped(maxFee); // reimburse Gelato from profit, bounded
        _sweep(asset, owner());          // remainder to owner
    }

    /// @dev Forward the contract's full balance of `asset` to `to`.
    function _sweep(address asset, address to) internal {
        uint256 bal = IERC20(asset).balanceOf(address(this));
        if (bal > 0) {
            IERC20(asset).safeTransfer(to, bal);
        }
    }

    receive() external payable {}
}
