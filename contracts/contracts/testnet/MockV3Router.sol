// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

/**
 * @title MockV3Router
 * @notice Testnet-only stand-in for Uniswap V3's SwapRouter. Arbitrum Sepolia has
 * no real ISwapRouter deployment (confirmed against Uniswap's own deployment docs
 * — only Factory and QuoterV2 are live there), so NexusFlashReceiver's Uniswap leg
 * has no real counterparty to call on that testnet. This contract implements the
 * exact `exactInputSingle` signature NexusFlashReceiver calls, backed by a simple
 * constant-product pool per (tokenIn, tokenOut, fee) that the owner seeds directly
 * — letting the receiver's real borrow -> swap -> swap -> repay path actually
 * execute and be broadcast on Sepolia, for mechanics/integration testing.
 *
 * NOT for mainnet use. NOT audited for anything beyond letting a test wallet
 * (owner-seeded, owner-only liquidity) exercise the real receiver contract.
 */
contract MockV3Router {
    using SafeERC20 for IERC20;

    address public immutable owner;

    // One pool per (tokenIn, tokenOut, fee) triple, keyed symmetrically so a swap
    // in either direction reads/writes the same underlying pool.
    mapping(address => mapping(address => mapping(uint24 => uint256))) public reserveIn;
    mapping(address => mapping(address => mapping(uint24 => uint256))) public reserveOut;

    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 deadline;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }

    event Seeded(address indexed tokenA, address indexed tokenB, uint24 fee, uint256 amountA, uint256 amountB);
    event Swapped(address indexed tokenIn, address indexed tokenOut, uint24 fee, uint256 amountIn, uint256 amountOut);

    error NotOwner();
    error Expired();
    error NoLiquidity();
    error SlippageTooHigh();

    constructor() {
        owner = msg.sender;
    }

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    /// @notice Seeds a (tokenA, tokenB, fee) pool with both sides of liquidity.
    /// Caller must have approved this contract for both amounts beforehand.
    /// Seeding the same (tokenA, tokenB, fee) triple twice adds to the existing
    /// pool rather than resetting it.
    function seedLiquidity(
        address tokenA,
        uint256 amountA,
        address tokenB,
        uint256 amountB,
        uint24 fee
    ) external onlyOwner {
        IERC20(tokenA).safeTransferFrom(msg.sender, address(this), amountA);
        IERC20(tokenB).safeTransferFrom(msg.sender, address(this), amountB);
        reserveIn[tokenA][tokenB][fee] += amountA;
        reserveOut[tokenA][tokenB][fee] += amountB;
        reserveIn[tokenB][tokenA][fee] += amountB;
        reserveOut[tokenB][tokenA][fee] += amountA;
        emit Seeded(tokenA, tokenB, fee, amountA, amountB);
    }

    /// @notice Matches IUniswapV3Router.exactInputSingle exactly (contracts/interfaces/IUniswapV3Router.sol).
    function exactInputSingle(ExactInputSingleParams calldata params) external returns (uint256 amountOut) {
        if (block.timestamp > params.deadline) revert Expired();

        uint256 rIn = reserveIn[params.tokenIn][params.tokenOut][params.fee];
        uint256 rOut = reserveOut[params.tokenIn][params.tokenOut][params.fee];
        if (rIn == 0 || rOut == 0) revert NoLiquidity();

        // Constant product; fee is in hundredths of a bip, same units Uniswap uses
        // (500 = 0.05%, 3000 = 0.30%), taken off amountIn before the swap math.
        uint256 amountInAfterFee = (params.amountIn * (1_000_000 - params.fee)) / 1_000_000;
        amountOut = (rOut * amountInAfterFee) / (rIn + amountInAfterFee);
        if (amountOut < params.amountOutMinimum) revert SlippageTooHigh();

        IERC20(params.tokenIn).safeTransferFrom(msg.sender, address(this), params.amountIn);

        reserveIn[params.tokenIn][params.tokenOut][params.fee] += params.amountIn;
        reserveOut[params.tokenIn][params.tokenOut][params.fee] -= amountOut;
        // Mirror pool (tokenOut, tokenIn, fee) is the same pool from the other
        // side — keep it consistent so a subsequent reverse swap sees the update.
        reserveIn[params.tokenOut][params.tokenIn][params.fee] -= amountOut;
        reserveOut[params.tokenOut][params.tokenIn][params.fee] += params.amountIn;

        IERC20(params.tokenOut).safeTransfer(params.recipient, amountOut);
        emit Swapped(params.tokenIn, params.tokenOut, params.fee, params.amountIn, amountOut);
    }
}
