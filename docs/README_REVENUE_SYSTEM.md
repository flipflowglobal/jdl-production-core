# Revenue System v2 (Complete Deploy Package)

## AS-v3 | Summary

**Generated:** 5 production-ready files for D.L, Aureon, FlipFlow, NEXUS-ARB
- **SQL Schema** with auto-aggregating triggers & views
- **Reconciliation Engine** (on-chain balance verification)
- **Fixed Chain Monitor** (no more silent failures)
- **Auto-deployment Script** (detects projects, initializes all)
- **Integration Guide** (copy-paste code for your bot)

**Problem Solved:**
- ❌ "NO DATA" silent exceptions → ✅ all logged to `rpc_diagnostics` table
- ❌ Revenue scattered, untracked → ✅ auto-aggregated in `revenue_summary` table
- ❌ No way to find stuck funds → ✅ reconciliation script detects discrepancies
- ❌ Manual per-project setup → ✅ one script initializes all 4 projects

---

## Files Generated

| File | Size | Purpose |
|------|------|---------|
| `revenue_schema.sql` | 12K | SQLite3 schema: tables, triggers, views, indexes |
| `revenue_reconciliation.py` | 17K | On-chain balance checker vs .db records |
| `chain_monitor_fixed.py` | 20K | RPC health monitor with exception logging |
| `deploy_termux.sh` | 20K | Auto-detect projects, initialize all .db, copy scripts |
| `INTEGRATION_GUIDE.txt` | 14K | Copy-paste code for your bot, quick reference |

**Total:** ~83KB of production code

---

## 1-Minute Deploy (Termux/Ubuntu)

### Copy files to your device

```bash
# On your PC/laptop:
scp -r /home/claude/revenue* username@your-device:~/
scp /home/claude/chain_monitor_fixed.py username@your-device:~/
scp /home/claude/deploy_termux.sh username@your-device:~/

# Then SSH in:
ssh username@your-device
```

### Run deployment

```bash
cd ~
bash deploy_termux.sh
```

**That's it.** Script auto-detects all your projects (dl.2, aureon, flipflow, nexus-arb), initializes schemas, copies monitoring scripts, and creates launcher scripts.

---

## What Gets Installed

After `deploy_termux.sh` runs:

### Per-project (e.g., `~/dl.2/`)

```
~/dl.2/
├── data/
│   └── aureon.db          ← Revenue schema initialized here
├── scripts/
│   ├── revenue_reconciliation.py
│   └── chain_monitor.py   ← FIXED version
├── reconcile_revenue.sh   ← Quick launcher
├── monitor_chains.sh      ← Health check
└── monitor_chains_daemon.sh ← Background daemon
```

### Global

```
~/revenue_system.conf       ← Configuration (set contract addresses)
~/REVENUE_SYSTEM_QUICKSTART.md ← Auto-generated guide
```

---

## Typical Usage After Deploy

### 1. Set Contract Addresses

```bash
nano ~/revenue_system.conf
```

Update:
```ini
CONTRACT_ARBITRUM=0xYourAureonPayProcessorAddress
CONTRACT_ETHEREUM=0x...
CONTRACT_OPTIMISM=0x...
CONTRACT_BASE=0x...
CONTRACT_POLYGON=0x...
CONTRACT_BSC=0x...
```

### 2. Add Recording to Your Bot

In your arbitrage execution code (e.g., `orchestrate_aurora.py`):

```python
from revenue_recording import record_flash_arbitrage, record_withdrawal

# After flash arbitrage completes:
record_flash_arbitrage('~/dl.2/data/aureon.db', {
    'project': 'Aureon',
    'chain': 'arbitrum',
    'asset': 'USDC',
    'amount_borrowed': 1000.0,
    'fee_paid': 5.0,
    'gross_profit': 8.5,
    'gas_cost': 0.50,
    'net_profit': 8.0,
    'tx_hash': '0x...',
    'contract_address': '0xAureonPayProcessor',
    'initiator': '0xYourWallet'
})
```

(Helper functions provided in `INTEGRATION_GUIDE.txt`)

