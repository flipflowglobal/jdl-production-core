#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# Revenue System — GitHub Push Script for Termux
# Run this on your Termux device after pulling jdl-production-core.
#
# Usage:
#   bash scripts/push_revenue_system.sh [SOURCE_DIR]
#
# SOURCE_DIR: directory containing the 7 revenue system files.
#   Defaults to: ~/jdl-production-core/revenue_system_files
#   or /mnt/user-data/outputs  (if that path exists)
#
# Files expected in SOURCE_DIR:
#   deploy_termux.sh
#   revenue_schema.sql
#   revenue_recording.py
#   chain_monitor_fixed.py
#   revenue_reconciliation.py
#   INTEGRATION_GUIDE.txt
#   README_REVENUE_SYSTEM.md
#
# Requirements on Termux:
#   pkg install git python
#   git config --global user.email "you@example.com"
#   git config --global user.name "Your Name"
#   # Auth: use a GitHub Personal Access Token (PAT) as the password
#   # when git asks, or store it:
#   #   git config --global credential.helper store
#   #   then push once and enter username + PAT
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

# ── Colour helpers ────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
step()  { echo -e "${CYAN}▶ $*${RESET}"; }
ok()    { echo -e "${GREEN}✓ $*${RESET}"; }
warn()  { echo -e "${YELLOW}⚠ $*${RESET}"; }
fail()  { echo -e "${RED}✗ $*${RESET}"; exit 1; }

BRANCH="feature/revenue-system-integration"
COMMIT_MSG="feat: Add revenue tracking and RPC monitoring system"
GITHUB_ORG="flipflowglobal"

# ── Repositories in priority order ───────────────────────────────
REPOS=(
  "D.L"
  "aureon_core"
  "jdl-production-core"
  "dl3"
  "Aureon"
  "FlipFlow"
  "JDLtrade"
)

# ── Locate source files ───────────────────────────────────────────
if [ -n "${1:-}" ]; then
  SRC="$1"
elif [ -d "/mnt/user-data/outputs" ]; then
  SRC="/mnt/user-data/outputs"
elif [ -d "$HOME/jdl-production-core/revenue_system_files" ]; then
  SRC="$HOME/jdl-production-core/revenue_system_files"
else
  fail "Source directory not found. Pass it as the first argument:\n  bash scripts/push_revenue_system.sh /path/to/your/files"
fi

step "Source directory: $SRC"

# Verify all required files are present
REQUIRED=(
  deploy_termux.sh
  revenue_schema.sql
  revenue_recording.py
  chain_monitor_fixed.py
  revenue_reconciliation.py
  INTEGRATION_GUIDE.txt
  README_REVENUE_SYSTEM.md
)
MISSING=()
for f in "${REQUIRED[@]}"; do
  [ -f "$SRC/$f" ] || MISSING+=("$f")
