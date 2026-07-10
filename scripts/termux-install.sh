#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# termux-install.sh — ONE-COMMAND full Termux install for the JDL flash engine
#
# IMPORTANT: this repo is PRIVATE, so you cannot fetch this script with a plain
# `curl … raw.githubusercontent.com` (raw returns 404 for unauthenticated
# requests to private repos — the pipe is empty and nothing runs). Bootstrap
# with `git clone` instead, which uses your GitHub credentials:
#
#   pkg install -y git && \
#   git clone https://github.com/flipflowglobal/jdl-production-core.git ~/projects/jdl-production-core && \
#   bash ~/projects/jdl-production-core/scripts/termux-install.sh
#
# (git clone on a private repo needs GitHub auth on the device — a Personal
# Access Token entered at the HTTPS password prompt, or `gh auth login`, or an
# SSH key with the git@ URL.)
#
# Once you have the repo, this script performs the rest of the setup, in order:
#   1. Verify this is Termux (Android)
#   2. Install the system packages the engine needs (non-interactive)
#   3. Clone (or update) the repo into $JDL_DIR  (default: ~/projects/jdl-production-core)
#   4. Run setup.sh — venv at ~/.flash_venv, web3 6.x, `jdl` command, auto-wire ~/jdl/.env
#   5. Verify the install can actually execute
#
# Env overrides:
#   JDL_DIR    where to clone the repo   (default: $HOME/projects/jdl-production-core)
#   JDL_BRANCH which branch to clone     (default: main)
#
# Idempotent: safe to re-run. If the repo is already cloned it does `git pull`
# instead of failing, then re-runs setup.sh (which itself is idempotent).
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

REPO_URL="${JDL_REPO_URL:-https://github.com/flipflowglobal/jdl-production-core.git}"
JDL_DIR="${JDL_DIR:-$HOME/projects/jdl-production-core}"
JDL_BRANCH="${JDL_BRANCH:-main}"

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'
step() { echo -e "${CYAN}▶ $1${RESET}"; }
ok()   { echo -e "${GREEN}✓ $1${RESET}"; }
warn() { echo -e "${YELLOW}⚠ $1${RESET}"; }
fail() { echo -e "${RED}✗ $1${RESET}"; exit 1; }

# Termux from its RUNTIME env, not the /data/data/com.termux dir (a co-installed
# UserLAnd can see that host dir and would otherwise wrongly pass this guard).
is_termux() {
    [ -n "${TERMUX_VERSION:-}" ] && return 0
    case "${PREFIX:-}" in *com.termux*) return 0 ;; esac
    case "$(command -v pkg 2>/dev/null)" in */com.termux/*) return 0 ;; esac
    return 1
}

echo -e "${CYAN}${BOLD}"
cat << 'BANNER'
  JDL FLASH ENGINE · ONE-COMMAND TERMUX INSTALL
BANNER
echo -e "${RESET}"

# ── 1. Verify Termux ─────────────────────────────────────────────────────
if ! is_termux; then
    warn "This doesn't look like Termux."
    echo -e "  ${DIM}This installer targets Termux on Android. On UserLAnd/Ubuntu/WSL/macOS,"
    echo -e "  run 'bash scripts/userland-setup.sh' (or 'bash setup.sh') instead.${RESET}"
    fail "Not running under Termux — aborting."
fi
ok "Termux detected"

# ── 2. System packages ───────────────────────────────────────────────────
# noninteractive frontend + non-fatal update: `pkg upgrade` is intentionally
# NOT run here — it can pop a dpkg/apt conffile prompt that hangs forever when
# there's no interactive tty (the classic Termux "install just hangs"). We only
# need `pkg install`, which `-y` makes non-interactive.
export DEBIAN_FRONTEND=noninteractive
step "Installing system packages…"
pkg update -y || warn "pkg update had issues — continuing to install anyway"
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
    if ! git clone "$REPO_URL" "$JDL_DIR" --branch "$JDL_BRANCH" --single-branch --depth 1; then
        echo
        warn "git clone failed. This repo is PRIVATE, so cloning needs GitHub auth on this device:"
        echo -e "  ${DIM}• HTTPS: create a Personal Access Token (repo scope) and paste it at the"
        echo -e "    password prompt, or run 'gh auth login' if you have the gh CLI.${RESET}"
        echo -e "  ${DIM}• SSH:   add an SSH key to GitHub, then re-run with"
        echo -e "    JDL_REPO_URL=git@github.com:flipflowglobal/jdl-production-core.git${RESET}"
        fail "Could not clone the repository — see auth guidance above."
    fi
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