### 3. Start Monitoring

```bash
cd ~/dl.2
./monitor_chains_daemon.sh    # Background daemon checks all chains every 60s
```

### 4. Run Weekly Reconciliation

```bash
cd ~/dl.2
./reconcile_revenue.sh        # Compare on-chain balances vs .db
cat *reconciliation_report.txt
```

---

## Database Schema (Automatically Initialized)

### Core Tables

**`flash_trades`** — Every flash loan execution
```sql
id, timestamp, project, chain, asset_borrowed, amount_borrowed, fee_paid,
gross_profit, gas_cost, net_profit, tx_hash, status
```

**`withdrawals`** — Profit withdrawn from contracts
```sql
id, timestamp, project, chain, token, amount, from_contract, to_address,
tx_hash, status
```

**`revenue_summary`** — Auto-aggregated (via triggers)
```sql
project, chain, total_trades, total_gross_profit, total_gas_spent, 
total_net_profit, successful_trades, failed_trades, total_withdrawn
```

### Monitoring Tables

**`chain_health`** — RPC health check results
```sql
chain, timestamp, status, block_number, gas_price, error_msg
```

**`rpc_diagnostics`** — **NEW!** All RPC failures logged here
```sql
chain, timestamp, rpc_url, http_status, response_time_ms, error_type, error_msg
```

### Views (Query these)

```sql
vw_profit_by_chain      -- Summary per chain with success rate %
vw_all_projects_total   -- Global totals for all projects
vw_pending_withdrawals  -- Withdrawals not yet successful
```

---

## Fixed: Chain Monitor (No More "NO DATA")

### What Was Wrong
```python
try:
    data = rpc_call()
except Exception:
    pass  # ← Silent failure, nothing recorded
```

### What's Fixed
```python
try:
    data = rpc_call()
except requests.exceptions.Timeout:
    log_error("RPC timeout")
    record_diagnostic(chain, rpc_url, error_type='timeout')
    # Try backup RPC
except requests.exceptions.ConnectionError:
    log_error("Connection refused")
    record_diagnostic(chain, rpc_url, error_type='connection_error')
    # Try backup RPC
except Exception as e:
    log_error(f"Unexpected: {e}")
    record_diagnostic(chain, rpc_url, error_type=type(e).__name__)
```

**All errors now logged to `rpc_diagnostics` table.**

### Usage

```bash
# One-time health check
python3 ~/dl.2/scripts/chain_monitor.py ~/dl.2/data/aureon.db

# Output: ✅ Healthy chains, ⚠️ Degraded, ❌ Unreachable

# Background daemon (checks every 60s)
python3 ~/dl.2/scripts/chain_monitor.py ~/dl.2/data/aureon.db --daemon --interval 60

# Specific chains only
python3 ~/dl.2/scripts/chain_monitor.py ~/dl.2/data/aureon.db --chains arbitrum optimism base
```

---

## Reconciliation Engine: Find Missing Funds

### What It Does
1. Queries on-chain contract balance via RPC
2. Sums successful withdrawals from `.db` table
3. Compares: `on_chain - db_recorded = discrepancy`
4. Generates report + JSON

### Usage

```bash
python3 ~/dl.2/scripts/revenue_reconciliation.py ~/dl.2/data/aureon.db

# Output:
# ✓ [arbitrum] On-chain: $125.50 USDC | DB recorded: $120.00 | Discrepancy: +$5.50 [BALANCED]
# ⚠️  [optimism] On-chain: $0.00 DAI | DB recorded: $50.00 | Discrepancy: -$50.00 [MISMATCH!]

# Files generated:
# - *reconciliation_report.txt (human-readable)
# - *reconciliation.json (structured data)
```

### Interpreting Results

| Discrepancy | Meaning | Action |
|-------------|---------|--------|
| `+$5.00 [BALANCED]` | Profit in contract, not yet withdrawn | OK, will withdraw soon |
| `-$5.00 [MISMATCH]` | Profit withdrawn but not recorded in .db | Add to withdrawals table |
| `+$100 [DISCREPANCY]` | Large profit stuck in contract | Call `withdrawToken()` on-chain |

