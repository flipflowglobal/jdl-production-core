import { ethers } from "ethers";
import { getTokenPrice } from "./price-feed.js";

const ALCHEMY_KEY = process.env.Alchemy_API_Key || "";
const ALCHEMY_ETH = ALCHEMY_KEY ? `https://eth-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}` : "https://eth.drpc.org";
const ALCHEMY_POLYGON = ALCHEMY_KEY ? `https://polygon-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}` : "https://polygon.drpc.org";
const ALCHEMY_ARB = ALCHEMY_KEY ? `https://arb-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}` : "https://arb1.arbitrum.io/rpc";
const ALCHEMY_OPT = ALCHEMY_KEY ? `https://opt-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}` : "https://mainnet.optimism.io";

const RPC_URLS: Record<string, string> = {
  ethereum: process.env.ETH_RPC_URL || ALCHEMY_ETH,
  polygon: process.env.POLYGON_RPC_URL || ALCHEMY_POLYGON,
  arbitrum: process.env.ARB_RPC_URL || ALCHEMY_ARB,
  bsc: process.env.BSC_RPC_URL || "https://bsc-dataseed1.binance.org",
  avalanche: process.env.AVAX_RPC_URL || "https://api.avax.network/ext/bc/C/rpc",
  optimism: process.env.OP_RPC_URL || ALCHEMY_OPT,
};

interface SystemWalletInfo {
  address: string;
  privateKey: string;
  mnemonic: string;
  generatedAt: string;
  isNew: boolean;
}

let _systemWallet: SystemWalletInfo | null = null;

export function initSystemWallet(): SystemWalletInfo {
  if (_systemWallet) return _systemWallet;

  const storedKey = process.env.SYSTEM_WALLET_PRIVATE_KEY;
  if (storedKey) {
    try {
      const wallet = new ethers.Wallet(storedKey);
      _systemWallet = {
        address: wallet.address,
        privateKey: wallet.privateKey,
        mnemonic: process.env.SYSTEM_WALLET_MNEMONIC || "",
        generatedAt: process.env.SYSTEM_WALLET_CREATED_AT || new Date().toISOString(),
        isNew: false,
      };
      return _systemWallet;
    } catch {}
  }

  const wallet = ethers.Wallet.createRandom();
  _systemWallet = {
    address: wallet.address,
    privateKey: wallet.privateKey,
    mnemonic: wallet.mnemonic?.phrase || "",
    generatedAt: new Date().toISOString(),
    isNew: true,
  };
  console.log("[JDL] New system wallet generated:", _systemWallet.address);
  console.log("[JDL] IMPORTANT: Save this private key as SYSTEM_WALLET_PRIVATE_KEY env var to persist across restarts!");
  return _systemWallet;
}

export function getSystemWallet(): SystemWalletInfo {
  return _systemWallet || initSystemWallet();
}

export function getSystemWalletEthers(chain?: string): ethers.Wallet {
  const info = getSystemWallet();
  if (chain) {
    return new ethers.Wallet(info.privateKey, getProvider(chain));
  }
  return new ethers.Wallet(info.privateKey);
}

export function getMainWalletAddress(): string {
  return getSystemWallet().address;
}

export function recoverFromMnemonic(mnemonic: string): { address: string; privateKey: string; mnemonic: string } {
  const wallet = ethers.Wallet.fromPhrase(mnemonic.trim());
  return {
    address: wallet.address,
    privateKey: wallet.privateKey,
    mnemonic: mnemonic.trim(),
  };
}

export function getTestWallet(): ethers.Wallet | null {
  const pk = process.env.Wallet_private_key;
  if (!pk) return null;
  return new ethers.Wallet(pk);
}

export function getTestWalletWithProvider(chain: string): ethers.Wallet | null {
  const pk = process.env.Wallet_private_key;
  if (!pk) return null;
  const provider = getProvider(chain);
  return new ethers.Wallet(pk, provider);
}

