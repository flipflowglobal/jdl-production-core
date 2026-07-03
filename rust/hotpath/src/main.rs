//! jdl-hotpath CLI — a stdin/stdout JSON filter (zero-FFI interop for Node/Python).
//!
//! Modes:
//!   jdl-hotpath            reads a ScanRequest  → writes a ScanResult   (arbitrage)
//!   jdl-hotpath analyze    reads {"bytecode":…} → writes AnalysisReport (EVM analysis)
//!
//! Examples:
//!   echo '{"edges":[{"from":"USDC","to":"WETH","rate":0.0005,"fee_bps":5},
//!                    {"from":"WETH","to":"USDC","rate":2020,"fee_bps":5}],
//!          "base":"USDC","loan_usd":100000,"gas_usd":1}' | jdl-hotpath
//!   echo '{"bytecode":"0x6080604052..."}' | jdl-hotpath analyze

use std::io::{self, Read, Write};

use jdl_hotpath::{analyze_bytecode, best_cycle, ScanRequest};

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
    } else {
        println!("{{\"opportunity\":null,\"tokens\":0,\"edges\":0}}");
    }
    std::process::exit(1);
}
