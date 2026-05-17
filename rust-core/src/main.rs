use std::io::{self, Read};
use serde::{Deserialize, Serialize};

mod evm;

#[derive(Deserialize)]
struct Pool {
    address: String,
    token0: String,
    token1: String,
    fee: u32,
    liquidity: u128,
    sqrt_price: u128,
}

#[derive(Serialize)]
struct Route {
    path: Vec<String>,
    expected_profit_usd: f64,
    confidence: f64,
}

fn find_routes(_pools: Vec<Pool>) -> Vec<Route> {
    // Placeholder — Bellman-Ford will be ported here
    vec![]
}

#[derive(Deserialize)]
struct QuoteRequest {
    chain: String,
    token_in: String,
    token_out: String,
    amount: String,
}

#[derive(Serialize)]
struct Quote {
    amount_out: String,
    estimated_gas: String,
    price_impact: f64,
}

fn quote_swap(_req: QuoteRequest) -> Quote {
    // Placeholder — Uniswap V3 quoting will be ported here
    Quote {
        amount_out: "0".to_string(),
        estimated_gas: "0".to_string(),
        price_impact: 0.0,
    }
}

#[derive(Deserialize)]
struct FlashLoanRequest {
    chain: String,
    asset: String,
    amount: String,
    steps: Vec<serde_json::Value>,
}

#[derive(Serialize)]
struct FlashLoanResult {
    tx_hash: String,
    estimated_profit: String,
}

fn build_flash_loan(_req: FlashLoanRequest) -> FlashLoanResult {
    // Placeholder — Aave V3 flash loan will be ported here
    FlashLoanResult {
        tx_hash: "0x0000000000000000000000000000000000000000".to_string(),
        estimated_profit: "0".to_string(),
    }
}

fn evm_analyze(hex_str: String) -> serde_json::Value {
    // Placeholder — Hinsdale EVM analysis will be called here
    serde_json::json!({
        "opcodes_count": hex_str.len() / 2,
        "functions_found": 0,
        "security_issues": [],
    })
}

fn yul_ir(hex_str: String) -> serde_json::Value {
    let bytes = hex::decode(&hex_str).unwrap_or_else(|e| {
        eprintln!("Invalid hex: {e}");
        std::process::exit(1);
    });
    let disasm = evm::disasm::disassemble(&bytes);
    let cfg = evm::cfg::build_cfg(&disasm);
    let sigs = evm::signatures::recover_signatures(&disasm);
    let mut exec = evm::symbolic::SymExec::new(&disasm, &cfg);
    let results = exec.run();

    let mut blocks: Vec<serde_json::Value> = results.iter()
        .map(|(&block_id, result)| {
            serde_json::json!({
                "block_id": block_id,
                "stmts": result.stmts,
            })
        })
        .collect();
    blocks.sort_by_key(|b| b["block_id"].as_u64().unwrap_or(0));
    serde_json::Value::Array(blocks)
}

fn storage_layout(hex_str: String) -> serde_json::Value {
    let bytes = hex::decode(&hex_str).unwrap_or_else(|e| {
        eprintln!("Invalid hex: {e}");
        std::process::exit(1);
    });
    let disasm = evm::disasm::disassemble(&bytes);
    let cfg = evm::cfg::build_cfg(&disasm);
    let sigs = evm::signatures::recover_signatures(&disasm);
    let decompiled = evm::decompiler::decompile(&disasm, &cfg, &sigs);
    serde_json::to_value(decompiled.storage_slots).unwrap_or_else(|e| {
        eprintln!("Serialization error: {e}");
        std::process::exit(1);
    })
}

fn abi_decode(hex_str: String) -> serde_json::Value {
    let bytes = hex::decode(&hex_str).unwrap_or_else(|e| {
        eprintln!("Invalid hex: {e}");
        std::process::exit(1);
    });
    let disasm = evm::disasm::disassemble(&bytes);
    let cfg = evm::cfg::build_cfg(&disasm);
    let sigs = evm::signatures::recover_signatures(&disasm);
    let decompiled = evm::decompiler::decompile(&disasm, &cfg, &sigs);

    let functions: Vec<serde_json::Value> = decompiled.functions.iter().map(|f| {
        serde_json::json!({
            "selector": f.selector,
            "name": f.name,
            "params": f.params,
        })
    }).collect();

    serde_json::Value::Array(functions)
}

fn security_scan(hex_str: String) -> serde_json::Value {
    let bytes = hex::decode(&hex_str).unwrap_or_else(|e| {
        eprintln!("Invalid hex: {e}");
        std::process::exit(1);
    });
    let disasm = evm::disasm::disassemble(&bytes);
    let report = evm::security::analyze_security(&disasm);
    serde_json::to_value(report).unwrap_or_else(|e| {
        eprintln!("Serialization error: {e}");
        std::process::exit(1);
    })
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 2 {
        eprintln!("Usage: jdl-core <command>");
        eprintln!("Commands: find-routes, quote-swap, flash-loan, evm-analyze, yul-ir, storage-layout, abi-decode, security-scan");
        std::process::exit(1);
    }

    let mut input = String::new();
    io::stdin().read_to_string(&mut input).expect("Failed to read stdin");

    let result: serde_json::Value = match args[1].as_str() {
        "find-routes" => {
            let pools: Vec<Pool> = serde_json::from_str(&input).expect("Invalid pool data");
            let routes = find_routes(pools);
            serde_json::to_value(routes).unwrap()
        }
        "quote-swap" => {
            let req: QuoteRequest = serde_json::from_str(&input).expect("Invalid quote request");
            let quote = quote_swap(req);
            serde_json::to_value(quote).unwrap()
        }
        "flash-loan" => {
            let req: FlashLoanRequest = serde_json::from_str(&input).expect("Invalid flash loan request");
            let result = build_flash_loan(req);
            serde_json::to_value(result).unwrap()
        }
        "evm-analyze" => {
            let hex_str: String = serde_json::from_str(&input).expect("Invalid hex string");
            evm_analyze(hex_str)
        }
        "yul-ir" => {
            let hex_str: String = serde_json::from_str(&input).expect("Invalid hex string");
            yul_ir(hex_str)
        }
        "storage-layout" => {
            let hex_str: String = serde_json::from_str(&input).expect("Invalid hex string");
            storage_layout(hex_str)
        }
        "abi-decode" => {
            let hex_str: String = serde_json::from_str(&input).expect("Invalid hex string");
            abi_decode(hex_str)
        }
        "security-scan" => {
            let hex_str: String = serde_json::from_str(&input).expect("Invalid hex string");
            security_scan(hex_str)
        }
        _ => {
            eprintln!("Unknown command: {}", args[1]);
            std::process::exit(1);
        }
    };

    println!("{}", serde_json::to_string(&result).unwrap());
}
