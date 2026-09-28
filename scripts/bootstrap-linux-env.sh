#!/usr/bin/env bash
#
# bootstrap-linux-env.sh — provision the JDL production-core development
# toolchain on a Linux box (bare metal, VPS, container, or WSL2).
#
# SCOPE
#   setup.sh at the repo root targets Termux/Android and is stdlib-Python by
#   design. This script targets a normal Linux host and installs the four
#   toolchains that .github/workflows/ci.yml actually gates on — Python,
#   Node, Rust, Solidity — plus the advisory scanners from security.yml.
#
# WHY THIS FILE EXISTS RATHER THAN "JUST USE CI"
#   On WSL2 the Windows interop layer injects /mnt/c/... directories into PATH.
#   `npm` resolves to a Windows .cmd/.exe that cannot execute inside the WSL
#   filesystem, `node` may not resolve at all, and `cargo` resolves to the
#   Windows rustup shim. A plain `npm install` therefore fails in ways that
#   look like dependency problems but are PATH shadowing. The fix is native
#   Linux binaries plus a profile.d snippet that PREPENDS them (prepending is
#   required — appending loses to the interop PATH).
#
# IDEMPOTENT
#   Every step detects an already-correct install and skips it. Safe to re-run;
#   safe to re-run after a partial failure.
#
# SECURITY
#   Downloads are SHA-256 verified against a published manifest before use.
#   This script contains no credentials, reads no .env, and never deploys a
#   contract. It does not run the project's deployment scripts.
#
# USAGE
#   ./scripts/bootstrap-linux-env.sh                 # full provision
#   ./scripts/bootstrap-linux-env.sh --verify-only   # report, change nothing
#   ./scripts/bootstrap-linux-env.sh --run-tests     # also run the CI gates
#   ./scripts/bootstrap-linux-env.sh --help
#
#   Steps: --skip-system --skip-node --skip-rust --skip-foundry
#          --skip-python --skip-node-deps --skip-security

set -euo pipefail

# ---------------------------------------------------------------------------
# Pinned versions. These match what the environment was provisioned and
# verified against; CI is the authority if one of these drifts.
# ---------------------------------------------------------------------------
readonly NODE_VERSION="v22.23.3"      # 22.x LTS; satisfies node/ ">=18" and Hardhat 2.x
readonly FOUNDRY_VERSION="v1.8.3"
readonly GITLEAKS_VERSION="8.18.4"    # matches security.yml's pin exactly
readonly SOLC_VERSION="0.8.20"        # matches hardhat.config.js / security.yml
readonly RUST_TOOLCHAIN="stable"

readonly SEC_VENV="${HOME}/.venvs/jdl-sec-tools"
readonly PROFILE_SNIPPET="/etc/profile.d/jdl-dev.sh"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
if [[ -t 1 ]]; then
    _C_RESET=$'\033[0m'; _C_BLUE=$'\033[34m'; _C_GREEN=$'\033[32m'
    _C_YELLOW=$'\033[33m'; _C_RED=$'\033[31m'
else
    _C_RESET=""; _C_BLUE=""; _C_GREEN=""; _C_YELLOW=""; _C_RED=""
fi

