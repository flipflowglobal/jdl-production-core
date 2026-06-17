// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// ============================================================
// FlashZeroGas.sol — Zero-upfront-gas flash loan arbitrage
// Novel methods: PEG (block.coinbase), Recursive Flash Stack,
// TWAP lag exploitation, Morpho/Balancer 0%-fee flash loans
// ============================================================

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function approve(address spender, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
    function decimals() external view returns (uint8);
}

interface IWETH {
    function deposit() external payable;
    function withdraw(uint256 amount) external;
    function transfer(address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
    function approve(address spender, uint256 amount) external returns (bool);
}

interface IAavePool {
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;
}

interface IBalancerVault {
    function flashLoan(
        address recipient,
        address[] calldata tokens,
        uint256[] calldata amounts,
        bytes calldata userData
    ) external;
}

interface IUniswapV3Pool {
    function flash(
        address recipient,
        uint256 amount0,
        uint256 amount1,
        bytes calldata data
    ) external;
    function token0() external view returns (address);
    function token1() external view returns (address);
    function fee() external view returns (uint24);
}

interface IMorpho {
    function flashLoan(address token, uint256 assets, bytes calldata data) external;
}

interface IUniswapV2Router {
    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata path,
        address to,
        uint256 deadline
    ) external returns (uint256[] memory amounts);
    function getAmountsOut(uint256 amountIn, address[] calldata path)
        external view returns (uint256[] memory amounts);
}

interface IUniswapV3Router {
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
    function exactInputSingle(ExactInputSingleParams calldata params)
        external returns (uint256 amountOut);
}

interface IUniswapV3PoolOracle {
    function observe(uint32[] calldata secondsAgos)
        external view returns (int56[] memory tickCumulatives, uint160[] memory secondsPerLiquidityCumulativeX128s);
    function slot0() external view returns (
        uint160 sqrtPriceX96, int24 tick, uint16 observationIndex, uint16 observationCardinality,
        uint16 observationCardinalityNext, uint8 feeProtocol, bool unlocked
    );
}

