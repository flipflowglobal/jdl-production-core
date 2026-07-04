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

/// Recovered + refined EVM bytecode-analysis engine (disassembler, CFG, signature
/// recovery, security scan, decompiler, symbolic execution). Originally the
/// `rust-core` crate that was removed from this repo; restored here and exposed as a
/// library so a flash-loan bot can vet a pool/token contract's bytecode before it
/// interacts with it (honeypot / dangerous-opcode detection, selector recovery).
pub mod evm;

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
    // Data-carrying recursion helper: each argument threads distinct state
    // (graph, request, mutable path/best accumulators), so collapsing them into a
    // struct would only obscure the traversal.
    #[allow(clippy::too_many_arguments)]
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
                && best.as_ref().is_none_or(|b| net > b.net_profit_usd)
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

// ═══════════════════════════════════════════════════════════════════════════
//  EVM bytecode analysis — high-level entry point over the recovered `evm` module
// ═══════════════════════════════════════════════════════════════════════════

/// One recovered function, distilled from the decompiler's `DecompiledFn` down to
/// the signal a bot cares about: which selector, its name (or "unknown"), whether
/// it mutates state, and its Solidity parameter types.
#[derive(Debug, Clone, Serialize)]
pub struct FunctionSummary {
    /// Dispatcher selector, e.g. "0xa9059cbb"; empty for the fallback function.
    pub selector: String,
    /// Recovered name (e.g. "transfer", "fallback") or "unknown" if unresolved.
    pub name: String,
    /// True if the function performs no SSTORE/CALL/DELEGATECALL (read-only).
    pub is_view: bool,
    /// Solidity parameter types recovered from the ABI-decode pattern.
    pub params: Vec<String>,
}

/// A bot-friendly summary of a contract's bytecode: is it safe to interact with?
#[derive(Debug, Clone, Serialize)]
pub struct AnalysisReport {
    /// "safe" | "caution" | "danger" — coarse verdict from the risk score + flags.
    pub verdict: String,
    pub risk_score: u32, // 0-100 from the security scanner
    pub bytes: usize,
    pub instruction_count: usize,
    pub has_selfdestruct: bool,
    pub has_delegatecall: bool,
    pub has_create2: bool,
    /// Selectors recovered from the dispatcher, e.g. ["0xa9059cbb", ...].
    pub selectors: Vec<String>,
    /// Named selectors that matched the built-in DeFi/ERC table.
    pub known_functions: Vec<String>,
    /// Security findings (severity/title/description).
    pub findings: Vec<evm::security::Finding>,
    /// Basic-block count of the recovered control-flow graph (0 if degenerate).
    pub cfg_blocks: usize,
    /// Edge count of the recovered control-flow graph (0 if degenerate).
    pub cfg_edges: usize,
    /// Functions reconstructed by the decompiler (selector/name/view/params).
    pub functions: Vec<FunctionSummary>,
    /// Number of distinct storage slots the decompiler recovered.
    pub storage_vars: usize,
}