log()  { printf '%s==>%s %s\n' "$_C_BLUE"   "$_C_RESET" "$*"; }
ok()   { printf '%s  ok%s %s\n' "$_C_GREEN"  "$_C_RESET" "$*"; }
warn() { printf '%swarn%s %s\n' "$_C_YELLOW" "$_C_RESET" "$*" >&2; }
die()  { printf '%serror%s %s\n' "$_C_RED"    "$_C_RESET" "$*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

# ---------------------------------------------------------------------------
# Privilege
# ---------------------------------------------------------------------------
SUDO=""
if [[ ${EUID} -ne 0 ]]; then
    have sudo || die "needs root: no sudo on PATH. Re-run as root or install sudo."
    SUDO="sudo"
fi

# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------
RUN_TESTS=0
VERIFY_ONLY=0
SKIP_SYSTEM=0; SKIP_NODE=0; SKIP_RUST=0; SKIP_FOUNDRY=0
SKIP_PYTHON=0; SKIP_NODE_DEPS=0; SKIP_SECURITY=0

usage() {
    # Print the file's leading comment block (the usage docs), stopping at the
    # first line that is not a comment. A fixed line range would drift whenever
    # the header is edited.
    awk 'NR > 1 { if ($0 !~ /^#/) exit; sub(/^# ?/, ""); print }' "$0"
    cat <<'EOF'

Options:
  --verify-only      Report toolchain status; make no changes.
  --run-tests        Also run the four CI gates after provisioning.
  --skip-system      Skip apt packages.
  --skip-node        Skip the Node.js runtime.
  --skip-rust        Skip the Rust toolchain.
  --skip-foundry     Skip Foundry.
  --skip-python      Skip the project venv and editable install.
  --skip-node-deps   Skip npm install in node/ and contracts/.
  --skip-security    Skip slither / pip-audit / solc-select / gitleaks.
  -h, --help         This text.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --verify-only)   VERIFY_ONLY=1 ;;
        --run-tests)     RUN_TESTS=1 ;;
        --skip-system)   SKIP_SYSTEM=1 ;;
        --skip-node)     SKIP_NODE=1 ;;
        --skip-rust)     SKIP_RUST=1 ;;
        --skip-foundry)  SKIP_FOUNDRY=1 ;;
        --skip-python)   SKIP_PYTHON=1 ;;
        --skip-node-deps) SKIP_NODE_DEPS=1 ;;
        --skip-security) SKIP_SECURITY=1 ;;
        -h|--help)       usage; exit 0 ;;
        *)               die "unknown option: $1 (try --help)" ;;
    esac
    shift
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO_ROOT

case "$(uname -m)" in
    x86_64|amd64)  NODE_ARCH="x64";   DL_ARCH="amd64" ;;
    aarch64|arm64) NODE_ARCH="arm64"; DL_ARCH="arm64" ;;
    *) die "unsupported architecture: $(uname -m)" ;;
esac
readonly NODE_ARCH DL_ARCH

# ===========================================================================
# Download + integrity helpers
# ===========================================================================
WORKDIR=""
cleanup() {
    if [[ -n "$WORKDIR" && -d "$WORKDIR" ]]; then
        rm -rf "$WORKDIR"
    fi
    # Must return 0 explicitly. In bash the EXIT trap's final status becomes
    # the script's exit status, so a trailing failed `[[ ]]` test here would
    # turn a successful run into exit 1.
    return 0
}
trap cleanup EXIT

ensure_workdir() {
    [[ -n "$WORKDIR" && -d "$WORKDIR" ]] || WORKDIR="$(mktemp -d)"
}

fetch() {
    local url="$1" dest="$2"
    log "downloading $(basename "$dest")"
    curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 20 -o "$dest" "$url" \
        || die "download failed: $url"
}

verify_sha256() {
    local file="$1" expected="$2" actual
    actual="$(sha256sum "$file" | awk '{print $1}')"
    [[ "$actual" == "$expected" ]] \
        || die "checksum mismatch for $(basename "$file"): expected $expected, got $actual"
    ok "checksum verified: $(basename "$file")"
}

# ===========================================================================
# Step 1 — system packages
# ===========================================================================
install_system_packages() {
    log "system packages"
    local -a pkgs=(
        build-essential pkg-config libssl-dev ca-certificates curl git
        unzip xz-utils jq shellcheck sqlite3 python3-venv python3-dev
        python3-pip lsb-release gnupg
    )
    $SUDO apt-get update -qq
    DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -qq --no-install-recommends "${pkgs[@]}"
    ok "apt packages installed"
}

