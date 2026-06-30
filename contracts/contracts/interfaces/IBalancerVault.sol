// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title IBalancerVault
 * @notice Minimal interface for the Balancer V2 Vault single swap.
 *         Mainnet/Arbitrum Vault: 0xBA12222222228d8Ba445958a75a0704d566BF2C8
 * @dev assetIn/assetOut are plain addresses (Balancer's IAsset is an address alias).
 */
interface IBalancerVault {
    enum SwapKind { GIVEN_IN, GIVEN_OUT }

    struct SingleSwap {
        bytes32  poolId;
        SwapKind kind;
        address  assetIn;
        address  assetOut;
        uint256  amount;
        bytes    userData;
    }

    struct FundManagement {
        address          sender;
        bool             fromInternalBalance;
        address payable  recipient;
        bool             toInternalBalance;
    }

    /// @notice Perform a single swap; returns amountOut (GIVEN_IN) or amountIn (GIVEN_OUT).
    function swap(
        SingleSwap calldata singleSwap,
        FundManagement calldata funds,
        uint256 limit,
        uint256 deadline
    ) external payable returns (uint256);
}
