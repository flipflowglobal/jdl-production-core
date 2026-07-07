# setup.ps1 — JDL Flash Loan Engine setup for native Windows PowerShell.
#
# `jdl install` (python/jdl_flash/cli.py's cmd_setup) picks this script
# automatically on real Windows (sys.platform starts with "win"); every other
# platform — Termux, UserLAnd, WSL, Ubuntu/Linux, macOS — uses setup.sh
# instead, because they all have a real POSIX shell.
#
# Foundry's official installer (foundryup) targets Linux/macOS/WSL, not
# native Windows — this script builds everything else, checks for `forge`,
# and points to WSL if it isn't found, rather than trying to fake an install
# that upstream doesn't support here.
$ErrorActionPreference = "Continue"

function Step($msg)  { Write-Host "> $msg" -ForegroundColor Cyan }
function Ok($msg)    { Write-Host "  OK: $msg" -ForegroundColor Green }
function WarnMsg($msg) { Write-Host "  WARN: $msg" -ForegroundColor Yellow }
function Info($msg)  { Write-Host "  $msg" -ForegroundColor DarkGray }
function Fail($msg)  { Write-Host "  FAIL: $msg" -ForegroundColor Red; exit 1 }

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent $ScriptDir
$VenvDir   = Join-Path $env:USERPROFILE ".flash_venv"
$EnvDir    = Join-Path $env:USERPROFILE "jdl"
$EnvFile   = Join-Path $EnvDir ".env"

Write-Host "  Platform: Windows (native PowerShell)" -ForegroundColor Magenta
Write-Host ""

# ── 1. Python ──────────────────────────────────────────────────
Step "Checking Python..."
$python = $null
foreach ($cand in @("python", "python3", "py")) {
    if (Get-Command $cand -ErrorAction SilentlyContinue) { $python = $cand; break }
}
if (-not $python) {
    Fail "Python not found. Install from https://www.python.org/downloads/ or: winget install Python.Python.3.12"
}
Ok "Python found ($python)"

# ── 2. Virtual environment ──────────────────────────────────────
Step "Setting up virtual environment at $VenvDir..."
if (-not (Test-Path $VenvDir)) {
    & $python -m venv $VenvDir
    Ok "Virtual environment created"
} else {
    Ok "Virtual environment already exists"
}
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip    = Join-Path $VenvDir "Scripts\pip.exe"

# ── 3. Python dependencies ──────────────────────────────────────
Step "Installing Python dependencies..."
& $VenvPip install --quiet --upgrade pip
& $VenvPip install --quiet -r (Join-Path $RepoRoot "python\requirements_flash.txt")
& $VenvPip install --quiet --no-deps --upgrade "parsimonious>=0.10"
& $VenvPip install --quiet -e (Join-Path $RepoRoot "python")
Ok "Dependencies installed; 'jdl' command ready inside $VenvDir\Scripts"

# ── 3b. Node.js / npm — contracts (Hardhat/solc) + node/ hotpath ──
Step "Checking Node.js..."
if (Get-Command npm -ErrorAction SilentlyContinue) {
    Ok "npm found ($(npm --version))"
    foreach ($d in @("contracts", "node")) {
        $dir = Join-Path $RepoRoot $d
        if (Test-Path (Join-Path $dir "package.json")) {
            Step "npm install in $d..."
            Push-Location $dir
            npm install --no-audit --no-fund --quiet
            if ($LASTEXITCODE -eq 0) { Ok "$d dependencies installed" } else { WarnMsg "$d npm install failed (non-fatal)" }
            Pop-Location
        }
    }
} else {
    WarnMsg "npm not found — skipping contracts/node dependency install."
    Info "Install with: winget install OpenJS.NodeJS.LTS"
}

# ── 3c. Foundry (forge/cast) — Solidity toolchain ────────────────
Step "Checking Foundry..."
if (Get-Command forge -ErrorAction SilentlyContinue) {
    Ok "forge found"
} else {
    WarnMsg "forge not found. Foundry's installer targets Linux/macOS/WSL, not native Windows."
    Info "Easiest path: install WSL (wsl --install), then run setup.sh inside it, or:"
    Info "  use an existing Linux/macOS machine for contract compilation/deploy only."
}

# ── 3d. Rust — jdl_native's optional hotpath extension ───────────
Step "Checking Rust..."
if (Get-Command cargo -ErrorAction SilentlyContinue) {
    Ok "cargo found ($(cargo --version))"
} else {
    WarnMsg "cargo not found — installing via rustup-init..."
    try {
        Invoke-WebRequest -Uri "https://win.rustup.rs" -OutFile "$env:TEMP\rustup-init.exe"
        & "$env:TEMP\rustup-init.exe" -y --default-toolchain stable
        $env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
        if (Get-Command cargo -ErrorAction SilentlyContinue) { Ok "Rust installed" }
        else { WarnMsg "Rust install did not complete (non-fatal — pure-Python fallback still works, see POLYGLOT.md)" }
    } catch {
        WarnMsg "Rust install failed (non-fatal — pure-Python fallback still works, see POLYGLOT.md)"
    }
}
$HotpathDir = Join-Path $RepoRoot "rust\hotpath"
if ((Get-Command cargo -ErrorAction SilentlyContinue) -and (Test-Path (Join-Path $HotpathDir "Cargo.toml"))) {
    Step "Building rust/hotpath (release, best-effort)..."
    Push-Location $HotpathDir
    cargo build --release --quiet
    if ($LASTEXITCODE -eq 0) { Ok "rust/hotpath built" } else { WarnMsg "rust/hotpath build failed — fallbacks still work (see POLYGLOT.md)" }
    Pop-Location
}

# ── 4. Data directory ─────────────────────────────────────────────
$DataDir = Join-Path $env:USERPROFILE ".flash_loan_engine"
Step "Creating data directory at $DataDir..."
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
Ok "Data directory ready"

# ── 5. Environment file — auto-wired, no manual copy-paste ───────
Step "Wiring $EnvFile from every .env file reachable on this machine..."
New-Item -ItemType Directory -Force -Path $EnvDir | Out-Null
& $VenvPython -c "from jdl_flash.env_autowire import autowire; autowire()"

Write-Host ""
Write-Host "  Setup complete." -ForegroundColor Green
Write-Host "  Next steps:"
Write-Host "    1. $VenvDir\Scripts\Activate.ps1   — activate the virtual environment"
Write-Host "    2. jdl integrate                   — verify every connection is wired"
Write-Host "    3. jdl start flashloan             — launch the engine (same as: jdl run)"
Write-Host "    4. jdl test system                 — run the full test suite"