const CHAIN_IDS: Record<string, number> = {
  ethereum: 1,
  polygon: 137,
  arbitrum: 42161,
  bsc: 56,
  avalanche: 43114,
  optimism: 10,
};

const providers: Record<string, ethers.JsonRpcProvider> = {};

export function getProvider(chain: string): ethers.JsonRpcProvider {
  if (!providers[chain]) {
    const url = RPC_URLS[chain];
    if (!url) throw new Error(`Unsupported chain: ${chain}`);
    providers[chain] = new ethers.JsonRpcProvider(url, CHAIN_IDS[chain]);
  }
  return providers[chain];
}

export async function checkConnection(chain: string): Promise<{
  connected: boolean;
  blockNumber?: number;
  chainId?: number;
  latency?: number;
  error?: string;
}> {
  try {
    const provider = getProvider(chain);
    const start = Date.now();
    const [blockNumber, network] = await Promise.all([
      provider.getBlockNumber(),
      provider.getNetwork(),
    ]);
    const latency = Date.now() - start;
    return {
      connected: true,
      blockNumber,
      chainId: Number(network.chainId),
      latency,
    };
  } catch (err: any) {
    return { connected: false, error: err.message };
  }
}

export async function checkAllConnections(): Promise<
  Record<string, { connected: boolean; blockNumber?: number; chainId?: number; latency?: number; error?: string }>
> {
  const results: Record<string, any> = {};
  const chains = Object.keys(RPC_URLS);
  await Promise.all(
    chains.map(async (chain) => {
      results[chain] = await checkConnection(chain);
    })
  );
  return results;
}

export function createWallet(): {
  address: string;
  privateKey: string;
  mnemonic: string;
} {
  const wallet = ethers.Wallet.createRandom();
  return {
    address: wallet.address,
    privateKey: wallet.privateKey,
    mnemonic: wallet.mnemonic?.phrase || "",
  };
}

export function importWallet(privateKey: string): { address: string } {
  const wallet = new ethers.Wallet(privateKey);
  return { address: wallet.address };
}

export async function getBalance(
  chain: string,
  address: string
): Promise<{ balance: string; balanceEth: string }> {
  const provider = getProvider(chain);
  const balance = await provider.getBalance(address);
  return {
    balance: balance.toString(),
    balanceEth: ethers.formatEther(balance),
  };
}

export async function getTokenBalance(
  chain: string,
  tokenAddress: string,
  walletAddress: string
): Promise<{ balance: string; formatted: string; decimals: number; symbol: string }> {
  const provider = getProvider(chain);
  const abi = [
    "function balanceOf(address) view returns (uint256)",
    "function decimals() view returns (uint8)",
    "function symbol() view returns (string)",
  ];
  const contract = new ethers.Contract(tokenAddress, abi, provider);
  const [balance, decimals, symbol] = await Promise.all([
    contract.balanceOf(walletAddress),
    contract.decimals(),
    contract.symbol(),
  ]);
  return {
    balance: balance.toString(),
    formatted: ethers.formatUnits(balance, decimals),
    decimals: Number(decimals),
    symbol,
  };
}

export async function sendTransaction(
  chain: string,
  privateKey: string,
  to: string,
  amountEth: string
): Promise<{
  hash: string;
  from: string;
  to: string;
  value: string;
  gasUsed?: string;
  status?: string;
}> {
  const provider = getProvider(chain);
  const wallet = new ethers.Wallet(privateKey, provider);
  const tx = await wallet.sendTransaction({
    to,
    value: ethers.parseEther(amountEth),
  });
  const receipt = await tx.wait();
  return {
    hash: tx.hash,
    from: tx.from,
    to: tx.to || to,
    value: amountEth,
    gasUsed: receipt?.gasUsed?.toString(),
    status: receipt?.status === 1 ? "confirmed" : "failed",
  };
}

