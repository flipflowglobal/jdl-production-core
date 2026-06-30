// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title ICurvePool
 * @notice Minimal interface for Curve StableSwap pools using int128 coin indices.
 * @dev Indices reference the pool's coins() ordering. `exchange` does not return a
 *      value on many older Curve pools, so callers measure output via balance delta.
 */
interface ICurvePool {
    /// @notice Exchange `dx` of coin `i` for coin `j`, requiring at least `min_dy` out.
    function exchange(
        int128 i,
        int128 j,
        uint256 dx,
        uint256 min_dy
    ) external returns (uint256);

    /// @notice Get the amount of coin `j` received for swapping `dx` of coin `i`.
    function get_dy(
        int128 i,
        int128 j,
        uint256 dx
    ) external view returns (uint256);

    /// @notice Address of coin at index `i`.
    function coins(uint256 i) external view returns (address);
}
