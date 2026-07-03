//! jdl-hotpath — the CPU-intensive core of the JDL flash-loan system.
//!
//! Given a set of directed exchange edges (token → token with a real quoted rate
//! and a pool fee), find the most profitable arbitrage **cycle** — a loop that
//! starts and ends in the loan asset and comes out ahead after fees, the Aave
//! flash-loan premium, and gas.
//!
//! This is deliberately the piece worth writing in Rust: detecting a profitable
//! cycle is a negative-cycle search over `-ln(rate)` weights (Bellman-Ford), which
//! is O(V·E) per source and gets run every scan tick over many pools. The Python
//! engine feeds it **real** quotes (Uniswap V3 Quoter etc.) — this crate does the
//! math, it does not invent prices.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// Aave V3 flash-loan premium: 0.05% = 5 basis points.
pub const AAVE_PREMIUM_BPS: f64 = 5.0;
const BPS_DENOM: f64 = 10_000.0;

/// A directed exchange edge: swapping `from` → `to` yields `rate` units of `to`
/// per unit of `from` (the real, quote-derived mid price for the size in question),
/// against a pool charging `fee_bps` basis points.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Edge {
    pub from: String,
    pub to: String,
    pub rate: f64,
    pub fee_bps: f64,
}

impl Edge {
    /// Effective multiplier for this hop, net of the pool fee.
    pub fn effective_rate(&self) -> f64 {
        self.rate * (1.0 - self.fee_bps / BPS_DENOM)
    }
}

/// Scan inputs. `loan_usd` sizes the trade; `gas_usd` is the on-chain cost to beat.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ScanRequest {
    pub edges: Vec<Edge>,
    /// The asset the loop must start and end in (e.g. "USDC").
    pub base: String,
    pub loan_usd: f64,
    pub gas_usd: f64,
    /// Extra safety margin required on top of break-even, in USD.
    #[serde(default)]
    pub min_profit_usd: f64,
}

/// A profitable arbitrage loop.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Opportunity {
    /// Token path, e.g. ["USDC","WETH","USDC"].
    pub path: Vec<String>,
    /// Product of effective hop rates around the loop (>1.0 means gross profit).
    pub gross_multiplier: f64,
    /// Net profit in USD after fees + Aave premium + gas.
    pub net_profit_usd: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct ScanResult {
    pub opportunity: Option<Opportunity>,
    /// How many distinct tokens / edges were considered (telemetry).
    pub tokens: usize,
    pub edges: usize,
}

/// Find the single best (most profitable) arbitrage cycle through `base`.
///
/// Approach: build the token graph, then for every simple cycle returning to `base`
/// up to a bounded hop count, evaluate the compounded effective rate. Real arbitrage
/// loops on a single chain are short (2–4 hops) because each extra hop multiplies fees
/// and slippage, so a bounded DFS is both correct and fast — and, unlike a raw
/// Bellman-Ford negative-cycle flag, it returns the concrete path and its magnitude.
pub fn best_cycle(req: &ScanRequest) -> ScanResult {
    let mut adj: HashMap<&str, Vec<&Edge>> = HashMap::new();
    let mut tokens = std::collections::HashSet::new();
    for e in &req.edges {
        adj.entry(e.from.as_str()).or_default().push(e);
        tokens.insert(e.from.as_str());
        tokens.insert(e.to.as_str());
    }

    const MAX_HOPS: usize = 5; // loop length cap; longer loops are dominated by fees
    let mut best: Option<Opportunity> = None;

    // DFS from base, tracking the compounded multiplier; close the loop when we
    // return to base with >= 2 hops.
    fn dfs<'a>(
        node: &'a str,
        base: &str,
        adj: &HashMap<&'a str, Vec<&'a Edge>>,
        mult: f64,
        depth: usize,
        path: &mut Vec<String>,
        best: &mut Option<Opportunity>,
        req: &ScanRequest,
    ) {
        if depth > MAX_HOPS {
            return;
        }
        if node == base && depth >= 2 {
            let net = net_profit(mult, req);
            if net > req.min_profit_usd
                && best.as_ref().map_or(true, |b| net > b.net_profit_usd)
            {
                *best = Some(Opportunity {
                    path: path.clone(),
                    gross_multiplier: mult,
                    net_profit_usd: net,
                });
            }
            return; // a loop is closed; don't extend through base again
        }
        if let Some(edges) = adj.get(node) {
            for e in edges {
                // avoid immediately revisiting a token except to close on base
                if e.to != base && path.iter().any(|p| p == &e.to) {
                    continue;
                }
                path.push(e.to.clone());
                dfs(&e.to, base, adj, mult * e.effective_rate(), depth + 1, path, best, req);
                path.pop();
            }
        }
    }

    let mut path = vec![req.base.clone()];
    dfs(&req.base, &req.base, &adj, 1.0, 0, &mut path, &mut best, req);

    ScanResult {
        opportunity: best,
        tokens: tokens.len(),
        edges: req.edges.len(),
    }
}

