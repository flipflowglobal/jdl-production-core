#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# run-all-tests.sh — the FULL cross-language test suite, in one command.
#
# Mirrors CI (.github/workflows/ci.yml), running all four jobs locally (plus the
# optional mainnet-fork tests, which CI doesn't run, when ARB_RPC_URL is set):
#   • python   — `jdl test`                     (blocking)
#   • rust     — `cargo test` + `cargo clippy`  (blocking)
#   • node     — `npm test`                     (blocking)
#   • solidity — Hardhat compile (blocking, mirrors CI) + fork tests (advisory:
#     they fork from a public RPC that can rate-limit)
#
# Ideal on UserLAnd / Ubuntu / WSL / macOS (glibc), where the whole polyglot
# stack runs. On Termux (Bionic libc) only the Python suite applies — Rust/Node/
# Foundry are skipped by design (see POLYGLOT.md), so there this reports the
# others as SKIP.
#
# Usage:
#   bash scripts/run-all-tests.sh            # run everything, print a summary
#   bash scripts/run-all-tests.sh --quick    # python + rust only (skip npm-heavy suites)
#   bash scripts/run-all-tests.sh --strict   # treat SKIP (missing toolchain) as failure
#
# Env:
#   ARB_RPC_URL   if set, also runs the contracts mainnet-fork tests (forge + hardhat)
#
# Exit code: 0 if every BLOCKING suite that ran passed; 1 otherwise. Solidity is
# advisory (mirrors CI's continue-on-error) and never fails the verdict on its own.
# ═══════════════════════════════════════════════════════════════════════════
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
VENV_DIR="${VENV_DIR:-$HOME/.flash_venv}"

QUICK=0; STRICT=0
for arg in "$@"; do
    case "$arg" in
        --quick)  QUICK=1 ;;
        --strict) STRICT=1 ;;
        # Print only the contiguous header comment block: skip the shebang, then
        # every leading `#` line up to the first non-comment line (stops before
        # the internal section-divider comments further down).
        -h|--help) awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "$0"; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'

# Parallel arrays of results: name / status (PASS|FAIL|SKIP) / whether blocking.
declare -a R_NAME R_STATUS R_BLOCKING
FAILED=0

hdr()    { echo; echo -e "${CYAN}${BOLD}━━ $1 ━━${RESET}"; }
record() { # name  status  blocking(1/0)
    R_NAME+=("$1"); R_STATUS+=("$2"); R_BLOCKING+=("$3")
    if [ "$2" = "FAIL" ] && [ "$3" = "1" ]; then FAILED=$((FAILED+1)); fi
    if [ "$2" = "SKIP" ] && [ "$3" = "1" ] && [ "$STRICT" = "1" ]; then FAILED=$((FAILED+1)); fi
}

echo -e "${CYAN}${BOLD}"
echo "  FULL TEST SUITE — JDL Production Core"
echo -e "${RESET}${DIM}  repo: $REPO_DIR${RESET}"

# ── 1. Python (blocking) ────────────────────────────────────────────────
# NB: some suites (test_cli.py) shell out to the bare `jdl` command, so the
# venv's bin/ must be on PATH — exactly what `source ~/.flash_venv/bin/activate`
# does. We replicate that here so the run works even without pre-activation.
hdr "python — jdl test"
py_status="SKIP"
if [ -x "$VENV_DIR/bin/jdl" ]; then
    if ( cd "$REPO_DIR" && export PATH="$VENV_DIR/bin:$PATH" && jdl test ); then py_status="PASS"; else py_status="FAIL"; fi
elif command -v jdl >/dev/null 2>&1; then
    if ( cd "$REPO_DIR" && jdl test ); then py_status="PASS"; else py_status="FAIL"; fi
elif python3 -c 'import jdl_flash' 2>/dev/null; then
    # Package imports under this python3 but `jdl` isn't on PATH. Don't fall back
    # to `python -m jdl_flash.cli test`: the suite includes test_cli.py, which
    # shells out to the literal `jdl` console-script (subprocess.run(["jdl",...])),
    # so it would fail with the very `jdl: not found` this runner guards against.
    # Instead put python's scripts dir (where pip installs the `jdl` entry point)
    # on PATH and run `jdl test`; if the console-script truly isn't installed,
    # SKIP with guidance rather than reporting a misleading FAIL.
    PY_SCRIPTS="$(python3 -c 'import sysconfig; print(sysconfig.get_path("scripts"))' 2>/dev/null)"
    if [ -n "$PY_SCRIPTS" ] && [ -x "$PY_SCRIPTS/jdl" ]; then
        if ( cd "$REPO_DIR" && export PATH="$PY_SCRIPTS:$PATH" && jdl test ); then py_status="PASS"; else py_status="FAIL"; fi
    else
        echo -e "  ${YELLOW}jdl_flash imports but the 'jdl' console-script isn't installed — run: pip install -e python/  (or bash setup.sh)${RESET}"
    fi
else
    echo -e "  ${YELLOW}jdl not installed — run: bash setup.sh  (or pip install -e python/)${RESET}"
fi
record "python" "$py_status" 1

