import { Router } from "express";
import * as blockchain from "../services/blockchain.js";
import { calculateFee, recordFee, calculateFundingFee, recordFundingFee } from "../services/system-fees.js";

const router = Router();

router.get("/system-wallet", async (_req, res) => {
  try {
    const info = blockchain.getSystemWallet();
    const address = info.address;
    const chains = ["ethereum", "polygon", "arbitrum", "bsc", "avalanche", "optimism"];
    const balances: Record<string, any> = {};
    await Promise.all(
      chains.map(async (chain) => {
        try {
          const bal = await blockchain.getBalance(chain, address);
          balances[chain] = { ...bal, connected: true };
        } catch (err: any) {
          balances[chain] = { connected: false, error: err.message };
        }
      })
    );
    res.json({
      success: true,
      address: info.address,
      privateKey: info.privateKey,
      mnemonic: info.mnemonic,
      generatedAt: info.generatedAt,
      isNew: info.isNew,
      balances,
    });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

router.get("/main-wallet", async (_req, res) => {
  try {
    const address = blockchain.getMainWalletAddress();
    const chains = ["ethereum", "polygon", "arbitrum", "bsc", "avalanche", "optimism"];
    const balances: Record<string, any> = {};
    await Promise.all(
      chains.map(async (chain) => {
        try {
          const bal = await blockchain.getBalance(chain, address);
          balances[chain] = { ...bal, connected: true };
        } catch (err: any) {
          balances[chain] = { connected: false, error: err.message };
        }
      })
    );
    res.json({ success: true, address, balances });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

router.get("/test-wallet", async (_req, res) => {
  try {
    const wallet = blockchain.getTestWallet();
    if (!wallet) {
      return res.status(400).json({ success: false, error: "Test wallet private key not configured" });
    }
    const address = wallet.address;
    const chains = ["ethereum", "polygon", "arbitrum", "bsc"];
    const balances: Record<string, any> = {};
    await Promise.all(
      chains.map(async (chain) => {
        try {
          const bal = await blockchain.getBalance(chain, address);
          balances[chain] = { ...bal, connected: true };
        } catch (err: any) {
          balances[chain] = { connected: false, error: err.message };
        }
      })
    );
    res.json({ success: true, address, balances });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

router.post("/test-wallet/send", async (req, res) => {
  try {
    const { chain, to, amount } = req.body;
    if (!chain || !to || !amount) {
      return res.status(400).json({ success: false, error: "chain, to, and amount are required" });
    }
    const parsedAmt = parseFloat(amount);
    if (isNaN(parsedAmt) || parsedAmt <= 0) {
      return res.status(400).json({ success: false, error: "amount must be a positive number" });
    }
    const wallet = blockchain.getTestWalletWithProvider(chain);
    if (!wallet) {
      return res.status(400).json({ success: false, error: "Test wallet private key not configured" });
    }
    const { feeAmount, netAmount } = calculateFee(parsedAmt);
    const mainWallet = blockchain.getMainWalletAddress();

    const result = await blockchain.sendTransaction(chain, wallet.privateKey, to, netAmount.toString());

    let feeCollected = false;
    let feeTxHash: string | null = null;
    try {
      const feeTx = await blockchain.sendTransaction(chain, wallet.privateKey, mainWallet, feeAmount.toString());
      feeCollected = true;
      feeTxHash = feeTx.hash || null;
    } catch (_feeErr) {
      feeCollected = false;
    }

    const feeRecord = recordFee({
      txHash: result.hash || "",
      fromWallet: wallet.address,
      toWallet: to,
      originalAmount: parseFloat(amount),
      chain,
      token: "ETH",
      type: "user-send",
      userId: "user-001",
    });

    res.json({
      success: true,
      transaction: result,
      systemFee: {
        rate: "0.75%",
        feeAmount: feeRecord.feeAmount,
        netAmountSent: feeRecord.netAmount,
        feeDestination: mainWallet,
        feeCollected,
        feeTxHash: feeCollected ? feeTxHash : null,
      },
    });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

router.post("/test-wallet/send-token", async (req, res) => {
  try {
    const { chain, tokenAddress, to, amount, decimals } = req.body;
    if (!chain || !tokenAddress || !to || !amount) {
      return res.status(400).json({ success: false, error: "chain, tokenAddress, to, amount required" });
    }
    const parsedAmt = parseFloat(amount);
    if (isNaN(parsedAmt) || parsedAmt <= 0) {
      return res.status(400).json({ success: false, error: "amount must be a positive number" });
    }
    const wallet = blockchain.getTestWalletWithProvider(chain);
    if (!wallet) {
      return res.status(400).json({ success: false, error: "Test wallet private key not configured" });
    }
    const { feeAmount, netAmount } = calculateFee(parsedAmt);
    const mainWallet = blockchain.getMainWalletAddress();

    const result = await blockchain.sendTokenTransaction(
      chain,
      wallet.privateKey,
      tokenAddress,
      to,
      netAmount.toString(),
      decimals || 18
    );

    let feeCollected = false;
    let feeTxHash: string | null = null;
    try {
      const feeTx = await blockchain.sendTokenTransaction(chain, wallet.privateKey, tokenAddress, mainWallet, feeAmount.toString(), decimals || 18);
      feeCollected = true;
      feeTxHash = feeTx.hash || null;
    } catch (_feeErr) {
      feeCollected = false;
    }

    const tokenSymbol = (blockchain.KNOWN_TOKENS[chain] || []).find(t => t.address.toLowerCase() === tokenAddress.toLowerCase())?.symbol || "TOKEN";

    const feeRecord = recordFee({
      txHash: result.hash || "",
      fromWallet: wallet.address,
      toWallet: to,
      originalAmount: parseFloat(amount),
      chain,
      token: tokenSymbol,
      type: "user-send",
      userId: "user-001",
    });

    res.json({
      success: true,
      transaction: result,
      systemFee: {
        rate: "0.75%",
        feeAmount: feeRecord.feeAmount,
        netAmountSent: feeRecord.netAmount,
        feeDestination: mainWallet,
        feeCollected,
        feeTxHash: feeCollected ? feeTxHash : null,
      },
    });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

router.get("/connections", async (_req, res) => {
  try {
    const connections = await blockchain.checkAllConnections();
    res.json({ success: true, connections });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

router.get("/connections/:chain", async (req, res) => {
  try {
    const result = await blockchain.checkConnection(req.params.chain);
    res.json({ success: true, ...result });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

router.post("/wallet/create", async (_req, res) => {
  try {
    const wallet = blockchain.createWallet();
    res.json({ success: true, wallet });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

router.post("/wallet/import", async (req, res) => {
  try {
    const { privateKey } = req.body;
    if (!privateKey) return res.status(400).json({ success: false, error: "privateKey required" });
    const wallet = blockchain.importWallet(privateKey);
    res.json({ success: true, wallet });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

router.post("/wallet/recover", async (req, res) => {
  try {
    const { mnemonic } = req.body;
    if (!mnemonic) return res.status(400).json({ success: false, error: "mnemonic phrase required" });
    const words = mnemonic.trim().split(/\s+/);
    if (words.length !== 12 && words.length !== 24) {
      return res.status(400).json({ success: false, error: "Mnemonic must be 12 or 24 words" });
    }
    const wallet = blockchain.recoverFromMnemonic(mnemonic);
    res.json({ success: true, wallet });
  } catch (err: any) {
    res.status(500).json({ success: false, error: "Invalid mnemonic phrase: " + err.message });
  }
});

router.get("/balance/:chain/:address", async (req, res) => {
  try {
    const { chain, address } = req.params;
    const balance = await blockchain.getBalance(chain, address);
    res.json({ success: true, chain, address, ...balance });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

router.get("/token-balance/:chain/:tokenAddress/:walletAddress", async (req, res) => {
  try {
    const { chain, tokenAddress, walletAddress } = req.params;
    const balance = await blockchain.getTokenBalance(chain, tokenAddress, walletAddress);
    res.json({ success: true, chain, ...balance });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

router.post("/transfer", async (req, res) => {
  try {
    const { chain, privateKey, to, amount } = req.body;
    if (!chain || !privateKey || !to || !amount) {
      return res.status(400).json({ success: false, error: "chain, privateKey, to, and amount are required" });
    }
    const parsedAmt = parseFloat(amount);
    if (isNaN(parsedAmt) || parsedAmt <= 0) {
      return res.status(400).json({ success: false, error: "amount must be a positive number" });
    }
    const { feeAmount, netAmount } = calculateFee(parsedAmt);
    const mainWallet = blockchain.getMainWalletAddress();

    const result = await blockchain.sendTransaction(chain, privateKey, to, netAmount.toString());

    let feeCollected = false;
    let feeTxHash: string | null = null;
    try {
      const feeTx = await blockchain.sendTransaction(chain, privateKey, mainWallet, feeAmount.toString());
      feeCollected = true;
      feeTxHash = feeTx.hash || null;
    } catch (_feeErr) {
      feeCollected = false;
    }

    const feeRecord = recordFee({
      txHash: result.hash || "",
      fromWallet: result.from || "",
      toWallet: to,
      originalAmount: parseFloat(amount),
      chain,
      token: "ETH",
      type: "wallet-transfer",
      userId: "user-001",
    });

    res.json({
      success: true,
      transaction: result,
      systemFee: {
        rate: "0.75%",
        feeAmount: feeRecord.feeAmount,
        netAmountSent: feeRecord.netAmount,
        feeDestination: mainWallet,
        feeCollected,
        feeTxHash: feeCollected ? feeTxHash : null,
      },
    });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

router.post("/transfer-token", async (req, res) => {
  try {
    const { chain, privateKey, tokenAddress, to, amount, decimals } = req.body;
    if (!chain || !privateKey || !tokenAddress || !to || !amount) {
      return res.status(400).json({ success: false, error: "chain, privateKey, tokenAddress, to, amount required" });
    }
    const parsedAmt = parseFloat(amount);
    if (isNaN(parsedAmt) || parsedAmt <= 0) {
      return res.status(400).json({ success: false, error: "amount must be a positive number" });
    }
    const { feeAmount, netAmount } = calculateFee(parsedAmt);
    const mainWallet = blockchain.getMainWalletAddress();

    const result = await blockchain.sendTokenTransaction(
      chain,
      privateKey,
      tokenAddress,
      to,
      netAmount.toString(),
      decimals || 18
    );

    let feeCollected = false;
    let feeTxHash: string | null = null;
    try {
      const feeTx = await blockchain.sendTokenTransaction(chain, privateKey, tokenAddress, mainWallet, feeAmount.toString(), decimals || 18);
      feeCollected = true;
      feeTxHash = feeTx.hash || null;
    } catch (_feeErr) {
      feeCollected = false;
    }

    const tokenSymbol = (blockchain.KNOWN_TOKENS[chain] || []).find(t => t.address.toLowerCase() === tokenAddress.toLowerCase())?.symbol || "TOKEN";
    const wallet = blockchain.importWallet(privateKey);

    const feeRecord = recordFee({
      txHash: result.hash || "",
      fromWallet: wallet.address,
      toWallet: to,
      originalAmount: parseFloat(amount),
      chain,
      token: tokenSymbol,
      type: "wallet-transfer",
      userId: "user-001",
    });

    res.json({
      success: true,
      transaction: result,
      systemFee: {
        rate: "0.75%",
        feeAmount: feeRecord.feeAmount,
        netAmountSent: feeRecord.netAmount,
        feeDestination: mainWallet,
        feeCollected,
        feeTxHash: feeCollected ? feeTxHash : null,
      },
    });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

router.post("/estimate-gas", async (req, res) => {
  try {
    const { chain, from, to, amount } = req.body;
    if (!chain || !from || !to || !amount) {
      return res.status(400).json({ success: false, error: "chain, from, to, amount required" });
    }
    const estimate = await blockchain.estimateGas(chain, from, to, amount);
    res.json({ success: true, ...estimate });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

router.get("/gas-price/:chain", async (req, res) => {
  try {
    const result = await blockchain.getGasPrice(req.params.chain);
    res.json({ success: true, chain: req.params.chain, ...result });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

router.get("/tx/:chain/:txHash", async (req, res) => {
  try {
    const { chain, txHash } = req.params;
    const result = await blockchain.getTransactionStatus(chain, txHash);
    res.json({ success: true, ...result });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

router.get("/flash-loan/check/:chain/:tokenAddress", async (req, res) => {
  try {
    const { chain, tokenAddress } = req.params;
    const result = await blockchain.checkFlashLoanAvailability(chain, tokenAddress);
    res.json({ success: true, chain, tokenAddress, ...result });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

router.get("/swap-quote/:chain", async (req, res) => {
  try {
    const { tokenIn, tokenOut, amountIn, decimalsIn } = req.query;
    if (!tokenIn || !tokenOut || !amountIn) {
      return res.status(400).json({ success: false, error: "tokenIn, tokenOut, amountIn required" });
    }
    const result = await blockchain.getSwapQuote(
      req.params.chain,
      tokenIn as string,
      tokenOut as string,
      amountIn as string,
      Number(decimalsIn) || 18
    );
    res.json({ success: true, chain: req.params.chain, ...result });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

router.get("/portfolio/:address", async (req, res) => {
  try {
    const { address } = req.params;
    const portfolio = await blockchain.getPortfolio(address);
    res.json({ success: true, ...portfolio });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

router.post("/fund", async (req, res) => {
  try {
    const { chain, toWallet, amount, token, fromExchange } = req.body;
    if (!chain || !toWallet || !amount) {
      return res.status(400).json({ success: false, error: "chain, toWallet, and amount are required" });
    }
    const parsedAmount = parseFloat(amount);
    if (isNaN(parsedAmount) || parsedAmount <= 0) {
      return res.status(400).json({ success: false, error: "amount must be a positive number" });
    }
    const { feeAmount, netAmount } = calculateFundingFee(parsedAmount);
    const mainWallet = blockchain.getMainWalletAddress();

    const depositId = `dep_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;

    const feeRecord = recordFundingFee({
      txHash: depositId,
      fromWallet: fromExchange || "external-deposit",
      toWallet,
      originalAmount: parseFloat(amount),
      chain,
      token: token || blockchain.CHAIN_DISPLAY[chain]?.nativeSymbol || "ETH",
      userId: "user-001",
    });

    res.json({
      success: true,
      type: "deposit-record",
      deposit: {
        depositId,
        toWallet,
        chain,
        originalAmount: parseFloat(amount),
        netAmountCredited: netAmount,
        token: token || blockchain.CHAIN_DISPLAY[chain]?.nativeSymbol || "ETH",
        source: fromExchange || "external-deposit",
        status: "pending-confirmation",
      },
      fundingFee: {
        rate: "2%",
        feeAmount: feeRecord.feeAmount,
        feeDestination: mainWallet,
        feeCollected: false,
        note: "Fee collected on-chain upon deposit confirmation",
      },
    });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

router.get("/exchanges", (_req, res) => {
  res.json({
    success: true,
    exchanges: [
      { name: "Coinbase", url: "https://www.coinbase.com", icon: "CB", description: "Most trusted US exchange", supportedChains: ["ethereum", "polygon", "arbitrum", "avalanche", "optimism"] },
      { name: "Binance", url: "https://www.binance.com", icon: "BN", description: "Largest global exchange", supportedChains: ["ethereum", "polygon", "arbitrum", "bsc", "avalanche", "optimism"] },
      { name: "Kraken", url: "https://www.kraken.com", icon: "KR", description: "Established & secure", supportedChains: ["ethereum", "polygon", "arbitrum"] },
      { name: "Bybit", url: "https://www.bybit.com", icon: "BB", description: "Derivatives & spot trading", supportedChains: ["ethereum", "polygon", "arbitrum", "bsc", "avalanche", "optimism"] },
      { name: "OKX", url: "https://www.okx.com", icon: "OK", description: "Web3 native exchange", supportedChains: ["ethereum", "polygon", "arbitrum", "bsc", "avalanche", "optimism"] },
      { name: "Crypto.com", url: "https://crypto.com", icon: "CR", description: "Multi-asset platform", supportedChains: ["ethereum", "polygon", "arbitrum", "avalanche"] },
    ],
  });
});

router.get("/chain-info", (_req, res) => {
  res.json({
    success: true,
    chains: blockchain.CHAIN_DISPLAY,
    tokens: blockchain.KNOWN_TOKENS,
  });
});

const SYSTEM_FEE_WALLET = "0x8C117222E14DcAA20fE3087C491b1d330D0F625a";
const SUPPORTED_FUND_FROM_WALLET_CHAINS = ["ethereum", "polygon", "bsc", "avalanche"];

router.post("/fund-from-wallet", async (req, res) => {
  const { sourcePrivateKey, sourceAddress, toAddress, amount, chain } = req.body;

  if (!sourcePrivateKey || !toAddress || !amount || !chain) {
    return res.status(400).json({ success: false, error: "sourcePrivateKey, toAddress, amount, and chain are required" });
  }

  if (!SUPPORTED_FUND_FROM_WALLET_CHAINS.includes(chain)) {
    return res.status(400).json({ success: false, error: `Chain '${chain}' is not supported. Supported: ${SUPPORTED_FUND_FROM_WALLET_CHAINS.join(", ")}` });
  }

  const parsedAmount = parseFloat(amount);
  if (isNaN(parsedAmount) || parsedAmount <= 0) {
    return res.status(400).json({ success: false, error: "amount must be a positive number" });
  }

  let derivedWallet: { address: string } | null = null;
  try {
    derivedWallet = blockchain.importWallet(sourcePrivateKey);
  } catch {
    return res.status(400).json({ success: false, error: "Invalid private key. Could not derive wallet address." });
  }

  if (sourceAddress && sourceAddress.toLowerCase() !== derivedWallet.address.toLowerCase()) {
    return res.status(400).json({
      success: false,
      error: `Private key does not match the provided source address. Key derives to ${derivedWallet.address}, but ${sourceAddress} was provided.`,
    });
  }

  const { feeAmount, netAmount } = calculateFundingFee(parsedAmount);
  const nativeSymbol = blockchain.CHAIN_DISPLAY[chain]?.nativeSymbol || "ETH";

  try {
    const { ethers } = await import("ethers");
    const provider = blockchain.getProvider(chain);
    const balanceWei = await provider.getBalance(derivedWallet.address);
    const balanceEth = parseFloat(ethers.formatEther(balanceWei));

    const feeData = await provider.getFeeData();
    const effectiveGasPrice = feeData.maxFeePerGas || feeData.gasPrice || 0n;

    const [gasForFee, gasForMain] = await Promise.all([
      provider.estimateGas({ from: derivedWallet.address, to: SYSTEM_FEE_WALLET, value: ethers.parseEther(feeAmount.toString()) }).catch(() => 21000n),
      provider.estimateGas({ from: derivedWallet.address, to: toAddress, value: ethers.parseEther(netAmount.toString()) }).catch(() => 21000n),
    ]);

    const totalGasWei = (gasForFee + gasForMain) * effectiveGasPrice;
    const totalGasEth = parseFloat(ethers.formatEther(totalGasWei));
    const totalRequired = parsedAmount + totalGasEth;

    if (balanceEth < totalRequired) {
      return res.status(400).json({
        success: false,
        error: `Insufficient balance. Source wallet has ${balanceEth.toFixed(6)} ${nativeSymbol} but needs at least ${totalRequired.toFixed(6)} ${nativeSymbol} (${parsedAmount} amount + ~${totalGasEth.toFixed(6)} estimated gas for both transactions).`,
      });
    }

    const feeTx = await blockchain.sendTransaction(chain, sourcePrivateKey, SYSTEM_FEE_WALLET, feeAmount.toString());
    const mainTx = await blockchain.sendTransaction(chain, sourcePrivateKey, toAddress, netAmount.toString());

    recordFundingFee({
      txHash: mainTx.hash || "",
      fromWallet: derivedWallet.address,
      toWallet: toAddress,
      originalAmount: parsedAmount,
      chain,
      token: nativeSymbol,
      userId: "user-001",
    });

    return res.json({
      success: true,
      txHash: mainTx.hash,
      from: derivedWallet.address,
      to: toAddress,
      chain,
      originalAmount: parsedAmount,
      netAmountCredited: netAmount,
      token: nativeSymbol,
      fundingFee: {
        rate: "2%",
        feeAmount,
        feeDestination: SYSTEM_FEE_WALLET,
        feeCollected: true,
        feeTxHash: feeTx.hash || null,
      },
    });
  } catch (err: any) {
    return res.status(500).json({ success: false, error: err.message });
  }
});

export default router;
