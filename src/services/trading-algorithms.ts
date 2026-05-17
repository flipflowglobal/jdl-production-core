export function generateCompositeSignal(_agentId: string, _strategyId: string, _priceHistory: number[], _volHistory: number[], _chains: string[], _capital: number, _pnl: number): any {
  return { signal: "neutral", confidence: 0.5, action: "hold" };
}