export async function sendTokenTransaction(
  chain: string,
  privateKey: string,
  tokenAddress: string,
  to: string,
  amount: string,
  decimals: number
): Promise<{
  hash: string;
  from: string;
  to: string;
  tokenAddress: string;
  amount: string;
  status?: string;
}> {
  const provider = getProvider(chain);
  const wallet = new ethers.Wallet(privateKey, provider);
  const abi = ["function transfer(address to, uint256 amount) returns (bool)"];
  const contract = new ethers.Contract(tokenAddress, abi, wallet);
  const parsedAmount = ethers.parseUnits(amount, decimals);
  const tx = await contract.transfer(to, parsedAmount);
  const receipt = await tx.wait();
  return {
    hash: tx.hash,
    from: wallet.address,
    to,
    tokenAddress,
    amount,
    status: receipt?.status === 1 ? "confirmed" : "failed",
  };
}

export async function estimateGas(
  chain: string,
  from: string,
  to: string,
  amountEth: string
): Promise<{ gasEstimate: string; gasPriceGwei: string; totalCostEth: string }> {
  const provider = getProvider(chain);
  const gasEstimate = await provider.estimateGas({
    from,
    to,
    value: ethers.parseEther(amountEth),
  });
  const feeData = await provider.getFeeData();
  const gasPrice = feeData.gasPrice || 0n;
  const totalCost = gasEstimate * gasPrice;
  return {
    gasEstimate: gasEstimate.toString(),
    gasPriceGwei: ethers.formatUnits(gasPrice, "gwei"),
    totalCostEth: ethers.formatEther(totalCost),
  };
}

export async function getTransactionStatus(
  chain: string,
  txHash: string
): Promise<{
  found: boolean;
  status?: string;
  blockNumber?: number;
  confirmations?: number;
  from?: string;
  to?: string;
  value?: string;
}> {
  const provider = getProvider(chain);
  const receipt = await provider.getTransactionReceipt(txHash);
  if (!receipt) {
    const tx = await provider.getTransaction(txHash);
    if (!tx) return { found: false };
    return {
      found: true,
      status: "pending",
      from: tx.from,
      to: tx.to || undefined,
      value: ethers.formatEther(tx.value),
    };
  }
  return {
    found: true,
    status: receipt.status === 1 ? "confirmed" : "failed",
    blockNumber: receipt.blockNumber,
    from: receipt.from,
    to: receipt.to || undefined,
  };
}