/// Net USD profit of a loop with gross multiplier `mult`, given the request sizing.
/// gross gain = loan_usd * (mult - 1); costs = Aave premium on the loan + gas.
pub fn net_profit(mult: f64, req: &ScanRequest) -> f64 {
    let gross_gain = req.loan_usd * (mult - 1.0);
    let premium = req.loan_usd * AAVE_PREMIUM_BPS / BPS_DENOM;
    gross_gain - premium - req.gas_usd
}

#[cfg(test)]
mod tests {
    use super::*;

    fn edge(from: &str, to: &str, rate: f64, fee_bps: f64) -> Edge {
        Edge { from: from.into(), to: to.into(), rate, fee_bps }
    }

    #[test]
    fn effective_rate_applies_fee() {
        let e = edge("A", "B", 2.0, 30.0); // 0.30% fee tier
        assert!((e.effective_rate() - 2.0 * 0.997).abs() < 1e-12);
    }

    #[test]
    fn detects_profitable_triangle() {
        // USDC->WETH->USDC that nets ~1% gross before fees/premium.
        let req = ScanRequest {
            edges: vec![
                edge("USDC", "WETH", 1.0 / 2000.0, 5.0),
                edge("WETH", "USDC", 2020.0, 5.0), // sell higher → profitable loop
            ],
            base: "USDC".into(),
            loan_usd: 100_000.0,
            gas_usd: 1.0,
            min_profit_usd: 0.0,
        };
        let r = best_cycle(&req);
        let opp = r.opportunity.expect("should find a profitable loop");
        assert_eq!(opp.path, vec!["USDC", "WETH", "USDC"]);
        assert!(opp.gross_multiplier > 1.0);
        assert!(opp.net_profit_usd > 0.0);
    }

    #[test]
    fn rejects_unprofitable_after_premium_and_gas() {
        // A loop that's barely >1 gross but loses to the Aave premium + gas.
        let req = ScanRequest {
            edges: vec![
                edge("USDC", "WETH", 1.0 / 2000.0, 5.0),
                edge("WETH", "USDC", 2000.2, 5.0),
            ],
            base: "USDC".into(),
            loan_usd: 100_000.0,
            gas_usd: 50.0,
            min_profit_usd: 0.0,
        };
        let r = best_cycle(&req);
        assert!(r.opportunity.is_none(), "must not report a net-losing loop");
    }

    #[test]
    fn no_loop_when_no_cycle() {
        let req = ScanRequest {
            edges: vec![edge("USDC", "WETH", 1.0 / 2000.0, 5.0)], // one-way, no return
            base: "USDC".into(),
            loan_usd: 100_000.0,
            gas_usd: 1.0,
            min_profit_usd: 0.0,
        };
        assert!(best_cycle(&req).opportunity.is_none());
    }

    #[test]
    fn net_profit_matches_formula() {
        let req = ScanRequest {
            edges: vec![], base: "USDC".into(),
            loan_usd: 10_000.0, gas_usd: 2.0, min_profit_usd: 0.0,
        };
        // mult 1.01 → gross 100, premium 5, gas 2 → net 93
        assert!((net_profit(1.01, &req) - 93.0).abs() < 1e-9);
    }

    #[test]
    fn prefers_higher_net_loop() {
        // Two return edges; the better one should win.
        let req = ScanRequest {
            edges: vec![
                edge("USDC", "WETH", 1.0 / 2000.0, 5.0),
                edge("WETH", "USDC", 2010.0, 5.0),
                edge("WETH", "USDC", 2050.0, 5.0),
            ],
            base: "USDC".into(),
            loan_usd: 100_000.0, gas_usd: 1.0, min_profit_usd: 0.0,
        };
        let opp = best_cycle(&req).opportunity.unwrap();
        // 2050 return is the more profitable close
        assert!(opp.gross_multiplier > (1.0 / 2000.0) * 0.9995 * 2010.0 * 0.9995);
    }
}