# ===========================================================================
# Step 2 — Node.js (native Linux; fixes the WSL interop shadowing)
# ===========================================================================
install_node() {
    log "Node.js ${NODE_VERSION}"
    if [[ -x /usr/local/lib/nodejs/bin/node ]] \
       && [[ "$(/usr/local/lib/nodejs/bin/node --version)" == "${NODE_VERSION}" ]]; then
        ok "node ${NODE_VERSION} already installed"
    else
        ensure_workdir
        local tarball="node-${NODE_VERSION}-linux-${NODE_ARCH}.tar.xz"
        local dest="${WORKDIR}/${tarball}"
        fetch "https://nodejs.org/dist/${NODE_VERSION}/${tarball}" "$dest"
        fetch "https://nodejs.org/dist/${NODE_VERSION}/SHASUMS256.txt" "${WORKDIR}/SHASUMS256.txt"
        local expected
        expected="$(awk -v f="$tarball" '$2 == f { print $1 }' "${WORKDIR}/SHASUMS256.txt")"
        [[ -n "$expected" ]] || die "no checksum published for ${tarball}"
        verify_sha256 "$dest" "$expected"

        $SUDO rm -rf /usr/local/lib/nodejs
        $SUDO mkdir -p /usr/local/lib/nodejs
        $SUDO tar -xJf "$dest" -C /usr/local/lib/nodejs --strip-components=1
        # Remove any stale shims before relinking, otherwise the Windows-inherited
        # node in /usr/local/bin would keep winning.
        $SUDO rm -f /usr/local/bin/node /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack
        local b
        for b in node npm npx corepack; do
            $SUDO ln -sf "/usr/local/lib/nodejs/bin/${b}" "/usr/local/bin/${b}"
        done
        ok "node $(/usr/local/bin/node --version) installed"
    fi
}

# ===========================================================================
# Step 3 — Rust
# ===========================================================================
install_rust() {
    log "Rust toolchain (${RUST_TOOLCHAIN})"
    have rustup || die "rustup not found; install it from https://rustup.rs first"

    rustup set profile default
    rustup toolchain install "$RUST_TOOLCHAIN" --profile default --component clippy,rustfmt
    rustup default "$RUST_TOOLCHAIN"

    # The distro rustup proxy (e.g. /usr/bin/cargo) installs toolchains under
    # ~/.rustup but creates no ~/.cargo/bin shims, so `cargo install` has
    # nowhere to write. Create them, and prepend ~/.cargo/bin to PATH.
    local tc
    tc="$(find "${HOME}/.rustup/toolchains" -mindepth 1 -maxdepth 1 -type d | sort | head -1)"
    [[ -d "$tc/bin" ]] || die "no rustup toolchain directory found under ~/.rustup/toolchains"

    mkdir -p "${HOME}/.cargo/bin"
    local b
    for b in cargo rustc rustdoc cargo-clippy clippy-driver cargo-fmt rustfmt; do
        [[ -e "${tc}bin/${b}" ]] && ln -sf "${tc}bin/${b}" "${HOME}/.cargo/bin/${b}"
    done
    for b in rustup cargo rustc; do
        have "/usr/bin/${b}" && ln -sf "/usr/bin/${b}" "${HOME}/.cargo/bin/${b}"
    done
    ok "rustc $(rustc --version | awk '{print $2}'), clippy $(cargo clippy --version | awk '{print $2}')"
}