done
if [ ${#MISSING[@]} -gt 0 ]; then
  fail "Missing files in $SRC:\n$(printf '  %s\n' "${MISSING[@]}")"
fi
ok "All 7 source files present"
echo

# ── Working directory ─────────────────────────────────────────────
WORK_DIR="$HOME/_revenue_push_tmp"
mkdir -p "$WORK_DIR"

# ── Per-repo function ─────────────────────────────────────────────
push_to_repo() {
  local REPO="$1"
  local REPO_DIR="$WORK_DIR/$REPO"

  echo -e "\n${BOLD}━━━ $GITHUB_ORG/$REPO ━━━${RESET}"

  # Clone or update
  if [ -d "$REPO_DIR/.git" ]; then
    step "Updating existing clone..."
    git -C "$REPO_DIR" fetch origin --quiet
    git -C "$REPO_DIR" checkout "$(git -C "$REPO_DIR" symbolic-ref --short refs/remotes/origin/HEAD | sed 's|origin/||')" --quiet 2>/dev/null || \
    git -C "$REPO_DIR" checkout main --quiet 2>/dev/null || \
    git -C "$REPO_DIR" checkout master --quiet 2>/dev/null
    git -C "$REPO_DIR" pull --quiet
    ok "Repo updated"
  else
    step "Cloning $REPO..."
    git clone --quiet "https://github.com/$GITHUB_ORG/$REPO.git" "$REPO_DIR" \
      || { warn "Clone failed for $REPO — skipping"; return 0; }
    ok "Cloned"
  fi

  # Create/reset feature branch
  step "Preparing branch $BRANCH..."
  git -C "$REPO_DIR" checkout -B "$BRANCH" --quiet

  # ── Create directory structure ──────────────────────────────────
  mkdir -p \
    "$REPO_DIR/scripts" \
    "$REPO_DIR/database" \
    "$REPO_DIR/revenue_system" \
    "$REPO_DIR/docs"

  # ── Copy files ─────────────────────────────────────────────────
  cp "$SRC/deploy_termux.sh"           "$REPO_DIR/scripts/deploy_termux.sh"
  chmod +x "$REPO_DIR/scripts/deploy_termux.sh"

  cp "$SRC/revenue_schema.sql"         "$REPO_DIR/database/revenue_schema.sql"
  cp "$SRC/revenue_recording.py"       "$REPO_DIR/revenue_system/revenue_recording.py"
  cp "$SRC/chain_monitor_fixed.py"     "$REPO_DIR/revenue_system/chain_monitor_fixed.py"
  cp "$SRC/revenue_reconciliation.py"  "$REPO_DIR/revenue_system/revenue_reconciliation.py"
  cp "$SRC/INTEGRATION_GUIDE.txt"      "$REPO_DIR/docs/INTEGRATION_GUIDE.txt"
  cp "$SRC/README_REVENUE_SYSTEM.md"   "$REPO_DIR/docs/README_REVENUE_SYSTEM.md"

  # ── revenue_system/__init__.py ─────────────────────────────────
  cat > "$REPO_DIR/revenue_system/__init__.py" << 'PYEOF'
"""Revenue tracking and RPC monitoring for flipflowglobal DeFi projects."""
from .revenue_recording import record_flash_arbitrage, record_withdrawal
PYEOF

  # ── Update .gitignore ───────────────────────────────────────────
  GITIGNORE="$REPO_DIR/.gitignore"
  touch "$GITIGNORE"
  for entry in "data/" "*.db" "logs/" "*.log" "revenue_system/__pycache__/"; do
    grep -qxF "$entry" "$GITIGNORE" 2>/dev/null || echo "$entry" >> "$GITIGNORE"
  done

  # ── Update requirements.txt ─────────────────────────────────────
  REQ="$REPO_DIR/requirements.txt"
  touch "$REQ"
  for dep in "web3>=6.0.0" "requests>=2.28.0"; do
    grep -qF "${dep%%>=*}" "$REQ" 2>/dev/null || echo "$dep" >> "$REQ"
  done

  # ── Update README.md ────────────────────────────────────────────
  README="$REPO_DIR/README.md"
  if [ -f "$README" ] && ! grep -q "Revenue Tracking" "$README" 2>/dev/null; then
    cat >> "$README" << 'MDEOF'

---

### Revenue Tracking & Monitoring

This project integrates the shared revenue tracking and RPC monitoring system.

**Quick integration:**
```python
from revenue_system.revenue_recording import record_flash_arbitrage

record_flash_arbitrage('.', {
    'chain': 'arbitrum',
    'asset_borrowed': 'USDC',
    'amount_borrowed': 1000.0,
    'fee_paid': 5.0,
    'gross_profit': 8.5,
    'gas_cost': 0.50,
    'net_profit': 8.0,
    'tx_hash': '0x...',
})
```

See `docs/INTEGRATION_GUIDE.txt` and `docs/README_REVENUE_SYSTEM.md` for full documentation.
MDEOF
    ok "README.md updated"
  elif [ ! -f "$README" ]; then
    echo "# $REPO" > "$README"
    echo "" >> "$README"
    echo "See \`docs/README_REVENUE_SYSTEM.md\` for revenue system documentation." >> "$README"
    ok "README.md created"
  else
    ok "README.md already has revenue section"
  fi

  # ── Commit ──────────────────────────────────────────────────────
  step "Committing..."
  git -C "$REPO_DIR" add \
    scripts/deploy_termux.sh \
    database/revenue_schema.sql \
    revenue_system/ \
    docs/INTEGRATION_GUIDE.txt \
    docs/README_REVENUE_SYSTEM.md \
    .gitignore \
    requirements.txt \
    README.md 2>/dev/null || true

  if git -C "$REPO_DIR" diff --cached --quiet; then
    ok "Nothing to commit (files already identical)"
  else
    git -C "$REPO_DIR" commit -m "$COMMIT_MSG" --quiet
    ok "Committed"
  fi

  # ── Push ────────────────────────────────────────────────────────
  step "Pushing $BRANCH to GitHub..."
  if git -C "$REPO_DIR" push -u origin "$BRANCH" --force-with-lease --quiet 2>&1; then
    ok "Pushed"
  else
    warn "Push failed for $REPO — check your GitHub credentials (PAT required)"
    return 0
  fi

  # ── Print PR URL (manual step — gh CLI not required) ────────────
  echo -e "  ${CYAN}Create PR:${RESET} https://github.com/$GITHUB_ORG/$REPO/compare/$BRANCH"
  ok "Done: $REPO"
}

# ── Run for all repos ─────────────────────────────────────────────
FAILED=()
for REPO in "${REPOS[@]}"; do
  push_to_repo "$REPO" || FAILED+=("$REPO")
done

# ── Summary ───────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}════════════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}  Revenue System Push Complete${RESET}"
echo -e "${BOLD}════════════════════════════════════════${RESET}"
echo ""
echo "  Branch pushed: $BRANCH"
echo "  Working copies: $WORK_DIR"
echo ""
echo "  Open each PR link above to merge."
echo ""
if [ ${#FAILED[@]} -gt 0 ]; then
  warn "These repos had issues: ${FAILED[*]}"
  echo "  Re-run with: bash scripts/push_revenue_system.sh \"$SRC\""
fi
