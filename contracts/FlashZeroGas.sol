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
}

interface IWETH {
    function deposit() external payable;
    function withdraw(uint256 amount) external;
    function transfer(address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
    function approve(address spender, uint256 amount) external returns (bool);
}

interface IAavePool {
    function flashLoanSimple(address receiverAddress, address asset, uint256 amount,
        bytes calldata params, uint16 referralCode) external;
}

interface IBalancerVault {
    function flashLoan(address recipient, address[] calldata tokens,
        uint256[] calldata amounts, bytes calldata userData) external;
}

interface IUniswapV3Pool {
    function flash(address recipient, uint256 amount0, uint256 amount1, bytes calldata data) external;
    function token0() external view returns (address);
    function token1() external view returns (address);
    function fee()    external view returns (uint24);
}

interface IMorpho {
    function flashLoan(address token, uint256 assets, bytes calldata data) external;
}

interface IUniswapV3Router {
    struct ExactInputSingleParams {
        address tokenIn; address tokenOut; uint24 fee; address recipient;
        uint256 deadline; uint256 amountIn; uint256 amountOutMinimum; uint160 sqrtPriceLimitX96;
    }
    function exactInputSingle(ExactInputSingleParams calldata params) external returns (uint256);
}

interface IUniswapV2Router {
    function swapExactTokensForTokens(uint256 amountIn, uint256 amountOutMin,
        address[] calldata path, address to, uint256 deadline) external returns (uint256[] memory);
}

interface IUniV3Oracle {
    function observe(uint32[] calldata secondsAgos)
        external view returns (int56[] memory tickCumulatives, uint160[] memory);
    function slot0() external view returns (
        uint160 sqrtPriceX96, int24 tick, uint16, uint16, uint16, uint8, bool);
}

contract FlashZeroGas {
    // ── State ────────────────────────────────────────────────────────────
    address public owner;
    address public constant AAVE_POOL_ARB  = 0x794a61358D6845594F94dc1DB02A252b5b4814aD;
    address public constant AAVE_POOL_ETH  = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;
    address public constant BALANCER_VAULT = 0xBA12222222228d8Ba445958a75a0704d566BF2C8;
    address public constant MORPHO_BLUE    = 0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFc;
    address public constant WETH_ARB       = 0x82aF49447D8a07e3bd95BD0d56f35241523fBab1;
    address public constant WETH_ETH       = 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2;
    address public constant UNIV3_ROUTER   = 0xE592427A0AEce92De3Edee1F18E0157C05861564;
    address public constant SUSHI_ROUTER   = 0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506;

    uint256 public gasReserve;
    uint256 public totalProfitRaw;
    uint256 public executionCount;
    uint16  public builderFeeBps   = 500;          // 5%  to block builder (PEG)
    uint16  public gasReserveBps   = 1000;         // 10% to gas reserve
    uint256 public withdrawThresholdUSD6 = 1_000_000_000; // $1000

    event FlashExecuted(address indexed asset, uint256 amount, uint256 profit, string strategy);
    event BuilderPaid(address indexed builder, uint256 amount);
    event GasReserved(uint256 amount);
    event ProfitWithdrawn(address indexed to, uint256 amount);

    modifier onlyOwner() { require(msg.sender == owner, "not owner"); _; }
    constructor() { owner = msg.sender; }
    receive() external payable {}

    // ── Entry Points ─────────────────────────────────────────────────────

    function executeAaveFlash(
        address pool, address asset, uint256 amount,
        address tokenInter, uint24 buyFee, uint24 sellFee, uint8 dexType,
        uint256 minProfit, uint256 builderFee
    ) external onlyOwner {
        IAavePool(pool).flashLoanSimple(address(this), asset, amount,
            abi.encode(tokenInter,buyFee,sellFee,dexType,minProfit,builderFee), 0);
    }

    function executeBalancerFlash(
        address[] calldata tokens, uint256[] calldata amounts,
        address tokenInter, uint24 buyFee, uint24 sellFee, uint8 dexType,
        uint256 minProfit, uint256 builderFee
    ) external onlyOwner {
        IBalancerVault(BALANCER_VAULT).flashLoan(address(this), tokens, amounts,
            abi.encode(tokenInter,buyFee,sellFee,dexType,minProfit,builderFee));
    }

    function executeUniswapFlash(
        address pool, uint256 amount0, uint256 amount1,
        address tokenInter, uint24 buyFee, uint24 sellFee, uint8 dexType,
        uint256 minProfit, uint256 builderFee
    ) external onlyOwner {
        IUniswapV3Pool(pool).flash(address(this), amount0, amount1,
            abi.encode(pool,tokenInter,buyFee,sellFee,dexType,minProfit,builderFee));
    }

    function executeMorphoFlash(
        address token, uint256 assets,
        address tokenInter, uint24 buyFee, uint24 sellFee, uint8 dexType,
        uint256 minProfit, uint256 builderFee
    ) external onlyOwner {
        IMorpho(MORPHO_BLUE).flashLoan(token, assets,
            abi.encode(tokenInter,buyFee,sellFee,dexType,minProfit,builderFee,token,assets));
    }

    // ── Callbacks ────────────────────────────────────────────────────────

    function executeOperation(
        address asset, uint256 amount, uint256 premium,
        address, bytes calldata params
    ) external returns (bool) {
        (address ti, uint24 bf, uint24 sf, uint8 dt, uint256 mp, uint256 bfee) =
            abi.decode(params,(address,uint24,uint24,uint8,uint256,uint256));
        uint256 profit = _arb(asset,amount,ti,bf,sf,dt);
        uint256 repay  = amount+premium;
        require(profit >= mp+repay,"profit<min");
        _peg(bfee, profit-repay);
        IERC20(asset).approve(AAVE_POOL_ARB, repay);
        IERC20(asset).approve(AAVE_POOL_ETH, repay);
        return true;
    }

    function receiveFlashLoan(
        address[] calldata tokens, uint256[] calldata amounts,
        uint256[] calldata feeAmounts, bytes calldata userData
    ) external {
        require(msg.sender==BALANCER_VAULT,"!balancer");
        (address ti,uint24 bf,uint24 sf,uint8 dt,uint256 mp,uint256 bfee) =
            abi.decode(userData,(address,uint24,uint24,uint8,uint256,uint256));
        uint256 profit = _arb(tokens[0],amounts[0],ti,bf,sf,dt);
        uint256 repay  = amounts[0]+feeAmounts[0];
        require(profit >= mp+repay,"profit<min");
        _peg(bfee, profit-repay);
        IERC20(tokens[0]).transfer(BALANCER_VAULT, repay);
        emit FlashExecuted(tokens[0],amounts[0],profit-repay,"BALANCER_0FEE");
    }

    function uniswapV3FlashCallback(uint256 fee0, uint256 fee1, bytes calldata data) external {
        (address pool,address ti,uint24 bf,uint24 sf,uint8 dt,uint256 mp,uint256 bfee) =
            abi.decode(data,(address,address,uint24,uint24,uint8,uint256,uint256));
        require(msg.sender==pool,"!pool");
        address t0 = IUniswapV3Pool(pool).token0();
        address t1 = IUniswapV3Pool(pool).token1();
        uint256 b0 = IERC20(t0).balanceOf(address(this));
        address asset  = b0>0 ? t0 : t1;
        uint256 amount = b0>0 ? b0 : IERC20(t1).balanceOf(address(this));
        uint256 fee    = b0>0 ? fee0 : fee1;
        uint256 profit = _arb(asset,amount,ti,bf,sf,dt);
        require(profit >= mp+amount+fee,"profit<min");
        _peg(bfee, profit-amount-fee);
        IERC20(asset).transfer(pool,amount+fee);
        emit FlashExecuted(asset,amount,profit-amount-fee,"UNIV3_FLASH");
    }

    function onMorphoFlashLoan(uint256 assets, bytes calldata data) external {
        require(msg.sender==MORPHO_BLUE,"!morpho");
        (address ti,uint24 bf,uint24 sf,uint8 dt,uint256 mp,uint256 bfee,address token,uint256 amount) =
            abi.decode(data,(address,uint24,uint24,uint8,uint256,uint256,address,uint256));
        uint256 profit = _arb(token,assets,ti,bf,sf,dt);
        require(profit >= mp+amount,"profit<min");
        _peg(bfee, profit-amount);
        IERC20(token).approve(MORPHO_BLUE,assets);
        emit FlashExecuted(token,assets,profit-amount,"MORPHO_0FEE");
    }

    // ── Novel Strategies ─────────────────────────────────────────────────

    function recursiveFlashStack(
        address wethPool, uint256 wethForGas,
        address arbAsset, uint256 arbAmount,
        address tokenInter, uint24 buyFee, uint24 sellFee,
        uint256 minProfit, uint256 builderFee
    ) external onlyOwner {
        address weth = block.chainid==1 ? WETH_ETH : WETH_ARB;
        bool is0 = IUniswapV3Pool(wethPool).token0()==weth;
        IUniswapV3Pool(wethPool).flash(
            address(this), is0?wethForGas:0, is0?0:wethForGas,
            abi.encode("RFS",arbAsset,arbAmount,tokenInter,buyFee,sellFee,minProfit,builderFee,wethForGas)
        );
    }

    function twapArbitrage(
        address pool, address assetUnder, address assetOver,
        uint256 flashAmount, uint24 buyFee, uint24 sellFee,
        uint256 minProfit, uint256 builderFee
    ) external onlyOwner {
        (,int24 spot,,,,,) = IUniV3Oracle(pool).slot0();
        uint32[] memory secs = new uint32[](2); secs[0]=1800; secs[1]=0;
        (int56[] memory cum,) = IUniV3Oracle(pool).observe(secs);
        int24 twap = int24((cum[1]-cum[0])/1800);
        int24 diff = spot-twap; if(diff<0) diff=-diff;
        require(diff>50,"no TWAP divergence");
        bool is0 = IUniswapV3Pool(pool).token0()==assetUnder;
        IUniswapV3Pool(pool).flash(
            address(this), is0?flashAmount:0, is0?0:flashAmount,
            abi.encode("TWAP",assetOver,flashAmount,buyFee,sellFee,minProfit,builderFee)
        );
    }

    // ── Internal ─────────────────────────────────────────────────────────

    function _arb(
        address assetIn, uint256 amountIn,
        address tokenInter, uint24 buyFee, uint24 sellFee, uint8 dexType
    ) internal returns (uint256 totalOut) {
        IERC20(assetIn).approve(UNIV3_ROUTER, amountIn);
        uint256 mid;
        if (dexType == 0) {
            mid = IUniswapV3Router(UNIV3_ROUTER).exactInputSingle(
                IUniswapV3Router.ExactInputSingleParams(
                    assetIn,tokenInter,buyFee,address(this),block.timestamp,amountIn,0,0));
            IERC20(tokenInter).approve(UNIV3_ROUTER,mid);
            totalOut = IUniswapV3Router(UNIV3_ROUTER).exactInputSingle(
                IUniswapV3Router.ExactInputSingleParams(
                    tokenInter,assetIn,sellFee,address(this),block.timestamp,mid,0,0));
        } else {
            IERC20(assetIn).approve(SUSHI_ROUTER,amountIn);
            address[] memory path=new address[](2); path[0]=assetIn; path[1]=tokenInter;
            uint256[] memory out1=IUniswapV2Router(SUSHI_ROUTER).swapExactTokensForTokens(
                amountIn,0,path,address(this),block.timestamp);
            mid=out1[out1.length-1];
            IERC20(tokenInter).approve(UNIV3_ROUTER,mid);
            totalOut=IUniswapV3Router(UNIV3_ROUTER).exactInputSingle(
                IUniswapV3Router.ExactInputSingleParams(
                    tokenInter,assetIn,sellFee,address(this),block.timestamp,mid,0,0));
        }
    }

    /// @notice PEG — Profit-Embedded Gas: block.coinbase paid from profit.
    ///         Submit tx with gasPrice=0 to Flashbots; builder accepts because
    ///         block.coinbase.transfer(fee) compensates them from inside the tx.
    function _peg(uint256 builderFee, uint256 profitRaw) internal {
        address weth = block.chainid==1 ? WETH_ETH : WETH_ARB;
        if (builderFee>0 && IWETH(weth).balanceOf(address(this))>=builderFee) {
            IWETH(weth).withdraw(builderFee);
            payable(block.coinbase).transfer(builderFee);
            emit BuilderPaid(block.coinbase, builderFee);
        }
        gasReserve    += (profitRaw*gasReserveBps)/10000;
        totalProfitRaw += profitRaw;
        executionCount++;
        emit GasReserved((profitRaw*gasReserveBps)/10000);
    }

    // ── Withdrawal (threshold-gated) ─────────────────────────────────────

    function withdrawToken(address token, uint256 amount, address to) external onlyOwner {
        require(totalProfitRaw>=withdrawThresholdUSD6,"below threshold");
        IERC20(token).transfer(to,amount);
        emit ProfitWithdrawn(to,amount);
    }
    function withdrawETH(uint256 amount, address payable to) external onlyOwner {
        require(totalProfitRaw>=withdrawThresholdUSD6,"below threshold");
        require(address(this).balance>=amount,"low balance");
        to.transfer(amount);
        emit ProfitWithdrawn(to,amount);
    }
    function emergencyWithdrawToken(address token, address to) external onlyOwner {
        IERC20(token).transfer(to,IERC20(token).balanceOf(address(this)));
    }
    function emergencyWithdrawETH(address payable to) external onlyOwner {
        to.transfer(address(this).balance);
    }

    function setBuilderFeeBps(uint16 b) external onlyOwner { builderFeeBps=b; }
    function setGasReserveBps(uint16 b) external onlyOwner { gasReserveBps=b; }
    function setWithdrawThreshold(uint256 v) external onlyOwner { withdrawThresholdUSD6=v; }
    function transferOwnership(address n) external onlyOwner { owner=n; }
}
