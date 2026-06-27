#!/usr/bin/env python3
# ════════════════════════════════════════════════════════════════════════════
# REVENUE RECONCILIATION ENGINE
# Compares on-chain AureonPayProcessor balances vs SQLite .db records
# Projects: D.L, Aureon, FlipFlow, NEXUS-ARB
# ════════════════════════════════════════════════════════════════════════════

import sqlite3
import json
import sys
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, asdict
from web3 import Web3
import logging

# ─────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class ChainConfig:
    """RPC & contract config per chain"""
    name: str
    rpc_url: str
    contract_address: str
    usdc_address: str
    usdt_address: str
    dai_address: str

CHAINS = {
    'ethereum': ChainConfig(
        name='ethereum',
        rpc_url='https://eth.llamarpc.com',
        contract_address='0x',  # SET YOUR CONTRACT ADDRESS
        usdc_address='0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48',
        usdt_address='0xdAC17F958D2ee523a2206206994597C13D831ec7',
        dai_address='0x6B175474E89094C44Da98b954EedeAC495271d0F'
    ),
    'arbitrum': ChainConfig(
        name='arbitrum',
        rpc_url='https://arb1.arbitrum.io/rpc',
        contract_address='0x',  # SET YOUR CONTRACT ADDRESS
        usdc_address='0xFF970A61A04b1cA14834A43f5dE4533eBDDB5F86',
        usdt_address='0xFd086bC7CD5C481DCC9C85ebA8a04e8573d0db4e',
        dai_address='0xDA10009cbd5D07dd0CeCc66161FC93D7c9000da1'
    ),
    'optimism': ChainConfig(
        name='optimism',
        rpc_url='https://mainnet.optimism.io',
        contract_address='0x',  # SET YOUR CONTRACT ADDRESS
        usdc_address='0x7F5c764cBc14f9669B88837ca1490cCa17c31607',
        usdt_address='0x94b008aA00579c1307B0EF2c499aD98a8ce58e58',
        dai_address='0xDA10009cbd5D07dd0CeCc66161FC93D7c9000da1'
    ),
    'base': ChainConfig(
        name='base',
        rpc_url='https://mainnet.base.org',
        contract_address='0x',  # SET YOUR CONTRACT ADDRESS
        usdc_address='0x833589fCD6eDb6E08f4c7C32D4f71b1566469C3D',
        usdt_address='0xfDE4C96c8593536E31F26E3DafDA059b4d4F6C00',
        dai_address='0x50c5725949A6F0c72E6C4a641F14122319E23344'
    ),
    'polygon': ChainConfig(
        name='polygon',
        rpc_url='https://polygon-rpc.com',
        contract_address='0x',  # SET YOUR CONTRACT ADDRESS
        usdc_address='0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174',
        usdt_address='0xc2132D05D31c914a87C6611C10748AEb04B58e8F',
        dai_address='0x8f3Cf7ad23Cd3CaDbD9735AFF958023D60d965Da'
    ),
    'bsc': ChainConfig(
        name='bsc',
        rpc_url='https://bsc-dataseed.bnbchain.org',
        contract_address='0x',  # SET YOUR CONTRACT ADDRESS
        usdc_address='0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d',
        usdt_address='0x55d398326f99059fF775485246999027B3197955',
        dai_address='0x1AF3DBc2B77b9D2Ae9D8F98c6FBB7eD63a5e8b2d'
    )
}

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s'
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────
# ERC20 ABI (minimal for balanceOf)
# ─────────────────────────────────────────────────────────────────────────
ERC20_ABI = json.loads('''
[
    {
        "constant": true,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": true,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function"
    }
]
''')

# ─────────────────────────────────────────────────────────────────────────
# RECONCILIATION ENGINE
# ─────────────────────────────────────────────────────────────────────────