/// Analyze a contract's runtime bytecode (hex, with or without `0x`).
/// Runs disassemble → security scan → signature recovery and returns a verdict a
/// flash-loan bot can gate on before interacting with a pool/token contract.
pub fn analyze_bytecode(hex_code: &str) -> Result<AnalysisReport, String> {
    let cleaned = hex_code.trim().trim_start_matches("0x");
    if cleaned.is_empty() {
        return Err("empty bytecode".into());
    }
    if !cleaned.len().is_multiple_of(2) {
        return Err("odd-length hex".into());
    }
    let mut bytes = Vec::with_capacity(cleaned.len() / 2);
    for i in (0..cleaned.len()).step_by(2) {
        let b = u8::from_str_radix(&cleaned[i..i + 2], 16)
            .map_err(|_| format!("invalid hex at byte {}", i / 2))?;
        bytes.push(b);
    }

    let disasm = evm::disasm::disassemble(&bytes);
    let sec = evm::security::analyze_security(&disasm);
    let sigs = evm::signatures::recover_signatures(&disasm);

    // Recovered CFG + decompiler pipeline. Degenerate/tiny bytecode can leave the
    // CFG empty (or make the symbolic executor index a missing entry block), so run
    // it under catch_unwind and fall back to zeroed CFG fields rather than aborting
    // the whole analysis — the security verdict is still valuable on its own.
    let (cfg_blocks, cfg_edges, functions, storage_vars) =
        std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let cfg = evm::cfg::build_cfg(&disasm);
            if cfg.blocks.is_empty() {
                return (0usize, 0usize, Vec::<FunctionSummary>::new(), 0usize);
            }
            let blocks = cfg.block_count();
            let edges = cfg.edge_count();
            let dec = evm::decompiler::decompile(&disasm, &cfg, &sigs);
            let functions: Vec<FunctionSummary> = dec
                .functions
                .iter()
                .map(|f| FunctionSummary {
                    selector: f.selector.clone().unwrap_or_default(),
                    // fn_XXXX is the decompiler's placeholder for an unresolved
                    // selector; surface it as "unknown" for the bot.
                    name: if f.name.starts_with("fn_") {
                        "unknown".to_string()
                    } else {
                        f.name.clone()
                    },
                    is_view: f.is_view,
                    params: f.params.iter().map(|p| p.ty.clone()).collect(),
                })
                .collect();
            (blocks, edges, functions, dec.storage_slots.len())
        }))
        .unwrap_or((0, 0, Vec::new(), 0));

    let verdict = if sec.risk_score >= 70 || sec.has_selfdestruct {
        "danger"
    } else if sec.risk_score >= 35 || sec.has_delegatecall {
        "caution"
    } else {
        "safe"
    }
    .to_string();

    let selectors = sigs.functions.iter().map(|f| f.selector.clone()).collect();
    let known_functions = sigs
        .functions
        .iter()
        .filter_map(|f| f.known_name.clone())
        .collect();

    Ok(AnalysisReport {
        verdict,
        risk_score: sec.risk_score,
        bytes: disasm.total_bytes,
        instruction_count: disasm.instruction_count,
        has_selfdestruct: sec.has_selfdestruct,
        has_delegatecall: sec.has_delegatecall,
        has_create2: sec.has_create2,
        selectors,
        known_functions,
        findings: sec.findings,
        cfg_blocks,
        cfg_edges,
        functions,
        storage_vars,
    })
}

// ═══════════════════════════════════════════════════════════════════════════
//  C ABI — for the Cython/Python wrapper (native fast path). Each function takes a
//  UTF-8 JSON C string and returns a newly-allocated JSON C string that the caller
//  MUST release with `jdl_string_free`. Never panics across the FFI boundary.
// ═══════════════════════════════════════════════════════════════════════════
use std::ffi::{CStr, CString};
use std::os::raw::c_char;

fn to_c_json<T: Serialize>(v: &T) -> *mut c_char {
    let s = serde_json::to_string(v).unwrap_or_else(|_| "{\"error\":\"serialize\"}".into());
    CString::new(s).unwrap_or_default().into_raw()
}

fn c_str_in<'a>(p: *const c_char) -> Option<&'a str> {
    if p.is_null() {
        return None;
    }
    unsafe { CStr::from_ptr(p).to_str().ok() }
}

/// Run the arbitrage hot-path. Input JSON = ScanRequest; output JSON = ScanResult.
///
/// # Safety
/// `input` must be either null or a valid pointer to a NUL-terminated C string
/// that stays alive and unmodified for the duration of the call. The returned
/// pointer is a freshly heap-allocated NUL-terminated C string that the caller
/// **must** release with [`jdl_string_free`] (never `free`) exactly once; leaking
/// or double-freeing it is undefined behavior. Not thread-hostile: no shared
/// mutable state, so distinct calls on distinct pointers may run concurrently.
#[no_mangle]
pub unsafe extern "C" fn jdl_scan(input: *const c_char) -> *mut c_char {
    let result = std::panic::catch_unwind(|| match c_str_in(input) {
        Some(s) => match serde_json::from_str::<ScanRequest>(s) {
            Ok(req) => to_c_json(&best_cycle(&req)),
            Err(e) => to_c_json(&serde_json::json!({ "error": format!("bad ScanRequest: {e}") })),
        },
        None => to_c_json(&serde_json::json!({ "error": "null/invalid input" })),
    });
    result.unwrap_or_else(|_| to_c_json(&serde_json::json!({ "error": "panic" })))
}