---

## Quick Reference: Common Queries

```bash
# SSH into Termux/Ubuntu
sqlite3 ~/dl.2/data/aureon.db

# View all successful trades
sqlite> SELECT chain, net_profit, tx_hash FROM flash_trades WHERE status='success';

# Total profit by project
sqlite> SELECT project, SUM(net_profit) FROM vw_all_projects_total GROUP BY project;

# Success rate per chain
sqlite> SELECT chain, success_rate_pct FROM vw_profit_by_chain;

# Recent RPC errors
sqlite> SELECT chain, error_type, error_msg FROM rpc_diagnostics ORDER BY created_at DESC LIMIT 10;

# Pending withdrawals (not yet on-chain)
sqlite> SELECT * FROM vw_pending_withdrawals;

# Export to CSV
sqlite> .mode csv
sqlite> .output report.csv
sqlite> SELECT * FROM vw_profit_by_chain;
sqlite> .output stdout
```

---

## Troubleshooting

### "ModuleNotFoundError: No module named 'web3'"

```bash
pip install web3 requests
# or
pip3 install web3 requests
```

### "sqlite3.OperationalError: no such table: flash_trades"

Schema not initialized. Re-run:
```bash
bash ~/deploy_termux.sh
```

### "RPC timeout" or "Connection refused"

Internet issue or RPC endpoint down. Check logs:
```bash
sqlite3 ~/dl.2/data/aureon.db "SELECT * FROM rpc_diagnostics ORDER BY timestamp DESC LIMIT 20;"
```

Script will auto-try backup RPC.

### Revenue not showing in .db

You haven't added `record_flash_arbitrage()` call to your bot yet. See **Integration Guide** section 2.

### Contract address shows "0x"

Edit `/root/revenue_system.conf` and set real addresses:
```bash
nano ~/revenue_system.conf
```

### Daemon not running

Check if process is alive:
```bash
ps aux | grep chain_monitor
```

If not, restart:
```bash
cd ~/dl.2
./monitor_chains_daemon.sh
```

---

## Integration Checklist

- [ ] Run `bash deploy_termux.sh`
- [ ] Set contract addresses in `~/revenue_system.conf`
- [ ] Copy `record_flash_arbitrage()` function into your bot code
- [ ] Call `record_flash_arbitrage()` after each flash arbitrage execution
- [ ] Test: `cd ~/dl.2 && ./monitor_chains.sh` (should show all chains)
- [ ] Test: `cd ~/dl.2 && ./reconcile_revenue.sh` (should show balances)
- [ ] Start daemon: `cd ~/dl.2 && ./monitor_chains_daemon.sh`
- [ ] Verify: Check logs after 1 hour: `cat *.log`
- [ ] Weekly: Run reconciliation to verify balances

---

## File Descriptions

### `revenue_schema.sql`
Complete SQLite3 schema. **Run this once per project:**
```bash
sqlite3 ~/dl.2/data/aureon.db < ~/revenue_schema.sql
```

(Deployment script does this automatically)

**Contains:**
- 6 tables (flash_trades, withdrawals, revenue_summary, chain_health, rpc_diagnostics, reconciliation_log)
- 2 triggers (auto-update revenue_summary on insert/update)
- 3 views (profit_by_chain, all_projects_total, pending_withdrawals)
- Indexes for fast queries

### `revenue_reconciliation.py`
Queries on-chain contract balances and compares to .db records.

**CLI:**
```bash
python3 revenue_reconciliation.py /path/to/aureon.db [--chains arbitrum optimism] [--projects Aureon FlipFlow]
```

**Output:**
- Console summary + detailed per-token report
- `*reconciliation_report.txt` (human)
- `*reconciliation.json` (machine)
- Inserts to `reconciliation_log` table

### `chain_monitor_fixed.py`
RPC health checker. **FIXED: logs all exceptions to database.**

**CLI:**
```bash
# One-time check
python3 chain_monitor.py /path/to/aureon.db

# Daemon mode
python3 chain_monitor.py /path/to/aureon.db --daemon --interval 60
```