class RevenueReconciler:
    """Reconcile on-chain balances with .db records"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.results = []
    
    def get_on_chain_balance(self, chain: str, token_address: str) -> Tuple[float, int]:
        """
        Fetch token balance from contract on given chain
        Returns: (balance_in_units, decimals)
        """
        cfg = CHAINS.get(chain)
        if not cfg or not cfg.contract_address or cfg.contract_address == '0x':
            logger.warning(f"[{chain}] Contract address not set, skipping on-chain check")
            return 0.0, 0
        
        try:
            w3 = Web3(Web3.HTTPProvider(cfg.rpc_url, request_kwargs={'timeout': 10}))
            if not w3.is_connected():
                logger.error(f"[{chain}] Failed to connect to RPC: {cfg.rpc_url}")
                return 0.0, 0
            
            token_contract = w3.eth.contract(
                address=Web3.to_checksum_address(token_address),
                abi=ERC20_ABI
            )
            
            # Get decimals
            try:
                decimals = token_contract.functions.decimals().call()
            except:
                decimals = 18  # Default
            
            # Get balance
            balance_wei = token_contract.functions.balanceOf(
                Web3.to_checksum_address(cfg.contract_address)
            ).call()
            
            balance_units = balance_wei / (10 ** decimals)
            logger.info(f"[{chain}] On-chain balance: {balance_units} ({token_address[:8]}...)")
            
            return balance_units, decimals
            
        except Exception as e:
            logger.error(f"[{chain}] Failed to fetch on-chain balance: {e}")
            return 0.0, 0
    
    def get_db_recorded_amount(self, project: str, chain: str, token: str) -> float:
        """
        Sum of successful withdrawals for token from .db
        """
        try:
            c = self.conn.cursor()
            c.execute("""
                SELECT COALESCE(SUM(amount), 0) as total
                FROM withdrawals
                WHERE project=? AND chain=? AND token=? AND status='success'
            """, (project, chain, token))
            
            row = c.fetchone()
            total = float(row['total']) if row else 0.0
            logger.info(f"[{project}/{chain}] DB recorded withdrawals: {total} {token}")
            return total
            
        except Exception as e:
            logger.error(f"Failed to query .db: {e}")
            return 0.0
    
    def reconcile_chain(self, project: str, chain: str, contract_addr: str) -> Dict:
        """
        Reconcile a single chain
        Returns: {token: {on_chain, db_recorded, discrepancy, status}}
        """
        logger.info(f"\n{'='*70}")
        logger.info(f"Reconciling [{project}] on [{chain}]")
        logger.info(f"Contract: {contract_addr}")
        logger.info(f"{'='*70}")
        
        cfg = CHAINS.get(chain)
        if not cfg:
            logger.error(f"Chain {chain} not configured")
            return {}
        
        results_by_token = {}
        
        # Check USDC, USDT, DAI
        for token_name, token_addr in [
            ('USDC', cfg.usdc_address),
            ('USDT', cfg.usdt_address),
            ('DAI', cfg.dai_address)
        ]:
            on_chain_balance, decimals = self.get_on_chain_balance(chain, token_addr)
            db_recorded = self.get_db_recorded_amount(project, chain, token_name)
            discrepancy = on_chain_balance - db_recorded
            
            status = 'balanced' if abs(discrepancy) < 0.01 else 'discrepancy'
            
            results_by_token[token_name] = {
                'on_chain_balance': on_chain_balance,
                'db_recorded': db_recorded,
                'discrepancy': discrepancy,
                'status': status,
                'token_address': token_addr
            }
            
            # Log to reconciliation_log table
            try:
                c = self.conn.cursor()
                c.execute("""
                    INSERT INTO reconciliation_log
                    (project, chain, contract_address, token, on_chain_balance, 
                     db_recorded_amount, discrepancy, reconciliation_status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    project, chain, contract_addr, token_name,
                    on_chain_balance, db_recorded, discrepancy, status
                ))
                self.conn.commit()
            except Exception as e:
                logger.error(f"Failed to insert reconciliation log: {e}")
            
            # Print result
            logger.info(f"\n  {token_name}:")
            logger.info(f"    On-chain:     ${on_chain_balance:.4f}")
            logger.info(f"    DB Recorded:  ${db_recorded:.4f}")
            logger.info(f"    Discrepancy:  ${discrepancy:+.4f}  [{status.upper()}]")
            
            if status == 'discrepancy':
                logger.warning(f"    ⚠️  MISMATCH DETECTED")
        
        return results_by_token
    
    def generate_summary_report(self) -> str:
        """Generate human-readable summary"""
        try:
            c = self.conn.cursor()
            c.execute("SELECT * FROM vw_profit_by_chain ORDER BY total_net_profit DESC")
            rows = c.fetchall()
            
            report = "\n" + "="*80 + "\n"
            report += "REVENUE SUMMARY BY CHAIN\n"
            report += "="*80 + "\n"
            report += f"{'Project':<15} {'Chain':<12} {'Trades':<8} {'Gross':<12} {'Gas':<10} {'Net':<12} {'SR%':<6}\n"
            report += "-"*80 + "\n"
            
            for row in rows:
                report += f"{row['project']:<15} {row['chain']:<12} {row['total_trades']:<8} "
                report += f"${row['total_gross_profit']:<11.2f} ${row['total_gas_spent']:<9.2f} "
                report += f"${row['total_net_profit']:<11.2f} {row['success_rate_pct']:<5.1f}%\n"
            
            report += "="*80 + "\n"
            
            # Global totals
            c.execute("SELECT * FROM vw_all_projects_total")
            totals = c.fetchall()
            
            report += "\nGLOBAL TOTALS (All Projects)\n"
            report += "-"*80 + "\n"
            
            for row in totals:
                report += f"Project: {row['project']}\n"
                report += f"  Total Trades:        {row['total_trades']}\n"
                report += f"  Total Gross Profit:  ${row['total_gross_profit']:.2f}\n"
                report += f"  Total Gas Spent:     ${row['total_gas_spent']:.2f}\n"
                report += f"  Total Net Profit:    ${row['total_net_profit']:.2f}\n"
                report += f"  Total Withdrawn:     ${row['total_withdrawn']:.2f}\n"
                report += f"  Success Rate:        {row['successful_trades']}/{row['total_trades']}\n\n"
            
            return report
            
        except Exception as e:
            logger.error(f"Failed to generate report: {e}")
            return ""
    
    def export_reconciliation_json(self, output_file: str):
        """Export reconciliation results as JSON"""
        try:
            c = self.conn.cursor()
            c.execute("SELECT * FROM reconciliation_log ORDER BY created_at DESC LIMIT 100")
            rows = c.fetchall()
            
            data = []
            for row in rows:
                data.append({
                    'project': row['project'],
                    'chain': row['chain'],
                    'contract': row['contract_address'],
                    'token': row['token'],
                    'on_chain_balance': row['on_chain_balance'],
                    'db_recorded': row['db_recorded_amount'],
                    'discrepancy': row['discrepancy'],
                    'status': row['reconciliation_status'],
                    'timestamp': row['created_at']
                })
            
            with open(output_file, 'w') as f:
                json.dump(data, f, indent=2)
            
            logger.info(f"\nReconciliation exported to: {output_file}")
            
        except Exception as e:
            logger.error(f"Failed to export reconciliation: {e}")
    
    def close(self):
        """Close database connection"""
        self.conn.close()

