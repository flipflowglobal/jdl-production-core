#!/bin/bash
# ════════════════════════════════════════════════════════════════════════════
# REVENUE SYSTEM - TERMUX AUTO-DEPLOY
# Auto-detects: AUREON, D.L, Omaga, aureon., aureon.core, dl3, jdl, etc.
# Works with ACTUAL Termux directory structure (capitals, mixed case, dots)
# Usage: bash deploy_termux.sh
# ════════════════════════════════════════════════════════════════════════════

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "${BLUE}${BOLD}════════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}${BOLD}REVENUE SYSTEM - TERMUX AUTO-DEPLOY${NC}"
echo -e "${BLUE}${BOLD}════════════════════════════════════════════════════════════════${NC}\n"

# ─────────────────────────────────────────────────────────────────────────
# STEP 1: AUTO-DETECT PROJECT DIRECTORIES (CASE-INSENSITIVE)
# ─────────────────────────────────────────────────────────────────────────

echo -e "${YELLOW}[1] Scanning for project directories...${NC}\n"

PROJECT_DIRS=()
EXCLUDED_DIRS=("storage" "downloads" "venv" ".git" ".cache")

# Scan home directory for project folders
for dir in $(ls -1d */ 2>/dev/null | sed 's/\///'); do
    # Skip excluded directories
    skip=0
    for excluded in "${EXCLUDED_DIRS[@]}"; do
        if [ "$dir" = "$excluded" ]; then
            skip=1
            break
        fi
    done
    
    if [ $skip -eq 0 ]; then
        abs_path="$(cd "$dir" 2>/dev/null && pwd)"
        
        # Check if it looks like a project:
        # - has data/ OR scripts/ OR .git OR *.py files
        if [ -d "$abs_path/data" ] || \
           [ -d "$abs_path/scripts" ] || \
           [ -d "$abs_path/.git" ] || \
           [ -d "$abs_path/contracts" ] || \
           ls "$abs_path"/*.py >/dev/null 2>&1; then
            
            PROJECT_DIRS+=("$abs_path")
            echo -e "${GREEN}✓${NC} Detected: ${BOLD}$dir${NC}"
        fi
    fi
done

echo ""

if [ ${#PROJECT_DIRS[@]} -eq 0 ]; then
    echo -e "${RED}✗${NC} No projects found"
    echo -e "${YELLOW}Expected directories: AUREON, D.L, Omaga, aureon.core, dl3, jdl, etc.${NC}"
    exit 1
fi

echo -e "${GREEN}Found ${#PROJECT_DIRS[@]} project(s)${NC}\n"

# ─────────────────────────────────────────────────────────────────────────
# STEP 2: CREATE DIRECTORY STRUCTURE
# ─────────────────────────────────────────────────────────────────────────

echo -e "${YELLOW}[2] Creating directory structure...${NC}"

for proj_path in "${PROJECT_DIRS[@]}"; do
    proj_name=$(basename "$proj_path")
    
    # Create required directories
    mkdir -p "$proj_path/data"
    mkdir -p "$proj_path/scripts"
    mkdir -p "$proj_path/logs"
    
    echo -e "${GREEN}✓${NC} $proj_name: data/, scripts/, logs/"
done

echo ""

# ─────────────────────────────────────────────────────────────────────────
# STEP 3: INITIALIZE OR FIND DATABASE
# ─────────────────────────────────────────────────────────────────────────

echo -e "${YELLOW}[3] Initializing databases...${NC}"

for proj_path in "${PROJECT_DIRS[@]}"; do
    proj_name=$(basename "$proj_path")
    data_dir="${proj_path}/data"
    
    # Look for existing .db file
    db_path=$(find "$data_dir" -maxdepth 1 -name "*.db" -type f 2>/dev/null | head -1)
    
    # If no .db found, create one named after project
    if [ -z "$db_path" ]; then
        # Sanitize project name for filename (remove dots, spaces)
        db_filename=$(echo "$proj_name" | tr '.,' '_' | tr ' ' '_').db
        db_path="${data_dir}/${db_filename}"
        touch "$db_path"
        echo -e "${GREEN}✓${NC} Created: $proj_name/data/${db_filename}"
    else
        echo -e "${GREEN}✓${NC} Found: $proj_name/data/$(basename "$db_path")"
    fi
    
    # Apply schema (silently, errors are expected for existing tables)
    if [ -f "revenue_schema.sql" ]; then
        sqlite3 "$db_path" < revenue_schema.sql 2>/dev/null || true
    else
        echo -e "${YELLOW}⚠${NC} revenue_schema.sql not found in current directory"
    fi
done

echo ""

# ─────────────────────────────────────────────────────────────────────────
# STEP 4: COPY MONITORING SCRIPTS
# ─────────────────────────────────────────────────────────────────────────

echo -e "${YELLOW}[4] Deploying Python scripts...${NC}"

for proj_path in "${PROJECT_DIRS[@]}"; do
    proj_name=$(basename "$proj_path")
    scripts_dir="${proj_path}/scripts"
    
    # Copy Python scripts if they exist in current directory
    if [ -f "revenue_reconciliation.py" ]; then
        cp revenue_reconciliation.py "$scripts_dir/"
        chmod +x "$scripts_dir/revenue_reconciliation.py"
        echo -e "${GREEN}✓${NC} $proj_name: revenue_reconciliation.py"
    fi
    
    if [ -f "chain_monitor_fixed.py" ]; then
        cp chain_monitor_fixed.py "$scripts_dir/chain_monitor.py"
        chmod +x "$scripts_dir/chain_monitor.py"
        echo -e "${GREEN}✓${NC} $proj_name: chain_monitor.py"
    fi
    
    if [ -f "revenue_recording.py" ]; then
        cp revenue_recording.py "$scripts_dir/"
        chmod +x "$scripts_dir/revenue_recording.py"
        echo -e "${GREEN}✓${NC} $proj_name: revenue_recording.py"
    fi
done

echo ""

# ─────────────────────────────────────────────────────────────────────────
# STEP 5: CREATE LAUNCHER SCRIPTS
# ─────────────────────────────────────────────────────────────────────────

echo -e "${YELLOW}[5] Creating launcher scripts...${NC}"

for proj_path in "${PROJECT_DIRS[@]}"; do
    proj_name=$(basename "$proj_path")
    
    # Find the database file
    db_path=$(find "$proj_path/data" -maxdepth 1 -name "*.db" -type f 2>/dev/null | head -1)
    
    if [ -z "$db_path" ]; then
        echo -e "${YELLOW}⚠${NC} No database found in $proj_name/data"
        continue
    fi
    
    db_dir=$(dirname "$db_path")
    db_name=$(basename "$db_path")
    scripts_dir="$proj_path/scripts"
    
    # Reconciliation launcher
    cat > "$proj_path/reconcile.sh" << EOF
#!/bin/bash
# Reconcile revenue for $proj_name
cd "$proj_path"
python3 scripts/revenue_reconciliation.py data/$db_name \${@}
EOF
    chmod +x "$proj_path/reconcile.sh"
    echo -e "${GREEN}✓${NC} $proj_name/reconcile.sh"
    
    # Health check launcher (once)
    cat > "$proj_path/health.sh" << EOF
#!/bin/bash
# Health check for $proj_name
cd "$proj_path"
python3 scripts/chain_monitor.py data/$db_name \${@}
EOF
    chmod +x "$proj_path/health.sh"
    echo -e "${GREEN}✓${NC} $proj_name/health.sh"
    
    # Daemon launcher
    cat > "$proj_path/monitor.sh" << EOF
#!/bin/bash
# Background daemon for $proj_name
cd "$proj_path"
mkdir -p logs
nohup python3 scripts/chain_monitor.py data/$db_name --daemon --interval 60 > logs/monitor.log 2>&1 &
PID=\$!
echo "\${BOLD}✓${NC} Monitor daemon started (PID: \$PID)"
echo "View logs: tail -f logs/monitor.log"
EOF
    chmod +x "$proj_path/monitor.sh"
    echo -e "${GREEN}✓${NC} $proj_name/monitor.sh"
done

echo ""

# ─────────────────────────────────────────────────────────────────────────
# STEP 6: CREATE GLOBAL LAUNCHER (Run ALL projects)
# ─────────────────────────────────────────────────────────────────────────

echo -e "${YELLOW}[6] Creating global launcher scripts...${NC}"

# Health check all
cat > "health_all.sh" << 'EOF'
#!/bin/bash
# Check health of ALL projects
echo "════════════════════════════════════════════════════════"
echo "CHECKING ALL PROJECTS"
echo "════════════════════════════════════════════════════════"
for dir in */; do
    [ -f "$dir/health.sh" ] && (cd "$dir" && bash health.sh 2>/dev/null | tail -5)