**Checks:**
- Block number (on-chain sync)
- Gas price (network congestion)
- Peer count (network health)
- RPC latency
- Primary + backup RPC failover

**Logs to:**
- Console (stdout)
- `chain_monitor.log` file
- `chain_health` table (results)
- `rpc_diagnostics` table (errors)

### `deploy_termux.sh`
Master deployment script. **Run once after copying files.**

**Auto-detects:**
- All your projects: dl.2, aureon, flipflow, nexus-arb
- Their data directories
- Their database files

**Performs:**
1. Create data/ dirs if missing
2. Initialize schema in all .db files
3. Copy Python scripts to scripts/ dirs
4. Create launcher scripts (reconcile_revenue.sh, etc.)
5. Generate config template
6. Generate quick start guide
7. Verify all tables created

**No arguments needed:**
```bash
bash deploy_termux.sh
```

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    Your Arbitrage Bot                           │
│   (D.L, Aureon, FlipFlow, NEXUS-ARB on Termux/Ubuntu)          │
│                                                                 │
│  executeFlashArbitrage() → Web3.py → AureonPayProcessor.sol   │
│         │                                                       │
│         ├─→ record_flash_arbitrage(db_path, trade_data)       │
│         │   (logs to flash_trades table)                       │
│         │                                                       │
│         └─→ withdrawToken() → record_withdrawal(...)          │
│             (logs to withdrawals table)                        │
└─────────────────────────────────────────────────────────────────┘
                            ↓
         ┌──────────────────────────────────────┐
         │     SQLite3 Database (aureon.db)     │
         ├──────────────────────────────────────┤
         │ flash_trades                         │
         │ withdrawals                          │
         │ revenue_summary (auto-updated)       │
         │ chain_health                         │
         │ rpc_diagnostics                      │
         │ reconciliation_log                   │
         └──────────────────────────────────────┘
                  ↑            ↑           ↑
        ┌─────────┘  ┌─────────┴──┐  ┌────┴──────────┐
        │            │            │  │               │
    [Queries]   [Monitor]    [Reconcile]        [Reports]
        │            │            │  │               │
        ├─ vw_*      ├─chain_     ├─ on-chain  ├─ .txt
        │  views     │  monitor.py│  balances  ├─ .json
        │            │            │  check     └─ .log
        │            ├─ daemon    │
        │            │  mode      └─ .db comparison
        │            │ (--daemon)
        │            └─ stores in
        │              rpc_diagnostics
        │              (NO MORE "NO DATA"!)
        │
    (CLI queries)
```

---

## Next Steps

1. **Copy files to device:**
   ```bash
   scp /home/claude/revenue* ~/chain_monitor_fixed.py ~/deploy_termux.sh user@device:~/
   ```

2. **Run deployment:**
   ```bash
   ssh user@device
   cd ~
   bash deploy_termux.sh
   ```

3. **Configure:**
   ```bash
   nano ~/revenue_system.conf
   # Set all CONTRACT_* addresses
   ```

4. **Integrate:**
   - Copy `record_flash_arbitrage()` function (see INTEGRATION_GUIDE.txt)
   - Add to your bot after executing each flash arbitrage

5. **Start monitoring:**
   ```bash
   cd ~/dl.2
   ./monitor_chains_daemon.sh
   ```

6. **Verify weekly:**
   ```bash
   cd ~/dl.2
   ./reconcile_revenue.sh
   ```

---

## Support

**All files are in `/home/claude/`:**
- `revenue_schema.sql`
- `revenue_reconciliation.py`
- `chain_monitor_fixed.py`
- `deploy_termux.sh`
- `INTEGRATION_GUIDE.txt`

Copy to your device and run `bash deploy_termux.sh`.

---

**Generated:** June 25, 2026
**For:** D.L, Aureon, FlipFlow, NEXUS-ARB (Termux/Ubuntu/Android)
**AS-v3 Compliance:** ✅ 3 components + auto-deployment + integration guide