# ─────────────────────────────────────────────────────────────────────────
# MAIN EXECUTION
# ─────────────────────────────────────────────────────────────────────────

def main(db_path: str, chains: List[str] = None, projects: List[str] = None):
    """
    Run reconciliation for all projects/chains
    
    Usage:
        python3 revenue_reconciliation.py ~/dl.2/data/aureon.db
        python3 revenue_reconciliation.py ~/dl.2/data/aureon.db --chains arbitrum optimism
        python3 revenue_reconciliation.py ~/dl.2/data/aureon.db --projects Aureon FlipFlow
    """
    
    logger.info(f"Starting revenue reconciliation from: {db_path}")
    
    reconciler = RevenueReconciler(db_path)
    
    try:
        # Determine which projects to check
        if not projects:
            c = reconciler.conn.cursor()
            c.execute("SELECT DISTINCT project FROM flash_trades")
            projects = [row[0] for row in c.fetchall()]
        
        if not projects:
            logger.warning("No projects found in database")
            return
        
        # Determine which chains to check
        if not chains:
            chains = list(CHAINS.keys())
        
        logger.info(f"Projects to reconcile: {projects}")
        logger.info(f"Chains to reconcile: {chains}")
        
        # Run reconciliation per project/chain
        for project in projects:
            for chain in chains:
                cfg = CHAINS.get(chain)
                if not cfg or cfg.contract_address == '0x':
                    logger.warning(f"[{project}/{chain}] Contract address not configured, skipping")
                    continue
                
                reconciler.reconcile_chain(project, chain, cfg.contract_address)
        
        # Generate reports
        report = reconciler.generate_summary_report()
        logger.info(report)
        
        # Save reports
        report_file = db_path.replace('.db', '_reconciliation_report.txt')
        json_file = db_path.replace('.db', '_reconciliation.json')
        
        with open(report_file, 'w') as f:
            f.write(report)
        logger.info(f"Report saved to: {report_file}")
        
        reconciler.export_reconciliation_json(json_file)
        
    finally:
        reconciler.close()

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Revenue Reconciliation Engine')
    parser.add_argument('db_path', help='Path to SQLite database (.db file)')
    parser.add_argument('--chains', nargs='+', help='Chains to reconcile (default: all)')
    parser.add_argument('--projects', nargs='+', help='Projects to reconcile (default: all in .db)')
    
    args = parser.parse_args()
    main(args.db_path, args.chains, args.projects)