export const KNOWN_TOKENS: Record<string, { address: string; symbol: string; decimals: number }[]> = {
  ethereum: [
    { address: "0xdAC17F958D2ee523a2206206994597C13D831ec7", symbol: "USDT", decimals: 6 },
    { address: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", symbol: "USDC", decimals: 6 },
    { address: "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", symbol: "WETH", decimals: 18 },
    { address: "0x6B175474E89094C44Da98b954EedeAC495271d0F", symbol: "DAI", decimals: 18 },
    { address: "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", symbol: "WBTC", decimals: 8 },
    { address: "0x514910771AF9Ca656af840dff83E8264EcF986CA", symbol: "LINK", decimals: 18 },
  ],
  polygon: [
    { address: "0xc2132D05D31c914a87C6611C10748AEb04B58e8F", symbol: "USDT", decimals: 6 },
    { address: "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", symbol: "USDC", decimals: 6 },
    { address: "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619", symbol: "WETH", decimals: 18 },
  ],
  arbitrum: [
    { address: "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", symbol: "USDT", decimals: 6 },
    { address: "0xaf88d065e77c8cC2239327C5EDb3A432268e5831", symbol: "USDC", decimals: 6 },
    { address: "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", symbol: "WETH", decimals: 18 },
  ],
  bsc: [
    { address: "0x55d398326f99059fF775485246999027B3197955", symbol: "USDT", decimals: 18 },
    { address: "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", symbol: "USDC", decimals: 18 },
    { address: "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56", symbol: "BUSD", decimals: 18 },
  ],
  avalanche: [
    { address: "0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7", symbol: "USDT", decimals: 6 },
    { address: "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E", symbol: "USDC", decimals: 6 },
  ],
  optimism: [
    { address: "0x94b008aA00579c1307B0EF2c499aD98a8ce58e58", symbol: "USDT", decimals: 6 },
    { address: "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85", symbol: "USDC", decimals: 6 },
    { address: "0x4200000000000000000000000000000000000006", symbol: "WETH", decimals: 18 },
  ],
};

export const CHAIN_DISPLAY: Record<string, { name: string; nativeSymbol: string; explorerUrl: string; chainId: number }> = {
  ethereum: { name: "Ethereum Mainnet", nativeSymbol: "ETH", explorerUrl: "https://etherscan.io", chainId: 1 },
  polygon: { name: "Polygon PoS", nativeSymbol: "MATIC", explorerUrl: "https://polygonscan.com", chainId: 137 },
  arbitrum: { name: "Arbitrum One", nativeSymbol: "ETH", explorerUrl: "https://arbiscan.io", chainId: 42161 },
  bsc: { name: "BNB Smart Chain", nativeSymbol: "BNB", explorerUrl: "https://bscscan.com", chainId: 56 },
  avalanche: { name: "Avalanche C-Chain", nativeSymbol: "AVAX", explorerUrl: "https://snowtrace.io", chainId: 43114 },
  optimism: { name: "Optimism", nativeSymbol: "ETH", explorerUrl: "https://optimistic.etherscan.io", chainId: 10 },
};

export async function getPortfolio(address: string): Promise<{
  address: string;
  chains: Record<string, {
    connected: boolean;
    nativeBalance: string;
    nativeSymbol: string;
    nativeBalanceUsd: number;
    chainName: string;
    chainId: number;
    explorerUrl: string;
    tokens: { symbol: string; balance: string; address: string; decimals: number }[];
    error?: string;
  }>;
}> {
  const FALLBACK_PRICES: Record<string, number> = {
    ETH: 1820, MATIC: 0.58, BNB: 578, AVAX: 28,
  };

  const chains: Record<string, any> = {};
  const chainKeys = Object.keys(RPC_URLS);

  await Promise.all(
    chainKeys.map(async (chain) => {
      const display = CHAIN_DISPLAY[chain];
      try {
        const provider = getProvider(chain);
        const bal = await provider.getBalance(address);
        const balEth = ethers.formatEther(bal);
        const symbol = display.nativeSymbol;
        const livePrice = getTokenPrice(symbol);
        const nativeUsd = livePrice?.price ?? FALLBACK_PRICES[symbol] ?? 0;
        const usdVal = parseFloat(balEth) * nativeUsd;

        const tokens: { symbol: string; balance: string; address: string; decimals: number }[] = [];
        const knownList = KNOWN_TOKENS[chain] || [];
        await Promise.all(
          knownList.map(async (tk) => {
            try {
              const abi = ["function balanceOf(address) view returns (uint256)"];
              const contract = new ethers.Contract(tk.address, abi, provider);
              const rawBal = await contract.balanceOf(address);
              const formatted = ethers.formatUnits(rawBal, tk.decimals);
              if (parseFloat(formatted) > 0) {
                tokens.push({ symbol: tk.symbol, balance: formatted, address: tk.address, decimals: tk.decimals });
              }
            } catch {}
          })
        );

        chains[chain] = {
          connected: true,
          nativeBalance: balEth,
          nativeSymbol: display.nativeSymbol,
          nativeBalanceUsd: usdVal,
          chainName: display.name,
          chainId: display.chainId,
          explorerUrl: display.explorerUrl,
          tokens,
        };
      } catch (err: any) {
        chains[chain] = {
          connected: false,
          nativeBalance: "0",
          nativeSymbol: display.nativeSymbol,
          nativeBalanceUsd: 0,
          chainName: display.name,
          chainId: display.chainId,
          explorerUrl: display.explorerUrl,
          tokens: [],
          error: err.message,
        };
      }
    })
  );

  return { address, chains };
}

const AAVE_V3_POOL: Record<string, string> = {
  ethereum: "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
  polygon: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
  arbitrum: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
  avalanche: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
  optimism: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
};

export async function checkFlashLoanAvailability(
  chain: string,
  tokenAddress: string
): Promise<{
  available: boolean;
  maxAmount?: string;
  premium?: string;
  error?: string;
}> {
  try {
    const provider = getProvider(chain);
    const poolAddress = AAVE_V3_POOL[chain];
    if (!poolAddress) return { available: false, error: "Chain not supported for flash loans" };

    const abi = [
      "function getReserveData(address asset) view returns (tuple(uint256,uint128,uint128,uint128,uint128,uint128,uint40,uint16,address,address,address,address,uint128,uint128,uint128))",
      "function FLASHLOAN_PREMIUM_TOTAL() view returns (uint128)",
    ];
    const pool = new ethers.Contract(poolAddress, abi, provider);
    const premium = await pool.FLASHLOAN_PREMIUM_TOTAL();

    const tokenAbi = [
      "function balanceOf(address) view returns (uint256)",
      "function decimals() view returns (uint8)",
    ];
    const token = new ethers.Contract(tokenAddress, tokenAbi, provider);
    const reserveData = await pool.getReserveData(tokenAddress);
    const aTokenAddress = reserveData[8];
    const aToken = new ethers.Contract(aTokenAddress, tokenAbi, provider);
    const availableLiquidity = await aToken.balanceOf(poolAddress);
    const decimals = await token.decimals();

    return {
      available: true,
      maxAmount: ethers.formatUnits(availableLiquidity, decimals),
      premium: (Number(premium) / 10000 * 100).toFixed(2) + "%",
    };
  } catch (err: any) {
    return { available: false, error: err.message };
  }
}

const UNISWAP_V3_QUOTER: Record<string, string> = {
  ethereum: "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6",
  polygon: "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6",
  arbitrum: "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6",
  optimism: "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6",
};

export async function getSwapQuote(
  chain: string,
  tokenIn: string,
  tokenOut: string,
  amountIn: string,
  decimalsIn: number
): Promise<{
  amountOut: string;
  price: string;
  error?: string;
}> {
  try {
    const provider = getProvider(chain);
    const quoterAddress = UNISWAP_V3_QUOTER[chain];
    if (!quoterAddress) return { amountOut: "0", price: "0", error: "Chain not supported" };

    const abi = [
      "function quoteExactInputSingle(address tokenIn, address tokenOut, uint24 fee, uint256 amountIn, uint160 sqrtPriceLimitX96) view returns (uint256 amountOut)",
    ];
    const quoter = new ethers.Contract(quoterAddress, abi, provider);
    const parsedAmount = ethers.parseUnits(amountIn, decimalsIn);
    const amountOut = await quoter.quoteExactInputSingle(
      tokenIn,
      tokenOut,
      3000,
      parsedAmount,
      0
    );

    const tokenOutAbi = ["function decimals() view returns (uint8)"];
    const tokenOutContract = new ethers.Contract(tokenOut, tokenOutAbi, provider);
    const decimalsOut = await tokenOutContract.decimals();
    const formattedOut = ethers.formatUnits(amountOut, decimalsOut);
    const price = (Number(formattedOut) / Number(amountIn)).toFixed(6);

    return { amountOut: formattedOut, price };
  } catch (err: any) {
    return { amountOut: "0", price: "0", error: err.message };
  }
}

export async function getGasPrice(chain: string): Promise<{
  gasPriceGwei: string;
  maxFeePerGasGwei?: string;
  maxPriorityFeePerGasGwei?: string;
}> {
  const provider = getProvider(chain);
  const feeData = await provider.getFeeData();
  return {
    gasPriceGwei: ethers.formatUnits(feeData.gasPrice || 0n, "gwei"),
    maxFeePerGasGwei: feeData.maxFeePerGas
      ? ethers.formatUnits(feeData.maxFeePerGas, "gwei")
      : undefined,
    maxPriorityFeePerGasGwei: feeData.maxPriorityFeePerGas
      ? ethers.formatUnits(feeData.maxPriorityFeePerGas, "gwei")
      : undefined,
  };
}
