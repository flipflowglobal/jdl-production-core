/**
 * JDL DEX Executor — Real Uniswap V3 / PancakeSwap swaps
 * Executes real on-chain token swaps using agent wallet private keys.
 * Falls back to paper trade if wallet is underfunded.
 */

import { ethers } from "ethers";
import { getProvider, KNOWN_TOKENS, CHAIN_DISPLAY } from "./blockchain.js";
import { getTokenPrice } from "./price-feed.js";

// ─── Router addresses per chain ──────────────────────────────────────────────

const SWAP_ROUTER: Record<string, string> = {
  ethereum:  "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45", // Uniswap V3 SwapRouter02
  polygon:   "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
  arbitrum:  "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
  optimism:  "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
  bsc:       "0x13f4EA83D0bd40E75C8222255bc855a974568Dd4", // PancakeSwap V3
  avalanche: "0xbb00FF08d01D300023C629E8fFfFcb65A5a578cE", // Uniswap V3 on AVAX
};

// WETH / native wrapped token per chain
const WETH: Record<string, string> = {
  ethereum:  "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
  polygon:   "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270", // WMATIC
  arbitrum:  "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
  optimism:  "0x4200000000000000000000000000000000000006",
  bsc:       "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c", // WBNB
  avalanche: "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7", // WAVAX
};

// USDC per chain (the "stable" side of every trade)
const USDC: Record<string, string> = {
  ethereum:  "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
  polygon:   "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
  arbitrum:  "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
  optimism:  "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
  bsc:       "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
  avalanche: "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",
};

const ERC20_ABI = [
  "function balanceOf(address) view returns (uint256)",
  "function decimals() view returns (uint8)",
  "function approve(address spender, uint256 amount) returns (bool)",
  "function allowance(address owner, address spender) view returns (uint256)",
];

const SWAP_ROUTER_ABI = [
  `function exactInputSingle(
    (address tokenIn, address tokenOut, uint24 fee, address recipient,
     uint256 amountIn, uint256 amountOutMinimum, uint160 sqrtPriceLimitX96) params
  ) payable returns (uint256 amountOut)`,
];

const MIN_ETH_FOR_GAS = ethers.parseEther("0.002"); // 0.002 ETH minimum for gas
const POOL_FEE = 3000; // 0.3% Uniswap pool fee

export interface SwapResult {
  success: true;
  txHash: string;
  chain: string;
  fromToken: string;
  toToken: string;
  amountIn: number;
  amountOut: number;
  gasUsed: number;
  gasCostEth: string;
}

export interface PaperTradeResult {
  success: false;
  paperTrade: true;
  reason: string;
}

export type ExecutionResult = SwapResult | PaperTradeResult;

/**
 * Execute a real token swap via Uniswap V3 (or chain equivalent).
 * Returns a real txHash on success, or falls back to paper trade.
 */
