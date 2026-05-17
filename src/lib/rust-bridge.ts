import { execSync } from "child_process";
import path from "path";

const RUST_CLI = process.env.RUST_CORE_PATH || path.join(__dirname, "../../target/release/jdl-core");

function call(command: string, input: any): any {
  try {
    const result = execSync(`"${RUST_CLI}" ${command}`, {
      input: JSON.stringify(input),
      encoding: "utf-8",
      timeout: 30000,
    });
    return JSON.parse(result);
  } catch (err: any) {
    console.error(`Rust CLI error (${command}):`, err.message);
    return null;
  }
}

export function findRoutes(pools: any[]): any {
  return call("find-routes", { pools });
}

export function quoteSwap(chain: string, tokenIn: string, tokenOut: string, amount: string): any {
  return call("quote-swap", { chain, tokenIn, tokenOut, amount });
}

export function buildFlashLoan(chain: string, asset: string, amount: string, steps: any[]): any {
  return call("flash-loan", { chain, asset, amount, steps });
}

export function analyzeBytecode(hex: string): any {
  return call("evm-analyze", hex);
}

export default { findRoutes, quoteSwap, buildFlashLoan, analyzeBytecode };