done
EOF
chmod +x "health_all.sh"
echo -e "${GREEN}✓${NC} health_all.sh (check all projects)"

# Start monitoring all
cat > "monitor_all.sh" << 'EOF'
#!/bin/bash
# Start monitoring daemon for ALL projects
echo "════════════════════════════════════════════════════════"
echo "STARTING MONITORS FOR ALL PROJECTS"
echo "════════════════════════════════════════════════════════"
for dir in */; do
    if [ -f "$dir/monitor.sh" ]; then
        echo "Starting: $dir"
        (cd "$dir" && bash monitor.sh)
    fi
done
echo "All monitors started"
EOF
chmod +x "monitor_all.sh"
echo -e "${GREEN}✓${NC} monitor_all.sh (start all daemons)"

# Reconcile all
cat > "reconcile_all.sh" << 'EOF'
#!/bin/bash
# Reconcile revenue for ALL projects
echo "════════════════════════════════════════════════════════"
echo "RECONCILING ALL PROJECTS"
echo "════════════════════════════════════════════════════════"
for dir in */; do
    if [ -f "$dir/reconcile.sh" ]; then
        echo -e "\nProject: $dir"
        (cd "$dir" && bash reconcile.sh 2>/dev/null | grep -E "^(✓|⚠|❌|On-chain|DB)")
    fi