# ===========================================================================
# Step 4 — Foundry
# ===========================================================================
install_foundry() {
    log "Foundry ${FOUNDRY_VERSION}"
    # `forge --version` prints a five-line banner, so the version must be taken
    # from the first line only — parsing the whole output yields a multi-line
    # string that never matches, which would reinstall on every run.
    local installed
    installed="$(forge --version 2>/dev/null | head -1 | awk '{print $3}' || true)"
    if [[ "$installed" == "${FOUNDRY_VERSION#v}" ]]; then
        ok "forge ${FOUNDRY_VERSION} already installed"
        return
    fi
    ensure_workdir
    local asset="foundry_${FOUNDRY_VERSION}_linux_${DL_ARCH}.tar.gz"
    fetch "https://github.com/foundry-rs/foundry/releases/download/${FOUNDRY_VERSION}/${asset}" \
          "${WORKDIR}/foundry.tar.gz"
    fetch "https://github.com/foundry-rs/foundry/releases/download/${FOUNDRY_VERSION}/foundry_${FOUNDRY_VERSION}_linux_${DL_ARCH}.sha256" \
          "${WORKDIR}/foundry.sha256"
    # The published .sha256 file contains the bare digest.
    verify_sha256 "${WORKDIR}/foundry.tar.gz" "$(awk '{print $1}' "${WORKDIR}/foundry.sha256")"

    mkdir -p "${WORKDIR}/foundry"
    tar -xzf "${WORKDIR}/foundry.tar.gz" -C "${WORKDIR}/foundry"
    $SUDO mkdir -p /usr/local/lib/foundry
    $SUDO cp "${WORKDIR}/foundry/"* /usr/local/lib/foundry/
    $SUDO chmod +x /usr/local/lib/foundry/*
    local b
    for b in forge cast anvil chisel solar; do
        [[ -e "/usr/local/lib/foundry/${b}" ]] && $SUDO ln -sf "/usr/local/lib/foundry/${b}" "/usr/local/bin/${b}"
    done
    ok "forge $(forge --version | awk '{print $3}') installed"
}

# ===========================================================================
# Step 5 — PATH profile (the actual WSL fix)
# ===========================================================================
write_path_profile() {
    log "PATH profile (${PROFILE_SNIPPET})"
    ensure_workdir
    cat > "${WORKDIR}/jdl-dev.sh" <<'PROFILE'
# JDL production-core development toolchain
#
# PREPENDED, not appended: on WSL2 the interop layer injects
# /mnt/c/Program Files/nodejs, /mnt/c/Users/<user>/.cargo/bin and the Windows
# Python into PATH. Those shims shadow the real toolchain (the Windows npm
# cannot execute inside WSL), so the native paths must come first.

if [ -d "$HOME/.cargo/bin" ]; then PATH="$HOME/.cargo/bin:$PATH"; fi
if [ -d "$HOME/.foundry/bin" ]; then PATH="$HOME/.foundry/bin:$PATH"; fi
if [ -d "/usr/local/lib/nodejs/bin" ]; then PATH="/usr/local/lib/nodejs/bin:$PATH"; fi
if [ -d "$HOME/.local/bin" ]; then PATH="$HOME/.local/bin:$PATH"; fi
export PATH
PROFILE
    $SUDO install -m 0644 "${WORKDIR}/jdl-dev.sh" "$PROFILE_SNIPPET"

    # Also source it from .bashrc so non-login interactive shells get it too.
    if ! grep -q "$PROFILE_SNIPPET" "${HOME}/.bashrc" 2>/dev/null; then
        {
            printf '\n# JDL production-core dev toolchain (native Linux, shadows WSL Windows shims)\n'
            printf 'if [ -f %s ]; then . %s; fi\n' "$PROFILE_SNIPPET" "$PROFILE_SNIPPET"
        } >> "${HOME}/.bashrc"
        ok "sourced from ~/.bashrc"
    fi
    ok "written"
}

# ===========================================================================
# Step 6 — Python project venv
# ===========================================================================
install_python() {
    log "Python project venv (.flash_venv)"
    local venv="${REPO_ROOT}/.flash_venv"
    local py="${venv}/bin/python3"

    if [[ ! -x "$py" ]]; then
        python3 -m venv "$venv" || die "failed to create venv at ${venv}"
    fi
    "$py" -m pip install --quiet --upgrade pip setuptools wheel

    # `pip install -e python/` installs the jdl / flashloan / flashpro console
    # scripts. requirements_flash.txt pulls the on-chain runtime deps.
    "$py" -m pip install --quiet -e "${REPO_ROOT}/python/"
    "$py" -m pip install --quiet -r "${REPO_ROOT}/python/requirements_flash.txt"
    "$py" -m pip install --quiet Cython

    # Expose only the console scripts globally. Putting the whole venv on PATH
    # would shadow system python3/pip for every other project on the host.
    mkdir -p "${HOME}/.local/bin"
    local b
    for b in jdl flashloan flashpro; do
        [[ -e "${venv}/bin/${b}" ]] && ln -sf "${venv}/bin/${b}" "${HOME}/.local/bin/${b}"
    done
    ok "venv ready: $("$py" --version), web3 $("$py" -c 'import web3; print(web3.__version__)')"
}

# ===========================================================================
# Step 7 — Rust build + Python native binding
# ===========================================================================
build_rust_and_native() {
    log "Rust hot-path build (release)"
    local crate="${REPO_ROOT}/rust/hotpath"
    ( cd "$crate" && cargo build --release ) || die "cargo build --release failed in ${crate}"
    [[ -x "${crate}/target/release/jdl-hotpath" ]] || die "CLI binary not produced"
    local so
    so="$(find "${crate}/target/release" -maxdepth 1 -name '*.so' | head -1)"
    if [[ -n "$so" ]]; then
        ok "jdl-hotpath + $(basename "$so")"
    else
        warn "no .so produced — jdl_native will fall back to ctypes/subprocess/python"
    fi

    log "Cython native binding (python/jdl_native)"
    local py="${REPO_ROOT}/.flash_venv/bin/python3"
    [[ -x "$py" ]] || die "project venv missing; run without --skip-python"
    ( cd "${REPO_ROOT}/python/jdl_native" && "$py" setup.py build_ext --inplace ) \
        || warn "Cython build failed — the ctypes/subprocess/python backends still work (POLYGLOT.md)"
}

# ===========================================================================
# Step 8 — npm dependencies
# ===========================================================================
install_node_deps() {
    log "npm dependencies"
    local d
    for d in node contracts; do
        [[ -f "${REPO_ROOT}/${d}/package.json" ]] || { warn "no package.json in ${d}/, skipping"; continue; }
        log "  ${d}/"
        ( cd "${REPO_ROOT}/${d}" && { [[ -f package-lock.json ]] && npm ci || npm install; } ) \
            || die "npm install failed in ${d}/"
    done
    ok "npm dependencies installed"
}

# ===========================================================================
# Step 9 — security tooling
# ===========================================================================
install_security_tooling() {
    log "security tooling (slither, pip-audit, solc-select, gitleaks)"
    if [[ ! -d "$SEC_VENV" ]]; then
        python3 -m venv "$SEC_VENV"
    fi
    local sp="${SEC_VENV}/bin/python3"
    "$sp" -m pip install --quiet --upgrade pip
    "$sp" -m pip install --quiet pip-audit slither-analyzer solc-select

    # Pin solc to the version hardhat.config.js / security.yml use, so slither
    # and any direct solc invocation agree with the compiler CI uses.
    "$SEC_VENV/bin/solc-select" install "$SOLC_VERSION" || warn "solc-select install failed"
    "$SEC_VENV/bin/solc-select" use "$SOLC_VERSION"    || warn "solc-select use failed"

    mkdir -p "${HOME}/.local/bin"
    local b
    for b in pip-audit solc-select slither crytic-compile \
             slither-check-erc slither-check-upgradeability; do
        [[ -x "${SEC_VENV}/bin/${b}" ]] && ln -sf "${SEC_VENV}/bin/${b}" "${HOME}/.local/bin/${b}"
    done
    ok "python security tools installed"

    # gitleaks gates merges in security.yml, so it is installed system-wide
    # rather than into the venv. security.yml pins 8.18.4; the release publishes
    # a combined checksums.txt (the per-file .sha256 URL 404s).
    if have gitleaks; then
        ok "gitleaks $(gitleaks version) already installed"
    else
        ensure_workdir
        fetch "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" \
              "${WORKDIR}/gitleaks.tar.gz"
        fetch "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_checksums.txt" \
              "${WORKDIR}/gitleaks_checksums.txt"
        verify_sha256 "${WORKDIR}/gitleaks.tar.gz" \
            "$(awk -v f="gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" '$2 == f { print $1 }' "${WORKDIR}/gitleaks_checksums.txt")"
        tar -xzf "${WORKDIR}/gitleaks.tar.gz" -C "${WORKDIR}" gitleaks
        $SUDO install -m 0755 "${WORKDIR}/gitleaks" /usr/local/bin/gitleaks
        ok "gitleaks $(gitleaks version) installed"
    fi
}

# ===========================================================================
# Verification
# ===========================================================================
verify() {
    log "verification"
    local rc=0
    local name cmd vargs hint resolved row

    # The table is fed on fd 3, not stdin. On stdin, the first version probe
    # that reads stdin swallows the rest of the table and the loop stops early.
    # Format: name|command|version-args|path-hint
    while IFS='|' read -r name cmd vargs hint <&3; do
        [[ -n "$name" ]] || continue
        resolved=""
        if have "$cmd"; then
            resolved="$(command -v "$cmd")"
            # Reject anything living under /mnt/c — that is a Windows shim.
            if [[ "$resolved" == /mnt/c/* ]]; then
                printf '  %-12s %-46s %sWINDOWS SHIM%s\n' "$name" "$resolved" "$_C_RED" "$_C_RESET"
                rc=1; continue
            fi
        else
            printf '  %-12s %-46s %sMISSING%s\n' "$name" "${hint:-(not on PATH)}" "$_C_RED" "$_C_RESET"
            rc=1; continue
        fi
        # Not every tool uses --version (gitleaks uses a `version` subcommand),
        # and a non-zero exit here is informational, not fatal.
        # shellcheck disable=SC2086  # vargs is intentionally word-split
        row="$("$cmd" $vargs 2>&1 | head -1 || true)"
        printf '  %-12s %-46s %s\n' "$name" "$resolved" "$row"
    done 3<<'TOOLS'
python3|python3|--version|
node|node|--version|
npm|npm|--version|
cargo|cargo|--version|
rustc|rustc|--version|
forge|forge|--version|
solc|solc|--version|
slither|slither|--version|
pip-audit|pip-audit|--version|
gitleaks|gitleaks|version|
sqlite3|sqlite3|--version|
jq|jq|--version|
shellcheck|shellcheck|--version|
jdl|jdl|--help|
TOOLS

    # Artifacts that must exist for the cross-language stack to function.
    log "build artifacts"
    local art
    for art in \
        "${REPO_ROOT}/rust/hotpath/target/release/jdl-hotpath" \
        "${REPO_ROOT}/rust/hotpath/target/release/libjdl_hotpath.so" \
        "${REPO_ROOT}/node/node_modules" \
        "${REPO_ROOT}/contracts/node_modules" \
        "${REPO_ROOT}/.flash_venv/bin/python3" ; do
        if [[ -e "$art" ]]; then
            ok "$(basename "$art")"
        else
            printf '  %-46s %sMISSING%s\n' "$(basename "$art")" "$_C_RED" "$_C_RESET"
            rc=1
        fi
    done
    return $rc
}

# ===========================================================================
# CI gates
# ===========================================================================
run_tests() {
    log "CI gate: python (jdl test)"
    ( cd "$REPO_ROOT" && jdl test ) || die "python suite failed"

    log "CI gate: rust (cargo test + clippy)"
    ( cd "${REPO_ROOT}/rust/hotpath" && cargo test ) || die "cargo test failed"
    ( cd "${REPO_ROOT}/rust/hotpath" && cargo clippy --all-targets -- -D warnings ) || die "clippy failed"

    log "CI gate: node (npm test)"
    ( cd "${REPO_ROOT}/node" && npm test ) || die "npm test failed"

    log "CI gate: solidity (hardhat compile)"
    ( cd "${REPO_ROOT}/contracts" && npm run compile ) || die "hardhat compile failed"
    ok "all four CI gates passed"
}

# ===========================================================================
# main
# ===========================================================================
# Load the profile snippet into THIS shell so that `verify` sees exactly the
# PATH a user gets in a fresh login shell. This must not be a subshell or a
# `bash -lc`: REPO_ROOT and the colour variables are not exported, so a child
# process would see empty paths and report every artifact as MISSING.
load_profile() {
    if [[ -f "$PROFILE_SNIPPET" ]]; then
        # shellcheck disable=SC1090  # path is a variable by design
        . "$PROFILE_SNIPPET"
    fi
}

main() {
    if [[ $VERIFY_ONLY -eq 1 ]]; then
        load_profile
        verify
        exit $?
    fi

    [[ $SKIP_SYSTEM -eq 0 ]]     && install_system_packages
    [[ $SKIP_NODE -eq 0 ]]       && install_node
    [[ $SKIP_RUST -eq 0 ]]       && install_rust
    [[ $SKIP_FOUNDRY -eq 0 ]]    && install_foundry
    write_path_profile
    [[ $SKIP_PYTHON -eq 0 ]]     && install_python
    [[ $SKIP_RUST -eq 0 ]]       && build_rust_and_native
    [[ $SKIP_NODE_DEPS -eq 0 ]]  && install_node_deps
    [[ $SKIP_SECURITY -eq 0 ]]  && install_security_tooling

    log "verifying"
    load_profile
    verify

    if [[ $RUN_TESTS -eq 1 ]]; then
        run_tests
    else
        log "skipping CI gates (pass --run-tests to include them)"
    fi
    log "done"
}

main "$@"
