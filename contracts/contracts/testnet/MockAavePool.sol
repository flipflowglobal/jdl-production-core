// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

interface IFlashLoanSimpleReceiver {
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool);
}

/**
 * @title MockAavePool
 * @notice Minimal local test double for Aave V3's Pool.flashLoanSimple. Charges
 * the same 5 bps premium this codebase hardcodes everywhere it matters
 * (flash_loan_engine.py's AAVE_V3.fee_bps, ArbitrageLib.sol, rust/hotpath).
 * Local unit-test tool only — never deployed anywhere real; the real Sepolia
 * deploy uses Aave's actual Pool (0xBfC91D59fdAA134A4ED45f7B584cAf96D7792Eff).
 */
contract MockAavePool {
    using SafeERC20 for IERC20;

    uint256 public constant PREMIUM_BPS = 5;

    /// @notice Seeds this mock pool with liquidity to lend out.
    function fund(address asset, uint256 amount) external {
        IERC20(asset).safeTransferFrom(msg.sender, address(this), amount);
    }

    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 /* referralCode */
    ) external {
        uint256 premium = (amount * PREMIUM_BPS) / 10_000;
        IERC20(asset).safeTransfer(receiverAddress, amount);
        bool ok = IFlashLoanSimpleReceiver(receiverAddress).executeOperation(
            asset,
            amount,
            premium,
            msg.sender,
            params
        );
        require(ok, "executeOperation failed");
        IERC20(asset).safeTransferFrom(receiverAddress, address(this), amount + premium);
    }
}