# ── 2. Rust (blocking) ──────────────────────────────────────────────────
hdr "rust — cargo test + clippy"
if command -v cargo >/dev/null 2>&1 && [ -f "$REPO_DIR/rust/hotpath/Cargo.toml" ]; then
    command -v rustup >/dev/null 2>&1 && rustup component add clippy >/dev/null 2>&1 || true
    if ( cd "$REPO_DIR/rust/hotpath" && cargo test && cargo clippy --all-targets -- -D warnings ); then
        record "rust" PASS 1
    else
        record "rust" FAIL 1
    fi
else
    echo -e "  ${YELLOW}cargo not found (Termux/no-Rust) — skipping (fallbacks still work, see POLYGLOT.md)${RESET}"
    record "rust" SKIP 1
fi

# ── 3. Node (blocking) ──────────────────────────────────────────────────
if [ "$QUICK" = "1" ]; then
    hdr "node — SKIPPED (--quick)"
    record "node" SKIP 0
elif command -v npm >/dev/null 2>&1 && [ -f "$REPO_DIR/node/package.json" ]; then
    hdr "node — npm test"
    if ( cd "$REPO_DIR/node" && { npm ci || npm install; } && npm test ); then
        record "node" PASS 1
    else
        record "node" FAIL 1
    fi
else
    hdr "node — skipped (no npm)"
    echo -e "  ${YELLOW}npm not found — skipping${RESET}"
    record "node" SKIP 1
fi

# ── 4. Solidity compile (blocking — mirrors CI) ─────────────────────────
# The compile is blocking here just as it is in ci.yml. It used to be advisory in
# both places, which is how a dependency bump that broke `npm run compile` outright
# stayed invisible: the command exited 1 on every run and nothing ever reported it.
if [ "$QUICK" = "1" ]; then
    hdr "solidity — SKIPPED (--quick)"
    record "solidity (compile)" SKIP 0
elif command -v npm >/dev/null 2>&1 && [ -f "$REPO_DIR/contracts/package.json" ]; then
    hdr "solidity — Hardhat compile"
    if ( cd "$REPO_DIR/contracts" && { npm ci || npm install; } && npm run compile ); then
        record "solidity (compile)" PASS 1
    else
        echo -e "  ${RED}compile failed — this is a real build break, not a flake${RESET}"
        record "solidity (compile)" FAIL 1
    fi
    if [ -n "${ARB_RPC_URL:-}" ]; then
        hdr "solidity — Hardhat mainnet-fork tests (ARB_RPC_URL set)"
        if ( cd "$REPO_DIR/contracts" && npm run test:fork ); then
            record "solidity (hardhat fork)" PASS 0
        else
            record "solidity (hardhat fork)" FAIL 0
        fi
    fi
else
    hdr "solidity — Hardhat skipped (no npm)"
    record "solidity (compile)" SKIP 0
fi

# ── 4b. Foundry (forge) mainnet-fork tests — independent of npm/Hardhat ──
# forge test doesn't need npm, so gate it only on ARB_RPC_URL + forge (advisory).
# This runs even on a machine that has Foundry but no npm. foundry.toml reads
# ARB_RPC_URL from the environment for the fork.
if [ "$QUICK" != "1" ] && [ -n "${ARB_RPC_URL:-}" ]; then
    hdr "solidity — Foundry (forge) mainnet-fork tests"
    if command -v forge >/dev/null 2>&1 && [ -f "$REPO_DIR/contracts/foundry.toml" ]; then
        if ( cd "$REPO_DIR/contracts" && forge test ); then
            record "solidity (forge fork)" PASS 0
        else
            record "solidity (forge fork)" FAIL 0
        fi
    else
        echo -e "  ${YELLOW}forge not installed — skipping the Foundry fork suite${RESET}"
        record "solidity (forge fork)" SKIP 0
    fi
fi

# ── Summary ─────────────────────────────────────────────────────────────
echo; echo -e "${CYAN}${BOLD}━━ Summary ━━${RESET}"
i=0
while [ "$i" -lt "${#R_NAME[@]}" ]; do
    name="${R_NAME[$i]}"; status="${R_STATUS[$i]}"; blocking="${R_BLOCKING[$i]}"
    # "non-blocking" (not "advisory"): a suite is non-blocking either because it's
    # intrinsically advisory (the mainnet-fork suites, which depend on a reachable
    # public RPC) or because the user skipped it with --quick (e.g. node) — the tag
    # must fit both cases.
    tag=""; [ "$blocking" = "0" ] && tag=" ${DIM}(non-blocking)${RESET}"
    case "$status" in
        PASS) echo -e "  ${GREEN}✓ PASS${RESET}  $name$tag" ;;
        FAIL) echo -e "  ${RED}✗ FAIL${RESET}  $name$tag" ;;
        SKIP) echo -e "  ${YELLOW}○ SKIP${RESET}  $name$tag" ;;
    esac
    i=$((i+1))
done

echo
if [ "$FAILED" -eq 0 ]; then
    echo -e "${GREEN}${BOLD}  ✓ FULL SUITE GREEN — every blocking suite that ran passed.${RESET}"
    exit 0
fi
echo -e "${RED}${BOLD}  ✗ $FAILED blocking suite(s) failed$([ "$STRICT" = "1" ] && echo " (or skipped, under --strict)").${RESET}"
exit 1
