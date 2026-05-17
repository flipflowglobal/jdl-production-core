const SYSTEM_FEE_RATE = 0.0075;
const FUNDING_FEE_RATE = 0.02;
const MAIN_WALLET = "0x8C117222E14DcAA20fE3087C491b1d330D0F625a";

export interface FeeRecord {
  id: string;
  txHash: string;
  feeTxHash: string;
  fromWallet: string;
  toWallet: string;
  originalAmount: number;
  feeAmount: number;
  netAmount: number;
  feeRate: number;
  feeDestination: string;
  chain: string;
  token: string;
  type: "wallet-transfer" | "agent-send" | "agent-revenue" | "user-send" | "funding-deposit";
  userId: string;
  timestamp: string;
}

const feeLedger: FeeRecord[] = [];

let idCounter = 1000;

function generateTxHash(): string {
  return `0x${Array.from({ length: 64 }, () => Math.floor(Math.random() * 16).toString(16)).join("")}`;
}

export function calculateFee(amount: number): { feeAmount: number; netAmount: number } {
  const feeAmount = Math.round(amount * SYSTEM_FEE_RATE * 100000000) / 100000000;
  const netAmount = Math.round((amount - feeAmount) * 100000000) / 100000000;
  return { feeAmount, netAmount };
}

export function recordFee(params: {
  txHash: string;
  fromWallet: string;
  toWallet: string;
  originalAmount: number;
  chain: string;
  token: string;
  type: FeeRecord["type"];
  userId: string;
}): FeeRecord {
  const { feeAmount, netAmount } = calculateFee(params.originalAmount);
  const record: FeeRecord = {
    id: `fee-${++idCounter}`,
    txHash: params.txHash,
    feeTxHash: generateTxHash(),
    fromWallet: params.fromWallet,
    toWallet: params.toWallet,
    originalAmount: params.originalAmount,
    feeAmount,
    netAmount,
    feeRate: SYSTEM_FEE_RATE,
    feeDestination: MAIN_WALLET,
    chain: params.chain,
    token: params.token,
    type: params.type,
    userId: params.userId,
    timestamp: new Date().toISOString(),
  };
  feeLedger.push(record);
  return record;
}

export function getFeesByUser(userId: string): FeeRecord[] {
  return feeLedger.filter((f) => f.userId === userId);
}

export function getAllFees(): FeeRecord[] {
  return [...feeLedger];
}

export function getFeeSummary(userId: string) {
  const userFees = getFeesByUser(userId);
  const totalFees = userFees.reduce((s, f) => s + f.feeAmount, 0);
  const totalVolume = userFees.reduce((s, f) => s + f.originalAmount, 0);
  return {
    totalFeesPaid: Math.round(totalFees * 100) / 100,
    totalTransactionVolume: Math.round(totalVolume * 100) / 100,
    transactionCount: userFees.length,
    feeRate: `${SYSTEM_FEE_RATE * 100}%`,
    feeDestination: MAIN_WALLET,
  };
}

export function calculateFundingFee(amount: number): { feeAmount: number; netAmount: number } {
  const feeAmount = Math.round(amount * FUNDING_FEE_RATE * 100000000) / 100000000;
  const netAmount = Math.round((amount - feeAmount) * 100000000) / 100000000;
  return { feeAmount, netAmount };
}

export function recordFundingFee(params: {
  txHash: string;
  fromWallet: string;
  toWallet: string;
  originalAmount: number;
  chain: string;
  token: string;
  userId: string;
}): FeeRecord {
  const { feeAmount, netAmount } = calculateFundingFee(params.originalAmount);
  const record: FeeRecord = {
    id: `fee-${++idCounter}`,
    txHash: params.txHash,
    feeTxHash: generateTxHash(),
    fromWallet: params.fromWallet,
    toWallet: params.toWallet,
    originalAmount: params.originalAmount,
    feeAmount,
    netAmount,
    feeRate: FUNDING_FEE_RATE,
    feeDestination: MAIN_WALLET,
    chain: params.chain,
    token: params.token,
    type: "funding-deposit",
    userId: params.userId,
    timestamp: new Date().toISOString(),
  };
  feeLedger.push(record);
  return record;
}

export function getSystemFeeRate(): number {
  return SYSTEM_FEE_RATE;
}

export function getFundingFeeRate(): number {
  return FUNDING_FEE_RATE;
}

export function getMainWallet(): string {
  return MAIN_WALLET;
}

function seedHistoricalFees() {
  const types: FeeRecord["type"][] = ["wallet-transfer", "agent-send", "agent-revenue", "user-send"];
  const chains = ["ethereum", "polygon", "arbitrum", "bsc", "avalanche", "optimism"];
  const tokens = ["ETH", "USDC", "USDT", "WBTC", "MATIC", "ARB"];
  const wallets = [
    "0x742d35Cc6634C0532925a3b844Bc9e7595f2F4a8",
    "0x8B3a4F6d2e9A7c1b5E0D3f8A6B4c2E1d0F2C1b",
    "0xA1B2C3D4E5F6a7b8c9d0e1f2A3B4C5D6E7F89E0f",
  ];

  for (let i = 0; i < 35; i++) {
    const daysAgo = Math.floor(Math.random() * 30);
    const hoursAgo = Math.floor(Math.random() * 24);
    const amount = Math.round((Math.random() * 5000 + 50) * 100) / 100;
    const chain = chains[Math.floor(Math.random() * chains.length)];
    const token = tokens[Math.floor(Math.random() * tokens.length)];
    const type = types[Math.floor(Math.random() * types.length)];
    const fromW = wallets[Math.floor(Math.random() * wallets.length)];
    const toW = wallets[Math.floor(Math.random() * wallets.length)];
    const ts = new Date(Date.now() - daysAgo * 86400000 - hoursAgo * 3600000);

    const { feeAmount, netAmount } = calculateFee(amount);
    feeLedger.push({
      id: `fee-${++idCounter}`,
      txHash: generateTxHash(),
      feeTxHash: generateTxHash(),
      fromWallet: fromW,
      toWallet: toW,
      originalAmount: amount,
      feeAmount,
      netAmount,
      feeRate: SYSTEM_FEE_RATE,
      feeDestination: MAIN_WALLET,
      chain,
      token,
      type,
      userId: "user-001",
      timestamp: ts.toISOString(),
    });
  }

  feeLedger.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
}

seedHistoricalFees();
