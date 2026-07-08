#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# termux-install.sh — ONE-COMMAND full Termux install for the JDL flash engine
#
# Run on a fresh Termux with a single line (no clone needed first):
#
#   curl -fsSL https://raw.githubusercontent.com/flipflowglobal/jdl-production-core/main/scripts/termux-install.sh | bash
#
# It performs the entire on-device setup, in order:
#   1. Verify this is Termux (Android)
#   2. pkg update + install the system packages the engine needs
#   3. Clone (or update) the repo into $JDL_DIR  (default: ~/projects/jdl-production-core)
#   4. Run setup.sh — venv at ~/.flash_venv, web3 6.x, `jdl` command, auto-wire ~/jdl/.env
#   5. Print next steps
#
# Env overrides:
#   JDL_DIR    where to clone the repo   (default: $HOME/projects/jdl-production-core)
#   JDL_BRANCH which branch to clone     (default: main)
#
# Idempotent: safe to re-run. If the repo is already cloned it does `git pull`
# instead of failing, then re-runs setup.sh (which itself is idempotent).
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

REPO_URL="https://github.com/flipflowglobal/jdl-production-core.git"
JDL_DIR="${JDL_DIR:-$HOME/projects/jdl-production-core}"
JDL_BRANCH="${JDL_BRANCH:-main}"

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'
step() { echo -e "${CYAN}▶ $1${RESET}"; }
ok()   { echo -e "${GREEN}✓ $1${RESET}"; }
warn() { echo -e "${YELLOW}⚠ $1${RESET}"; }
fail() { echo -e "${RED}✗ $1${RESET}"; exit 1; }

echo -e "${CYAN}${BOLD}"
cat << 'BANNER'
  JDL FLASH ENGINE · ONE-COMMAND TERMUX INSTALL
BANNER
echo -e "${RESET}"

# ── 1. Verify Termux ─────────────────────────────────────────────────────
if [ -z "${TERMUX_VERSION:-}" ] && [ ! -d "/data/data/com.termux" ]; then
    warn "This doesn't look like Termux."
    echo -e "  ${DIM}This one-liner targets Termux on Android. On Ubuntu/WSL/macOS,"
    echo -e "  clone the repo and run 'bash setup.sh' directly instead.${RESET}"
    fail "Not running under Termux — aborting."
fi
ok "Termux detected"

# ── 2. System packages ───────────────────────────────────────────────────
step "Updating Termux and installing system packages…"
pkg update -y && pkg upgrade -y
# python + git are essential; openssl/libffi back the crypto libs; clang/make
# let pip build the few wheels with no Android binary. setup.sh additionally
# pulls the prebuilt python-rpds-py / python-psutil wheels that can't compile
# from source on Android.
pkg install -y python git openssl libffi clang make
ok "System packages installed"

# ── 3. Clone or update the repo ──────────────────────────────────────────
if [ -d "$JDL_DIR/.git" ]; then
    step "Repo already present at $JDL_DIR — pulling latest…"
    git -C "$JDL_DIR" pull --ff-only || warn "git pull failed (local changes?) — continuing with existing checkout"
    ok "Repo updated"
else
    step "Cloning $REPO_URL → $JDL_DIR (branch $JDL_BRANCH)…"
    mkdir -p "$(dirname "$JDL_DIR")"
    git clone "$REPO_URL" "$JDL_DIR" --branch "$JDL_BRANCH" --single-branch --depth 1
    ok "Repo cloned"
fi

# ── 4. Run the platform installer ────────────────────────────────────────
step "Running setup.sh (venv, deps, jdl command, .env auto-wire)…"
bash "$JDL_DIR/setup.sh"

# ── 5. Assured-execution check ───────────────────────────────────────────
# Don't just claim success — prove the engine can actually run before saying so.
# termux-verify.sh gates on the install layer (venv, web3 import, jdl on PATH,
# smoke test). Non-fatal here: a failure prints its own remediation, and the
# user can re-run `bash scripts/termux-verify.sh --fix` at any time.
step "Verifying the install can actually execute…"
VERIFY_RC=0
bash "$JDL_DIR/scripts/termux-verify.sh" || VERIFY_RC=$?

# ── 6. Next steps ────────────────────────────────────────────────────────
echo
if [ "$VERIFY_RC" -eq 0 ]; then
    echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════╗${RESET}"
    echo -e "${GREEN}${BOLD}║   Termux install complete — execution ASSURED.   ║${RESET}"
    echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════╝${RESET}"
else
    echo -e "${YELLOW}${BOLD}╔══════════════════════════════════════════════════╗${RESET}"
    echo -e "${YELLOW}${BOLD}║   Install ran, but the verifier found issues.    ║${RESET}"
    echo -e "${YELLOW}${BOLD}║   See the ✗ lines above, or run:                 ║${RESET}"
    echo -e "${YELLOW}${BOLD}║   bash scripts/termux-verify.sh --fix            ║${RESET}"
    echo -e "${YELLOW}${BOLD}╚══════════════════════════════════════════════════╝${RESET}"
fi
echo
echo -e "  ${BOLD}Next steps:${RESET}"
echo -e "  1. ${CYAN}source ~/.flash_venv/bin/activate${RESET}   — activate the venv (once per shell)"
echo -e "  2. ${CYAN}jdl integrate${RESET}                       — check config; shows anything still unset"
echo -e "  3. ${CYAN}nano ~/jdl/.env${RESET}                     — add PRIVATE_KEY / ALCHEMY_ARB_KEY if flagged"
echo -e "  4. ${CYAN}jdl start flashloan${RESET}                 — launch the engine"
echo
echo -e "  ${DIM}Full walkthrough: $JDL_DIR/docs/TERMUX_WALKTHROUGH.md${RESET}"
echo
