//! jdl-hotpath CLI — reads a ScanRequest as JSON on stdin, writes a ScanResult as
//! JSON on stdout. The Node orchestration server spawns this per scan tick; keeping
//! it a stdin/stdout filter means zero FFI and trivial language interop.
//!
//! Example:
//!   echo '{"edges":[{"from":"USDC","to":"WETH","rate":0.0005,"fee_bps":5},
//!                    {"from":"WETH","to":"USDC","rate":2020,"fee_bps":5}],
//!          "base":"USDC","loan_usd":100000,"gas_usd":1}' | jdl-hotpath

use std::io::{self, Read, Write};

use jdl_hotpath::{best_cycle, ScanRequest};

fn main() {
    let mut input = String::new();
    if let Err(e) = io::stdin().read_to_string(&mut input) {
        fail(&format!("failed to read stdin: {e}"));
    }
    let req: ScanRequest = match serde_json::from_str(&input) {
        Ok(r) => r,
        Err(e) => fail(&format!("invalid ScanRequest JSON: {e}")),
    };
    let result = best_cycle(&req);
    match serde_json::to_string(&result) {
        Ok(s) => {
            let mut out = io::stdout();
            let _ = out.write_all(s.as_bytes());
            let _ = out.write_all(b"\n");
        }
        Err(e) => fail(&format!("failed to serialize result: {e}")),
    }
}

fn fail(msg: &str) -> ! {
    let _ = writeln!(io::stderr(), "jdl-hotpath: {msg}");
    // Emit a valid, empty result on stdout so callers can always parse it.
    println!("{{\"opportunity\":null,\"tokens\":0,\"edges\":0}}");
    std::process::exit(1);
}
