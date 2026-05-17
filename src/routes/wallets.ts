import { Router, type IRouter } from "express";
import { ethers } from "ethers";
import { calculateFee, recordFee } from "../services/system-fees.js";
import { getPriceState } from "../services/price-feed.js";

const router: IRouter = Router();

const wallets = [
  {
    id: "w-001",
    name: "Primary Trading Vault",
    address: "0x7FD7f50ed0D0625887072a95426F6A5d1e0BD3bF",
    type: "generated",
    network: "Ethereum",
    ethBalance: 4.2831,
    usdcBalance: 12450.0,
    totalUsd: 29841.5,
    tokens: [
      { symbol: "ETH", balance: 4.2831, usd: 13886.42 },
      { symbol: "USDC", balance: 12450.0, usd: 12450.0 },
      { symbol: "USDT", balance: 2300.0, usd: 2300.0 },
      { symbol: "WBTC", balance: 0.0412, usd: 2961.82 },
    ],
    lastSync: "2026-04-03T10:15:00Z",
  },
  {
    id: "w-002",
    name: "Arbitrage Capital",
    address: "0x64DC101bb50C692b05080595B40D95f93c878A44",
    type: "generated",
    network: "Arbitrum",
    ethBalance: 1.844,
    usdcBalance: 5200.0,
    totalUsd: 11982.4,
    tokens: [
      { symbol: "ETH", balance: 1.844, usd: 5979.36 },
      { symbol: "USDC", balance: 5200.0, usd: 5200.0 },
    ],
    lastSync: "2026-04-03T10:14:30Z",
  },
  {
    id: "w-003",
    name: "MetaMask Wallet",
    address: "0x65497D27BFDa3F502BF4aE90597C39c14eFBF229",
    type: "connected",
    network: "Polygon",
    ethBalance: 0.5,
    usdcBalance: 800.0,
    totalUsd: 4171.2,
    tokens: [
      { symbol: "ETH", balance: 0.5, usd: 1620.79 },
      { symbol: "USDC", balance: 800.0, usd: 800.0 },
      { symbol: "MATIC", balance: 850, usd: 1241.0 },
    ],
    lastSync: "2026-04-03T10:10:00Z",
  },
];

router.get("/wallets", (_req, res) => {
  const totalUsd = wallets.reduce((a, w) => a + w.totalUsd, 0);
  res.json({ wallets: wallets.map(w => ({ ...w, tokens: undefined })), totalUsd });
});

router.post("/wallets", (req, res) => {
  const body = req.body as { name: string; type: string; network: string; address?: string };

  let address: string;
  let hdData: { mnemonic: string; privateKey: string } | undefined;

  if (body.address) {
    address = body.address;
  } else {
    const wallet = ethers.Wallet.createRandom();
    address = wallet.address;
    hdData = {
      mnemonic: wallet.mnemonic?.phrase ?? "",
      privateKey: wallet.privateKey,
    };
  }

  const newWallet = {
    id: `w-${Date.now()}`,
    name: body.name,
    address,
    type: body.type || "generated",
    network: body.network,
    ethBalance: 0,
    usdcBalance: 0,
    totalUsd: 0,
    tokens: [],
    lastSync: new Date().toISOString(),
    ...(hdData ? { hdGenerated: true } : {}),
  };
  wallets.push(newWallet);

  res.status(201).json({
    ...newWallet,
    ...(hdData ? {
      mnemonic: hdData.mnemonic,
      privateKey: hdData.privateKey,
      warning: "Store your mnemonic securely. It will never be shown again.",
    } : {}),
  });
});

router.get("/wallets/:id", (req, res) => {
  const wallet = wallets.find(w => w.id === req.params.id);
  if (!wallet) {
    res.status(404).json({ error: "Wallet not found" });
    return;
  }
  res.json(wallet);
});

router.get("/wallets/:id/balance", (req, res) => {
  const wallet = wallets.find(w => w.id === req.params.id);
  if (!wallet) {
    res.status(404).json({ error: "Wallet not found" });
    return;
  }
  const priceState = getPriceState();
  const ethPrice = (priceState as any).tokens?.["ETH"]?.price ?? (priceState as any).eth?.price ?? 2000;
  res.json({
    walletId: wallet.id,
    address: wallet.address,
    network: wallet.network,
    tokens: wallet.tokens,
    ethPrice,
    totalUsd: wallet.totalUsd,
    lastUpdated: new Date().toISOString(),
  });
});

router.post("/wallets/:id/send", (req, res) => {
  const body = req.body as { to: string; amount: number; token: string; gasPrice: number };
  const wallet = wallets.find(w => w.id === req.params.id);
  if (!wallet) {
    res.status(404).json({ error: "Wallet not found" });
    return;
  }

  const { feeAmount, netAmount } = calculateFee(body.amount);
  const txHash = `0x${Array.from({ length: 64 }, () => Math.floor(Math.random() * 16).toString(16)).join("")}`;

  const feeRecord = recordFee({
    txHash,
    fromWallet: wallet.address,
    toWallet: body.to,
    originalAmount: body.amount,
    chain: wallet.network.toLowerCase(),
    token: body.token || "ETH",
    type: "wallet-transfer",
    userId: "user-001",
  });

  res.json({
    txHash,
    status: "pending",
    from: wallet.address,
    to: body.to,
    amount: netAmount,
    originalAmount: body.amount,
    token: body.token,
    gasEstimate: 21000,
    systemFee: {
      rate: "0.75%",
      feeAmount: feeRecord.feeAmount,
      netAmountSent: feeRecord.netAmount,
      feeDestination: "0x8C117222E14DcAA20fE3087C491b1d330D0F625a",
      feeTxHash: feeRecord.feeTxHash,
    },
  });
});

router.delete("/wallets/:id", (req, res) => {
  const idx = wallets.findIndex(w => w.id === req.params.id);
  if (idx === -1) {
    res.status(404).json({ error: "Wallet not found" });
    return;
  }
  wallets.splice(idx, 1);
  res.status(204).send();
});

export default router;
