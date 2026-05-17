// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title ArbitrageLib
 * @notice Shared data structures and pure math for arbitrage route encoding.
 *
 * Math:
 *   minProfit = loanAmount * (premiumBps / 10000) + gasPrice * gasLimit
 *   pathHash  = keccak256(abi.encode(steps))
 *   Uniswap V3 packed path: abi.encodePacked(token0, fee0, token1, fee1, token2...)
 */
library ArbitrageLib {

    uint256 private constant BPS_DENOMINATOR = 10_000;
    uint256 private constant AAVE_PREMIUM_BPS = 5; // 0.05%

    struct SwapStep {
        uint8   protocol;        // 0=UniV3, 1=Curve, 2=Balancer
        address pool;            // pool or router address
        address tokenIn;
        address tokenOut;
        uint24  fee;             // Uniswap V3 fee tier (500/3000/10000)
        uint256 minAmountOut;    // slippage floor
        // Curve-specific
        uint8   curveIndexIn;
        uint8   curveIndexOut;
        // Balancer-specific
        bytes32 balancerPoolId;
    }

    /**
     * @notice Minimum gross output needed to cover Aave premium + gas cost.
     * @param loanAmount  Flash loan principal in token wei.
     * @param gasPrice    Current gas price in wei/gas.
     * @param gasLimit    Estimated gas units for the full arbitrage tx.
     * @return minGross   Minimum token units that must be returned to break even.
     */
    function calculateMinProfit(
        uint256 loanAmount,
        uint256 gasPrice,
        uint256 gasLimit
    ) internal pure returns (uint256 minGross) {
        uint256 premium   = (loanAmount * AAVE_PREMIUM_BPS) / BPS_DENOMINATOR;
        uint256 gasCostWei = gasPrice * gasLimit;
        // gasCostWei is in ETH-wei; caller responsible for converting to token units
        // This function returns token-denominated minimum
        minGross = loanAmount + premium + gasCostWei;
    }

    /**
     * @notice Compute keccak256 hash of an encoded step array for deduplication.
     */
    function computePathHash(SwapStep[] memory steps) internal pure returns (bytes32) {
        return keccak256(abi.encode(steps));
    }

    /**
     * @notice Encode a Uniswap V3 multi-hop path.
     *         Format: token0 (20B) | fee0 (3B) | token1 (20B) | fee1 (3B) | token2 (20B)
     */
    function encodeUniswapV3Path(
        address[] memory tokens,
        uint24[]  memory fees
    ) internal pure returns (bytes memory path) {
        require(tokens.length >= 2 && fees.length == tokens.length - 1, "invalid path");
        path = abi.encodePacked(tokens[0]);
        for (uint256 i = 0; i < fees.length; i++) {
            path = abi.encodePacked(path, fees[i], tokens[i + 1]);
        }
    }

    /**
     * @notice Attempt to extract a profit uint256 from raw revert bytes.
     *         Used for dry-run profit estimation via staticcall.
     *         Expects revert data: abi.encodeWithSignature("InsufficientProfit(uint256,uint256)", ...)
     */
    function decodeProfitFromRevertData(
        bytes memory revertData
    ) internal pure returns (uint256 profit) {
        if (revertData.length < 68) return 0;
        // Skip 4-byte selector, read first uint256 argument (actual profit)
        assembly {
            profit := mload(add(revertData, 36))
        }
    }
}
