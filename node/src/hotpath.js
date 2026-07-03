// hotpath.js — bridge to the Rust hot-path binary (jdl-hotpath).
//
// The Rust crate is a stdin/stdout JSON filter, so interop is a plain child
// process: write a ScanRequest, read a ScanResult. No FFI, no native addon —
// the binary is built separately with `cargo build --release`.
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));

// Default location of the release binary within the repo; overridable via env.
export const HOTPATH_BIN =
  process.env.HOTPATH_BIN ||
  join(__dirname, '..', '..', 'rust', 'hotpath', 'target', 'release', 'jdl-hotpath');

export function hotpathAvailable() {
  return existsSync(HOTPATH_BIN);
}

/**
 * Run the Rust hot-path over a ScanRequest.
 * @param {{edges:Array,base:string,loan_usd:number,gas_usd:number,min_profit_usd?:number}} req
 * @returns {Promise<{opportunity:object|null,tokens:number,edges:number}>}
 */
// Max time (ms) to let the child run before we kill it. A pathological
// ScanRequest can make the bounded-DFS run long or a child hang; overridable
// via env.
export const HOTPATH_TIMEOUT_MS = Number(process.env.HOTPATH_TIMEOUT_MS || 15000);

export function scan(req) {
  return new Promise((resolve, reject) => {
    if (!hotpathAvailable()) {
      return reject(new Error(`hot-path binary not found at ${HOTPATH_BIN} ` +
        `(build it: cd rust/hotpath && cargo build --release)`));
    }
    const child = spawn(HOTPATH_BIN, [], { stdio: ['pipe', 'pipe', 'pipe'] });
    let out = '';
    let err = '';
    let settled = false;
    const done = (fn) => (arg) => { if (!settled) { settled = true; clearTimeout(timer); fn(arg); } };
    const ok = done(resolve);
    const fail = done(reject);

    // Kill a hung/pathological child so it doesn't leak; cleared on close.
    const timer = setTimeout(() => {
      child.kill();
      fail(new Error(`hot-path timed out after ${HOTPATH_TIMEOUT_MS}ms (killed)`));
    }, HOTPATH_TIMEOUT_MS);
    if (typeof timer.unref === 'function') timer.unref();

    child.stdout.on('data', (d) => (out += d));
    child.stderr.on('data', (d) => (err += d));
    // spawn failures
    child.on('error', fail);
    // A broken stdin pipe (e.g. the child exits before reading) emits an 'error'
    // on the stream; without this handler EPIPE crashes the whole process.
    child.stdin.on('error', fail);
    child.on('close', (code) => {
      try {
        const parsed = JSON.parse(out.trim());
        // The binary emits a valid empty result even on error (exit 1); surface
        // stderr only when we couldn't parse anything useful.
        if (code !== 0 && !parsed.opportunity && err) {
          return fail(new Error(err.trim()));
        }
        ok(parsed);
      } catch (e) {
        fail(new Error(`hot-path returned unparseable output: ${e.message}; stderr: ${err.trim()}`));
      }
    });
    child.stdin.write(JSON.stringify(req));
    child.stdin.end();
  });
}