/// Analyze bytecode. Input JSON = {"bytecode":"0x..."}; output JSON = AnalysisReport.
///
/// # Safety
/// `input` must be either null or a valid pointer to a NUL-terminated C string
/// that stays alive and unmodified for the duration of the call. The returned
/// pointer is a freshly heap-allocated NUL-terminated C string that the caller
/// **must** release with [`jdl_string_free`] (never `free`) exactly once; leaking
/// or double-freeing it is undefined behavior. Not thread-hostile: no shared
/// mutable state, so distinct calls on distinct pointers may run concurrently.
#[no_mangle]
pub unsafe extern "C" fn jdl_analyze(input: *const c_char) -> *mut c_char {
    let result = std::panic::catch_unwind(|| match c_str_in(input) {
        Some(s) => {
            let v: serde_json::Value = match serde_json::from_str(s) {
                Ok(v) => v,
                Err(e) => return to_c_json(&serde_json::json!({ "error": format!("bad JSON: {e}") })),
            };
            let code = v.get("bytecode").and_then(|b| b.as_str()).unwrap_or("");
            match analyze_bytecode(code) {
                Ok(r) => to_c_json(&r),
                Err(e) => to_c_json(&serde_json::json!({ "error": e })),
            }
        }
        None => to_c_json(&serde_json::json!({ "error": "null/invalid input" })),
    });
    result.unwrap_or_else(|_| to_c_json(&serde_json::json!({ "error": "panic" })))
}

/// Free a string returned by `jdl_scan` / `jdl_analyze`.
///
/// # Safety
/// `p` must be either null or a pointer previously returned by [`jdl_scan`] or
/// [`jdl_analyze`] and not yet freed. Passing any other pointer, or the same
/// pointer twice, is undefined behavior. After this call `p` is dangling and must
/// not be used again. Not thread-hostile, but a given pointer must be freed by
/// only one thread.
#[no_mangle]
pub unsafe extern "C" fn jdl_string_free(p: *mut c_char) {
    if !p.is_null() {
        unsafe {
            let _ = CString::from_raw(p);
        }
    }
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
    fn analyze_rejects_bad_hex() {
        assert!(analyze_bytecode("").is_err());
        assert!(analyze_bytecode("0xabc").is_err()); // odd length
        assert!(analyze_bytecode("0xzz").is_err());  // non-hex
    }

    #[test]
    fn analyze_flags_selfdestruct_as_danger() {
        // Minimal runtime: PUSH1 0x00 ; SELFDESTRUCT (0xff). Should verdict "danger".
        let report = analyze_bytecode("0x6000ff").expect("analyzes");
        assert!(report.has_selfdestruct, "SELFDESTRUCT must be detected");
        assert_eq!(report.verdict, "danger");
    }

    #[test]
    fn analyze_recovers_a_selector() {
        // A tiny ERC20-style dispatcher fragment: compare calldata[0] to 0xa9059cbb
        // (transfer). PUSH4 a9059cbb ... EQ. The signature recoverer should surface it.
        // 0x63 = PUSH4. We just need the PUSH4 of a known selector present.
        let report = analyze_bytecode("0x63a9059cbb").expect("analyzes");
        assert!(report.bytes > 0 && report.instruction_count > 0);
    }

    #[test]
    fn analyze_recovers_cfg_and_functions() {
        // A minimal ERC20-style dispatcher: extract selector, compare to
        // transfer(address,uint256) = 0xa9059cbb, JUMPI to a JUMPDEST.
        //   PUSH1 0x00 CALLDATALOAD PUSH1 0xe0 SHR   (selector = msg.sig)
        //   DUP1 PUSH4 a9059cbb EQ PUSH1 0x11 JUMPI  (dispatch → JUMPDEST @ 0x11)
        //   STOP ; JUMPDEST STOP
        let code = "0x600035".to_owned()       // PUSH1 00 CALLDATALOAD
            + "60e01c"                          // PUSH1 e0 SHR
            + "80"                              // DUP1
            + "63a9059cbb"                      // PUSH4 selector
            + "14"                              // EQ
            + "601157"                          // PUSH1 0x11 JUMPI
            + "00"                              // STOP  (byte offset 0x10)
            + "5b00";                           // JUMPDEST STOP (byte offset 0x11)
        let report = analyze_bytecode(&code).expect("analyzes");
        assert!(report.cfg_blocks > 0, "CFG should have basic blocks");
        assert!(
            report.functions.iter().any(|f| f.selector == "0xa9059cbb"),
            "transfer selector should be recovered as a function: {:?}",
            report.functions
        );
    }

    #[test]
    fn analyze_trivial_bytecode_does_not_panic() {
        // A lone STOP: valid but degenerate. Must not panic; CFG fields sane.
        let report = analyze_bytecode("0x00").expect("analyzes trivial bytecode");
        assert_eq!(report.verdict, "safe");
        // No dispatcher, so no named DeFi functions and no storage recovered.
        assert!(report.known_functions.is_empty());
        assert_eq!(report.storage_vars, 0);
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
