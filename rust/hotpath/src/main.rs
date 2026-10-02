//! jdl-hotpath CLI — a stdin/stdout JSON filter (zero-FFI interop for Node/Python).
//!
//! Modes:
//!   jdl-hotpath            reads a ScanRequest  → writes a ScanResult   (arbitrage)
//!   jdl-hotpath analyze    reads {"bytecode":…} → writes AnalysisReport (EVM analysis)
//!   jdl-hotpath probe      reads {"url":…}      → writes a ProbeReport  (read-only RPC)
//!
//! `probe` is the only mode that touches the network, and it is read-only by
//! construction: the RPC method set is a closed enum that contains no write
//! method. It exists so the Rust leg of `jdl connectivity` measures the
//! toolchain this crate actually ships in, rather than asserting it exists.
//!
//! Examples:
//!   echo '{"edges":[{"from":"USDC","to":"WETH","rate":0.0005,"fee_bps":5},
//!                    {"from":"WETH","to":"USDC","rate":2020,"fee_bps":5}],
//!          "base":"USDC","loan_usd":100000,"gas_usd":1}' | jdl-hotpath
//!   echo '{"bytecode":"0x6080604052..."}' | jdl-hotpath analyze
//!   echo '{"url":"https://arb1.arbitrum.io/rpc","expected_chain_id":42161}' | jdl-hotpath probe

use std::io::{self, Read, Write};
use std::time::Duration;

use jdl_hotpath::{analyze_bytecode, best_cycle, probe, ScanRequest};

/// Wire shape accepted on stdin by `jdl-hotpath probe`.
///
/// The connectivity checker in `python/` emits `snake_case`; JS callers and the
/// CLI docs use `camelCase`. Both are accepted via `alias` because defaulting a
/// missing `expected_chain_id` to `None` would make an endpoint on the wrong
/// chain report `chain_ok: true` — a silent false pass, which is the one
/// failure mode this probe exists to eliminate.
#[derive(serde::Deserialize)]
#[serde(rename_all = "camelCase")]
struct ProbeRequest {
    url: String,
    #[serde(default, alias = "expected_chain_id")]
    expected_chain_id: Option<u64>,
    /// Per-request ceiling on the whole probe. Bounded so a hung endpoint
    /// cannot wedge the caller; the outer checker also imposes its own.
    #[serde(default = "default_probe_timeout_ms")]
    timeout_ms: u64,
}

fn default_probe_timeout_ms() -> u64 {
    10_000
}

fn main() {
    let mode = std::env::args().nth(1).unwrap_or_default();
    let mut input = String::new();
    if let Err(e) = io::stdin().read_to_string(&mut input) {
        fail(&mode, &format!("failed to read stdin: {e}"));
    }

    let json = if mode == "analyze" {
        let v: serde_json::Value = match serde_json::from_str(&input) {
            Ok(v) => v,
            Err(e) => fail(&mode, &format!("invalid JSON: {e}")),
        };
        let code = v.get("bytecode").and_then(|b| b.as_str()).unwrap_or("");
        match analyze_bytecode(code) {
            Ok(r) => serde_json::to_string(&r),
            Err(e) => fail(&mode, &e),
        }
    } else if mode == "probe" {
        let req: ProbeRequest = match serde_json::from_str(&input) {
            Ok(r) => r,
            Err(e) => fail(&mode, &format!("invalid ProbeRequest JSON: {e}")),
        };
        let report = probe::probe(
            &req.url,
            req.expected_chain_id,
            Duration::from_millis(req.timeout_ms),
        );
        // A transport failure is reported in the payload AND as a non-zero
        // exit, so a shell caller can branch without parsing JSON.
        let serialized = match serde_json::to_string(&report) {
            Ok(s) => s,
            Err(e) => fail(&mode, &format!("failed to serialize probe report: {e}")),
        };
        if report.error.is_some() || !report.chain_ok {
            eprintln!("jdl-hotpath: probe did not confirm connectivity");
            let mut out = io::stdout();
            let _ = out.write_all(serialized.as_bytes());
            let _ = out.write_all(b"\n");
            std::process::exit(2);
        }
        Ok(serialized)
    } else {
        let req: ScanRequest = match serde_json::from_str(&input) {
            Ok(r) => r,
            Err(e) => fail(&mode, &format!("invalid ScanRequest JSON: {e}")),
        };
        serde_json::to_string(&best_cycle(&req))
    };

    match json {
        Ok(s) => {
            let mut out = io::stdout();
            let _ = out.write_all(s.as_bytes());
            let _ = out.write_all(b"\n");
        }
        Err(e) => fail(&mode, &format!("failed to serialize result: {e}")),
    }
}

fn fail(mode: &str, msg: &str) -> ! {
    let _ = writeln!(io::stderr(), "jdl-hotpath: {msg}");
    // Emit a valid, parseable empty result on stdout so callers never choke.
    if mode == "analyze" {
        println!("{{\"error\":\"{}\"}}", msg.replace('"', "'"));
    } else if mode == "probe" {
        println!(
            "{{\"endpoint\":\"<unprobed>\",\"credentialInUrl\":false,\"chainId\":null,\
             \"expectedChainId\":null,\"chainOk\":false,\"blockNumber\":null,\
             \"gasPriceWei\":null,\"client\":null,\"calls\":[],\"totalMs\":0,\
             \"error\":\"{}\"}}",
            msg.replace('"', "'")
        );
    } else {
        println!("{{\"opportunity\":null,\"tokens\":0,\"edges\":0}}");
    }
    std::process::exit(1);
}