contract FlashZeroGas {
    // ── State ────────────────────────────────────────────────────────────────
    address public owner;
    address public constant AAVE_POOL_ARB   = 0x794a61358D6845594F94dc1DB02A252b5b4814aD;
    address public constant AAVE_POOL_ETH   = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;
    address public constant BALANCER_VAULT  = 0xBA12222222228d8Ba445958a75a0704d566BF2C8;
    address public constant MORPHO_BLUE     = 0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFc;
    address public constant WETH_ARB        = 0x82aF49447D8a07e3bd95BD0d56f35241523fBab1;
    address public constant WETH_ETH        = 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2;
    address public constant UNIV3_ROUTER    = 0xE592427A0AEce92De3Edee1F18E0157C05861564;
    address public constant SUSHI_ROUTER    = 0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506;

    uint256 public gasReserve;
    uint256 public totalProfitRaw;
    uint256 public executionCount;
    uint16  public builderFeeBps    = 500;   // 5%  to block builder (PEG)
    uint16  public gasReserveBps    = 1000;  // 10% to gas reserve
    uint256 public withdrawThresholdUSD6 = 1_000_000_000; // $1000 USDC-6

    // ── Events ───────────────────────────────────────────────────────────────
    event FlashExecuted(address indexed asset, uint256 amount, uint256 profit, string strategy);
    event BuilderPaid(address indexed builder, uint256 amount);
    event GasReserved(uint256 amount);
    event ProfitWithdrawn(address indexed to, uint256 amount);

    // ── Modifiers ────────────────────────────────────────────────────────────
    modifier onlyOwner() { require(msg.sender == owner, "not owner"); _; }

    constructor() { owner = msg.sender; }
    receive() external payable {}

    // ══════════════════════════════════════════════════════════════════════════
    //  PUBLIC ENTRY POINTS
    // ══════════════════════════════════════════════════════════════════════════

    /// @notice Aave V3 flash loan (0.09% fee). Submit via Flashbots with gasPrice=0.
    function executeAaveFlash(
        address pool,
        address asset,
        uint256 amount,
        address tokenInter,
        uint24  buyFee,
        uint24  sellFee,
        uint8   dexType,
        uint256 minProfit,
        uint256 builderFee
    ) external onlyOwner {
        bytes memory params = abi.encode(tokenInter, buyFee, sellFee, dexType, minProfit, builderFee);
        IAavePool(pool).flashLoanSimple(address(this), asset, amount, params, 0);
    }

    /// @notice Balancer V2 flash loan — 0% fee, cheapest available.
    function executeBalancerFlash(
        address[] calldata tokens,
        uint256[] calldata amounts,
        address tokenInter,
        uint24  buyFee,
        uint24  sellFee,
        uint8   dexType,
        uint256 minProfit,
        uint256 builderFee
    ) external onlyOwner {
        bytes memory userData = abi.encode(tokenInter, buyFee, sellFee, dexType, minProfit, builderFee);
        IBalancerVault(BALANCER_VAULT).flashLoan(address(this), tokens, amounts, userData);
    }

    /// @notice Uniswap V3 pool.flash() — ~0.01% fee at 0.01%-tier pools.
    function executeUniswapFlash(
        address pool,
        uint256 amount0,
        uint256 amount1,
        address tokenInter,
        uint24  buyFee,
        uint24  sellFee,
        uint8   dexType,
        uint256 minProfit,
        uint256 builderFee
    ) external onlyOwner {
        bytes memory data = abi.encode(pool, tokenInter, buyFee, sellFee, dexType, minProfit, builderFee);
        IUniswapV3Pool(pool).flash(address(this), amount0, amount1, data);
    }

    /// @notice Morpho Blue flash loan — 0% fee.
    function executeMorphoFlash(
        address token,
        uint256 assets,
        address tokenInter,
        uint24  buyFee,
        uint24  sellFee,
        uint8   dexType,
        uint256 minProfit,
        uint256 builderFee
    ) external onlyOwner {
        bytes memory data = abi.encode(tokenInter, buyFee, sellFee, dexType, minProfit, builderFee, token, assets);
        IMorpho(MORPHO_BLUE).flashLoan(token, assets, data);
    }

    // ══════════════════════════════════════════════════════════════════════════
    //  FLASH CALLBACKS
    // ══════════════════════════════════════════════════════════════════════════

    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address /*initiator*/,
        bytes calldata params
    ) external returns (bool) {
        (address tokenInter, uint24 buyFee, uint24 sellFee, uint8 dexType,
         uint256 minProfit, uint256 builderFee) =
            abi.decode(params, (address, uint24, uint24, uint8, uint256, uint256));

        uint256 profit = _arb(asset, amount, tokenInter, buyFee, sellFee, dexType);
        uint256 repay  = amount + premium;
        require(profit >= minProfit + repay, "insufficient profit");

        _payBuilderAndReserve(builderFee, profit - repay);
        IERC20(asset).approve(AAVE_POOL_ARB, repay);
        IERC20(asset).approve(AAVE_POOL_ETH, repay);
        return true;
    }

    function receiveFlashLoan(
        address[] calldata tokens,
        uint256[] calldata amounts,
        uint256[] calldata feeAmounts,
        bytes calldata userData
    ) external {
        require(msg.sender == BALANCER_VAULT, "not balancer");
        (address tokenInter, uint24 buyFee, uint24 sellFee, uint8 dexType,
         uint256 minProfit, uint256 builderFee) =
            abi.decode(userData, (address, uint24, uint24, uint8, uint256, uint256));

        address asset  = tokens[0];
        uint256 amount = amounts[0];
        uint256 profit = _arb(asset, amount, tokenInter, buyFee, sellFee, dexType);
        uint256 repay  = amount + feeAmounts[0];
        require(profit >= minProfit + repay, "insufficient profit");

        _payBuilderAndReserve(builderFee, profit - repay);
        IERC20(asset).transfer(BALANCER_VAULT, repay);
        emit FlashExecuted(asset, amount, profit - repay, "BALANCER_0FEE");
    }

    function uniswapV3FlashCallback(
        uint256 fee0,
        uint256 fee1,
        bytes calldata data
    ) external {
        (address pool, address tokenInter, uint24 buyFee, uint24 sellFee, uint8 dexType,
         uint256 minProfit, uint256 builderFee) =
            abi.decode(data, (address, address, uint24, uint24, uint8, uint256, uint256));
        require(msg.sender == pool, "not pool");

        address token0 = IUniswapV3Pool(pool).token0();
        address token1 = IUniswapV3Pool(pool).token1();
        uint256 bal0   = IERC20(token0).balanceOf(address(this));
        uint256 bal1   = IERC20(token1).balanceOf(address(this));
        address asset  = bal0 > 0 ? token0 : token1;
        uint256 amount = bal0 > 0 ? bal0 : bal1;
        uint256 fee    = bal0 > 0 ? fee0  : fee1;

        uint256 profit = _arb(asset, amount, tokenInter, buyFee, sellFee, dexType);
        require(profit >= minProfit + amount + fee, "insufficient profit");

        _payBuilderAndReserve(builderFee, profit - amount - fee);
        IERC20(asset).transfer(pool, amount + fee);
        emit FlashExecuted(asset, amount, profit - amount - fee, "UNIV3_FLASH");
    }

    function onMorphoFlashLoan(uint256 assets, bytes calldata data) external {
        require(msg.sender == MORPHO_BLUE, "not morpho");
        (address tokenInter, uint24 buyFee, uint24 sellFee, uint8 dexType,
         uint256 minProfit, uint256 builderFee, address token, uint256 amount) =
            abi.decode(data, (address, uint24, uint24, uint8, uint256, uint256, address, uint256));

        uint256 profit = _arb(token, assets, tokenInter, buyFee, sellFee, dexType);
        require(profit >= minProfit + amount, "insufficient profit");

        _payBuilderAndReserve(builderFee, profit - amount);
        IERC20(token).approve(MORPHO_BLUE, assets);
        emit FlashExecuted(token, assets, profit - amount, "MORPHO_0FEE");
    }

    // ══════════════════════════════════════════════════════════════════════════
    //  NOVEL ZERO-GAS STRATEGIES
    // ══════════════════════════════════════════════════════════════════════════

    /// @notice Recursive Flash Stack: borrow WETH → unwrap → fund gas reserve
    ///         → execute main arb → wrap profit → repay WETH flash
    function recursiveFlashStack(
        address wethPool,
        uint256 wethForGas,
        address arbAsset,
        uint256 arbAmount,
        address tokenInter,
        uint24  buyFee,
        uint24  sellFee,
        uint256 minProfit,
        uint256 builderFee
    ) external onlyOwner {
        bytes memory data = abi.encode(
            "RFS", arbAsset, arbAmount, tokenInter, buyFee, sellFee, minProfit, builderFee, wethForGas
        );
        address weth = block.chainid == 1 ? WETH_ETH : WETH_ARB;
        bool isToken0 = IUniswapV3Pool(wethPool).token0() == weth;
        IUniswapV3Pool(wethPool).flash(
            address(this),
            isToken0 ? wethForGas : 0,
            isToken0 ? 0 : wethForGas,
            data
        );
    }

    /// @notice TWAP Lag Arbitrage: exploit 30-min TWAP oracle lag vs spot price
    function twapArbitrage(
        address pool,
        address assetUnderpriced,
        address assetOverpriced,
        uint256 flashAmount,
        uint24  buyFee,
        uint24  sellFee,
        uint256 minProfit,
        uint256 builderFee
    ) external onlyOwner {
        // Verify TWAP divergence before committing
        (, int24 spotTick, , , , , ) = IUniswapV3PoolOracle(pool).slot0();
        uint32[] memory secondsAgos = new uint32[](2);
        secondsAgos[0] = 1800; // 30 min
        secondsAgos[1] = 0;
        (int56[] memory cumulatives, ) = IUniswapV3PoolOracle(pool).observe(secondsAgos);
        int24 twapTick = int24((cumulatives[1] - cumulatives[0]) / 1800);
        require(spotTick - twapTick > 50 || twapTick - spotTick > 50, "no TWAP divergence");

        bytes memory data = abi.encode(
            "TWAP", assetOverpriced, flashAmount, buyFee, sellFee, minProfit, builderFee
        );
        bool isToken0 = IUniswapV3Pool(pool).token0() == assetUnderpriced;
        IUniswapV3Pool(pool).flash(
            address(this),
            isToken0 ? flashAmount : 0,
            isToken0 ? 0 : flashAmount,
            data
        );
    }

    // ══════════════════════════════════════════════════════════════════════════
    //  INTERNAL LOGIC
    // ══════════════════════════════════════════════════════════════════════════

    function _arb(
        address assetIn,
        uint256 amountIn,
        address tokenInter,
        uint24  buyFee,
        uint24  sellFee,
        uint8   dexType
    ) internal returns (uint256 totalOut) {
        IERC20(assetIn).approve(UNIV3_ROUTER, amountIn);
        uint256 midAmount;
        if (dexType == 0) {
            // UniV3 both legs
            midAmount = IUniswapV3Router(UNIV3_ROUTER).exactInputSingle(
                IUniswapV3Router.ExactInputSingleParams({
                    tokenIn: assetIn, tokenOut: tokenInter, fee: buyFee,
                    recipient: address(this), deadline: block.timestamp,
                    amountIn: amountIn, amountOutMinimum: 0, sqrtPriceLimitX96: 0
                })
            );
            IERC20(tokenInter).approve(UNIV3_ROUTER, midAmount);
            totalOut = IUniswapV3Router(UNIV3_ROUTER).exactInputSingle(
                IUniswapV3Router.ExactInputSingleParams({
                    tokenIn: tokenInter, tokenOut: assetIn, fee: sellFee,
                    recipient: address(this), deadline: block.timestamp,
                    amountIn: midAmount, amountOutMinimum: 0, sqrtPriceLimitX96: 0
                })
            );
        } else {
            // Sushi leg 1, UniV3 leg 2
            IERC20(assetIn).approve(SUSHI_ROUTER, amountIn);
            address[] memory path = new address[](2);
            path[0] = assetIn; path[1] = tokenInter;
            uint256[] memory out1 = IUniswapV2Router(SUSHI_ROUTER).swapExactTokensForTokens(
                amountIn, 0, path, address(this), block.timestamp
            );
            midAmount = out1[out1.length - 1];
            IERC20(tokenInter).approve(UNIV3_ROUTER, midAmount);
            totalOut = IUniswapV3Router(UNIV3_ROUTER).exactInputSingle(
                IUniswapV3Router.ExactInputSingleParams({
                    tokenIn: tokenInter, tokenOut: assetIn, fee: sellFee,
                    recipient: address(this), deadline: block.timestamp,
                    amountIn: midAmount, amountOutMinimum: 0, sqrtPriceLimitX96: 0
                })
            );
        }
    }

    /// @notice PEG — Profit-Embedded Gas: pay block builder from inside tx.
    ///         Submit tx with gasPrice=0 via Flashbots; builder accepts because
    ///         block.coinbase.transfer(builderFee) compensates them directly.
    function _payBuilderAndReserve(uint256 builderFee, uint256 profitRaw) internal {
        address weth = block.chainid == 1 ? WETH_ETH : WETH_ARB;
        // Convert WETH profit to ETH for builder payment
        if (builderFee > 0 && IWETH(weth).balanceOf(address(this)) >= builderFee) {
            IWETH(weth).withdraw(builderFee);
            payable(block.coinbase).transfer(builderFee);
            emit BuilderPaid(block.coinbase, builderFee);
        }
        // Reserve gas fund from profit
        uint256 reserve = (profitRaw * gasReserveBps) / 10000;
        gasReserve += reserve;
        totalProfitRaw += profitRaw;
        executionCount++;
        emit GasReserved(reserve);
    }

    // ══════════════════════════════════════════════════════════════════════════
    //  ADMIN & WITHDRAWAL
    // ══════════════════════════════════════════════════════════════════════════

    /// @notice Only callable once totalProfitRaw >= withdrawThresholdUSD6.
    function withdrawToken(address token, uint256 amount, address to) external onlyOwner {
        require(totalProfitRaw >= withdrawThresholdUSD6, "below threshold — reinvesting");
        IERC20(token).transfer(to, amount);
        emit ProfitWithdrawn(to, amount);
    }

    function withdrawETH(uint256 amount, address payable to) external onlyOwner {
        require(totalProfitRaw >= withdrawThresholdUSD6, "below threshold — reinvesting");
        require(address(this).balance >= amount, "low balance");
        to.transfer(amount);
        emit ProfitWithdrawn(to, amount);
    }

    // Emergency bypass (no threshold check)
    function emergencyWithdrawToken(address token, address to) external onlyOwner {
        IERC20(token).transfer(to, IERC20(token).balanceOf(address(this)));
    }
    function emergencyWithdrawETH(address payable to) external onlyOwner {
        to.transfer(address(this).balance);
    }

    function setBuilderFeeBps(uint16 bps) external onlyOwner { builderFeeBps = bps; }
    function setGasReserveBps(uint16 bps) external onlyOwner { gasReserveBps = bps; }
    function setWithdrawThreshold(uint256 usd6) external onlyOwner { withdrawThresholdUSD6 = usd6; }
    function transferOwnership(address newOwner) external onlyOwner { owner = newOwner; }
}