export async function executeRealSwap(params: {
  privateKey: string;
  chain: string;
  action: "buy" | "sell";   // buy = USDC→WETH, sell = WETH→USDC
  tradeAmountUsd: number;   // USD value to trade
  tokenSymbol: string;      // e.g. "ETH"
  slippageBps?: number;     // default 50 = 0.5%
}): Promise<ExecutionResult> {
  const { privateKey, chain, action, tradeAmountUsd, slippageBps = 50 } = params;

  const routerAddress = SWAP_ROUTER[chain];
  const wethAddress   = WETH[chain];
  const usdcAddress   = USDC[chain];

  if (!routerAddress || !wethAddress || !usdcAddress) {
    return { success: false, paperTrade: true, reason: `Chain ${chain} not configured for DEX swaps` };
  }

  try {
    const provider = getProvider(chain);
    const signer   = new ethers.Wallet(privateKey, provider);

    // ── Check ETH balance for gas ────────────────────────────────────────────
    const ethBalance = await provider.getBalance(signer.address);
    if (ethBalance < MIN_ETH_FOR_GAS) {
      return {
        success: false,
        paperTrade: true,
        reason: `Insufficient ETH for gas. Need 0.002 ETH, wallet has ${ethers.formatEther(ethBalance)} ETH on ${chain}`,
      };
    }

    const router = new ethers.Contract(routerAddress, SWAP_ROUTER_ABI, signer);

    // ── BUY: USDC → WETH ────────────────────────────────────────────────────
    if (action === "buy") {
      const usdcContract = new ethers.Contract(usdcAddress, ERC20_ABI, signer);
      const usdcDecimals = await usdcContract.decimals();
      const usdcBalance  = await usdcContract.balanceOf(signer.address);
      const amountIn     = ethers.parseUnits(tradeAmountUsd.toFixed(6), usdcDecimals);

      if (usdcBalance < amountIn) {
        return {
          success: false,
          paperTrade: true,
          reason: `Insufficient USDC. Need $${tradeAmountUsd}, wallet has ${ethers.formatUnits(usdcBalance, usdcDecimals)} USDC`,
        };
      }

      // Approve router
      const allowance = await usdcContract.allowance(signer.address, routerAddress);
      if (allowance < amountIn) {
        const approveTx = await usdcContract.approve(routerAddress, ethers.MaxUint256);
        await approveTx.wait();
      }

      // Calculate min out with slippage
      const ethPrice    = getTokenPrice("ETH")?.price ?? 2000;
      const expectedOut = (tradeAmountUsd / ethPrice) * 0.999;
      const slippageMul = 1 - (slippageBps / 10000);
      const minOut      = ethers.parseEther((expectedOut * slippageMul).toFixed(18));

      const tx = await router.exactInputSingle({
        tokenIn:            usdcAddress,
        tokenOut:           wethAddress,
        fee:                POOL_FEE,
        recipient:          signer.address,
        amountIn,
        amountOutMinimum:   minOut,
        sqrtPriceLimitX96:  0n,
      });
      const receipt = await tx.wait();

      return {
        success:   true,
        txHash:    receipt.hash,
        chain,
        fromToken: "USDC",
        toToken:   "WETH",
        amountIn:  tradeAmountUsd,
        amountOut: parseFloat(ethers.formatEther(minOut)),
        gasUsed:   Number(receipt.gasUsed),
        gasCostEth: ethers.formatEther(receipt.gasUsed * (receipt.gasPrice ?? 0n)),
      };
    }

    // ── SELL: WETH → USDC ───────────────────────────────────────────────────
    if (action === "sell") {
      const wethContract = new ethers.Contract(wethAddress, ERC20_ABI, signer);
      const wethDecimals = await wethContract.decimals();
      const wethBalance  = await wethContract.balanceOf(signer.address);

      const ethPrice   = getTokenPrice("ETH")?.price ?? 2000;
      const ethAmount  = tradeAmountUsd / ethPrice;
      const amountIn   = ethers.parseEther(ethAmount.toFixed(18));

      if (wethBalance < amountIn) {
        return {
          success: false,
          paperTrade: true,
          reason: `Insufficient WETH. Need ${ethAmount.toFixed(4)} WETH, wallet has ${ethers.formatUnits(wethBalance, wethDecimals)} WETH`,
        };
      }

      const allowance = await wethContract.allowance(signer.address, routerAddress);
      if (allowance < amountIn) {
        const approveTx = await wethContract.approve(routerAddress, ethers.MaxUint256);
        await approveTx.wait();
      }

      const usdcDecimals = 6;
      const slippageMul  = 1 - (slippageBps / 10000);
      const minOut = ethers.parseUnits(
        (tradeAmountUsd * slippageMul).toFixed(6),
        usdcDecimals
      );

      const tx = await router.exactInputSingle({
        tokenIn:           wethAddress,
        tokenOut:          usdcAddress,
        fee:               POOL_FEE,
        recipient:         signer.address,
        amountIn,
        amountOutMinimum:  minOut,
        sqrtPriceLimitX96: 0n,
      });
      const receipt = await tx.wait();

      return {
        success:   true,
        txHash:    receipt.hash,
        chain,
        fromToken: "WETH",
        toToken:   "USDC",
        amountIn:  ethAmount,
        amountOut: tradeAmountUsd,
        gasUsed:   Number(receipt.gasUsed),
        gasCostEth: ethers.formatEther(receipt.gasUsed * (receipt.gasPrice ?? 0n)),
      };
    }

    return { success: false, paperTrade: true, reason: "Unknown action" };

  } catch (err: any) {
    console.error(`[DEXExecutor] Swap failed on ${chain}:`, err?.message);
    // Classify the error
    const msg = err?.message || "";
    if (msg.includes("insufficient funds") || msg.includes("transfer amount exceeds")) {
      return { success: false, paperTrade: true, reason: "Insufficient wallet funds for trade" };
    }
    if (msg.includes("UNPREDICTABLE_GAS_LIMIT") || msg.includes("execution reverted")) {
      return { success: false, paperTrade: true, reason: "Transaction would revert — liquidity or slippage issue" };
    }
    return { success: false, paperTrade: true, reason: `DEX error: ${msg.slice(0, 120)}` };
  }
}

/**
 * Get the explorer URL for a transaction hash on a given chain.
 */
export function getTxExplorerUrl(chain: string, txHash: string): string {
  const display = CHAIN_DISPLAY[chain];
  if (!display) return `https://etherscan.io/tx/${txHash}`;
  return `${display.explorerUrl}/tx/${txHash}`;
}
