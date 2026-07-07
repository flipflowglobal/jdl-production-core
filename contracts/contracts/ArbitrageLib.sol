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

    // ═══════════════════════════════════════════════════════════════════════
    //  Route Splitting & Path Decoding
    // ═══════════════════════════════════════════════════════════════════════

    /**
     * @notice Decode a Uniswap V3 encoded multi-hop path into token and fee arrays.
     * @param path Encoded path: token0 (20B) | fee0 (3B) | token1 (20B) | fee1 (3B) | ...
     * @return tokens Address array of tokens in the path
     * @return fees   Fee tier array between consecutive tokens
     */
    function decodeUniswapV3Path(
        bytes memory path
    ) internal pure returns (address[] memory tokens, uint24[] memory fees) {
        require(path.length >= 43, "path too short"); // at least one hop (20+3+20)
        // slither-disable-next-line divide-before-multiply
        uint256 numHops = (path.length - 20) / 23;
        tokens = new address[](numHops + 1);
        fees   = new uint24[](numHops);

        for (uint256 i = 0; i < numHops; i++) {
            uint256 offset = i * 23;
            assembly {
                let chunk := mload(add(add(path, 32), offset))
                mstore(add(add(tokens, 32), mul(i, 32)), shr(96, chunk))
                mstore(add(add(fees,   32), mul(i, 32)), and(shr(72, chunk), 0xffffff))
            }
        }
        // Last token
        assembly {
            let chunk := mload(add(add(path, 32), mul(numHops, 23)))
            mstore(add(add(tokens, 32), mul(numHops, 32)), shr(96, chunk))
        }
    }

    /**
     * @notice Decode a single Uniswap V3 path and return the amount at each hop.
     * @dev amountsOut[0] = amountIn; subsequent entries are placeholders — use
     *      getAmountsOut() for real values from the Quoter.
     * @param path     Encoded multi-hop path.
     * @param amountIn Initial input amount.
     * @return amountsOut Array of length hopCount+1 with amountIn at index 0.
     */
    function splitSwap(
        bytes memory path,
        uint256 amountIn
    ) internal pure returns (uint256[] memory amountsOut) {
        (address[] memory tokens, ) = decodeUniswapV3Path(path);
        amountsOut = new uint256[](tokens.length);
        amountsOut[0] = amountIn;
    }

    /**
     * @notice Split a total input amount equally across multiple swap paths.
     * @dev Useful for distributing a flash loan into parallel arbitrage routes.
     * @param paths     Array of encoded Uniswap V3 paths.
     * @param totalAmount  Total tokens available to split.
     * @return amountsIn Amount allocated to each path (even split).
     */
    function splitSwap(
        bytes[] memory paths,
        uint256 totalAmount
    ) internal pure returns (uint256[] memory amountsIn) {
        amountsIn = new uint256[](paths.length);
        if (paths.length == 0) return amountsIn;
        // slither-disable-next-line divide-before-multiply
        uint256 perRoute = totalAmount / paths.length;
        uint256 remainder = totalAmount - perRoute * paths.length;
        for (uint256 i = 0; i < paths.length; i++) {
            amountsIn[i] = perRoute + (i < remainder ? 1 : 0);
        }
    }

    /**
     * @notice Query the Uniswap V3 QuoterV2 for expected output amounts across all hops.
     * @dev Calls quoteExactInputSingle for each hop in the path. Returns amounts[0..n]
     *      where amounts[0] = amountIn and amounts[n] = final output.
     * @param quoter   Address of Uniswap V3 QuoterV2 contract.
     * @param path     Encoded multi-hop path.
     * @param amountIn Input token amount.
     * @return amounts Expected amount at each step (index 0 = amountIn).
     */
    function getAmountsOut(
        address quoter,
        bytes memory path,
        uint256 amountIn
    ) internal view returns (uint256[] memory amounts) {
        (address[] memory tokens, uint24[] memory fees) = decodeUniswapV3Path(path);
        amounts = new uint256[](tokens.length);
        amounts[0] = amountIn;

        uint256 currentAmount = amountIn;
        for (uint256 i = 0; i < fees.length; i++) {
            // quoteExactInputSingle(address tokenIn, address tokenOut, uint256 amountIn, uint24 fee, uint160 sqrtPriceLimitX96)
            (bool success, bytes memory data) = quoter.staticcall(
                abi.encodeWithSignature(
                    "quoteExactInputSingle(address,address,uint256,uint24,uint160)",
                    tokens[i],
                    tokens[i + 1],
                    currentAmount,
                    fees[i],
                    0
                )
            );
            if (!success || data.length < 32) break;
            currentAmount = abi.decode(data, (uint256));
            amounts[i + 1] = currentAmount;
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  Deadline Helpers
    // ═══════════════════════════════════════════════════════════════════════

    /**
     * @notice Compute a block.timestamp-based deadline from a TTL offset.
     * @param ttl Seconds from now until the deadline expires.
     * @return deadline Unix timestamp for the deadline.
     */
    function deadlineFromNow(uint256 ttl) internal view returns (uint256 deadline) {
        deadline = block.timestamp + ttl;
    }

    /**
     * @notice Validate that the current block time is before the given deadline.
     */
    function checkDeadline(uint256 deadline) internal view {
        // slither-disable-next-line timestamp
        require(block.timestamp <= deadline, "deadline expired");
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  EIP-712 Signature Helpers
    // ═══════════════════════════════════════════════════════════════════════

    bytes32 private constant EIP712_DOMAIN_TYPEHASH =
        keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)");

    /**
     * @notice Build an EIP-712 domain separator.
     */
    function _buildDomainSeparator(
        string memory name,
        string memory version,
        uint256 chainId,
        address verifyingContract
    ) internal pure returns (bytes32 domainSeparator) {
        domainSeparator = keccak256(
            abi.encode(EIP712_DOMAIN_TYPEHASH, keccak256(bytes(name)), keccak256(bytes(version)), chainId, verifyingContract)
        );
    }

    /**
     * @notice Hash typed data per EIP-712.
     * @param domainSeparator The domain separator.
     * @param structHash      keccak256(abi.encode(typeHash, ...field values)).
     */
    function _hashTypedDataV4(bytes32 domainSeparator, bytes32 structHash) internal pure returns (bytes32) {
        return keccak256(abi.encodePacked("\x19\x01", domainSeparator, structHash));
    }

    /**
     * @notice Verify an ECDSA signature on an EIP-712 typed message.
     * @param signer    Expected signer address.
     * @param digest    EIP-712 message digest.
     * @param signature Raw bytes signature (r, s, v).
     * @return True if the signer matches the signature.
     */
    function verifySignature(
        address signer,
        bytes32 digest,
        bytes calldata signature
    ) internal pure returns (bool) {
        if (signature.length != 65) return false;
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := calldataload(signature.offset)
            s := calldataload(add(signature.offset, 32))
            v := byte(0, calldataload(add(signature.offset, 64)))
        }
        if (v < 27) v += 27;
        address recovered = ecrecover(digest, v, r, s);
        return recovered == signer;
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  Token Transfer Helpers
    // ═══════════════════════════════════════════════════════════════════════

    /**
     * @notice Approve a spender for the maximum amount if current allowance is insufficient.
     * @dev Compatible with USDT-style tokens that require allowance to be 0 first.
     */
    function safeApproveMax(address token, address spender, uint256 amount) internal {
        (bool success, bytes memory data) = token.call(
            abi.encodeWithSignature("allowance(address,address)", address(this), spender)
        );
        if (success && data.length >= 32) {
            uint256 currentAllowance = abi.decode(data, (uint256));
            if (currentAllowance >= amount) return;
            // Reset to 0 first (handles USDT)
            // slither-disable-next-line unused-return
            (success, ) = token.call(
                abi.encodeWithSignature("approve(address,uint256)", spender, 0)
            );
            require(success, "approve reset failed");
        }
        // slither-disable-next-line unused-return
        (success, ) = token.call(
            abi.encodeWithSignature("approve(address,uint256)", spender, amount)
        );
        require(success, "approve failed");
    }
}