done
EOF
chmod +x "reconcile_all.sh"
echo -e "${GREEN}✓${NC} reconcile_all.sh (reconcile all projects)"

echo ""

# ─────────────────────────────────────────────────────────────────────────
# STEP 7: VERIFY DEPLOYMENT
# ─────────────────────────────────────────────────────────────────────────

echo -e "${YELLOW}[7] Verifying deployment...${NC}\n"

for proj_path in "${PROJECT_DIRS[@]}"; do
    proj_name=$(basename "$proj_path")
    
    db_path=$(find "$proj_path/data" -maxdepth 1 -name "*.db" -type f 2>/dev/null | head -1)
    
    if [ -z "$db_path" ]; then
        echo -e "${YELLOW}⚠${NC} $proj_name: No database"
        continue
    fi
    
    # Check tables exist
    tables=$(sqlite3 "$db_path" ".tables" 2>/dev/null | grep -o "flash_trades\|withdrawals\|revenue_summary" | wc -l)
    
    if [ "$tables" -eq 3 ]; then
        echo -e "${GREEN}✓${NC} $proj_name: Schema initialized (3/3 tables)"
    else
        echo -e "${YELLOW}⚠${NC} $proj_name: Found $tables/3 tables"
    fi
done

echo ""

# ─────────────────────────────────────────────────────────────────────────
# FINAL SUMMARY
# ─────────────────────────────────────────────────────────────────────────

echo -e "${BLUE}${BOLD}════════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}✓ DEPLOYMENT COMPLETE${NC}"
echo -e "${BLUE}${BOLD}════════════════════════════════════════════════════════════════${NC}\n"

echo -e "${YELLOW}Deployed to ${#PROJECT_DIRS[@]} projects:${NC}"
for proj_path in "${PROJECT_DIRS[@]}"; do
    echo "  • $(basename "$proj_path")"
done

echo ""
echo -e "${YELLOW}Each project now has:${NC}"
echo "  • data/*.db (revenue schema initialized)"
echo "  • scripts/ (Python monitoring tools)"
echo "  • health.sh (one-time health check)"
echo "  • monitor.sh (background daemon)"
echo "  • reconcile.sh (balance reconciliation)"
echo "  • logs/ (output logs)"

echo ""
echo -e "${YELLOW}Global commands (from home):${NC}"
echo "  • bash health_all.sh       (check all projects)"
echo "  • bash monitor_all.sh      (start all daemons)"
echo "  • bash reconcile_all.sh    (reconcile all projects)"

echo ""
echo -e "${YELLOW}Per-project commands:${NC}"
echo "  • cd PROJECT_NAME && bash health.sh"
echo "  • cd PROJECT_NAME && bash monitor.sh"
echo "  • cd PROJECT_NAME && bash reconcile.sh"

echo ""
echo -e "${YELLOW}View logs:${NC}"
echo "  • tail -f PROJECT_NAME/logs/monitor.log"

echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "  1. bash health_all.sh        (verify all chains)"
echo "  2. bash monitor_all.sh       (start monitoring)"
echo "  3. Add revenue_recording.py to your bots"

echo ""
echo -e "${BLUE}${BOLD}════════════════════════════════════════════════════════════════${NC}"
