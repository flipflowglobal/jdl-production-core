#!/usr/bin/env python3
# ════════════════════════════════════════════════════════════════════════════
# REVENUE RECORDING MODULE (Project-Agnostic)
# Works with any project directory or database path
# Import and use: from revenue_recording import record_flash_arbitrage
# ════════════════════════════════════════════════════════════════════════════

import sqlite3
import os
from datetime import datetime
from typing import Dict, Optional
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s'
)
logger = logging.getLogger('RevenueRecording')

# ─────────────────────────────────────────────────────────────────────────
# HELPER: Find database file
# ─────────────────────────────────────────────────────────────────────────

def find_database(project_root: str) -> Optional[str]:
    """
    Find the first .db file in project/data/
    
    Args:
        project_root: Root directory of project (can be absolute or relative)
    
    Returns:
        Path to .db file, or None if not found
    """
    
    project_root = os.path.expanduser(project_root)
    if not os.path.isdir(project_root):
        logger.error(f"Project directory not found: {project_root}")
        return None
    
    data_dir = os.path.join(project_root, "data")
    if not os.path.isdir(data_dir):
        logger.error(f"Data directory not found: {data_dir}")
        return None
    
    # Find first .db file
    for filename in os.listdir(data_dir):
        if filename.endswith('.db'):
            db_path = os.path.join(data_dir, filename)
            logger.debug(f"Found database: {db_path}")
            return db_path
    
    logger.error(f"No .db files found in {data_dir}")
    return None

# ─────────────────────────────────────────────────────────────────────────
# MAIN FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────

def record_flash_arbitrage(db_path: str, trade_data: Dict) -> bool:
    """
    Record a flash loan arbitrage execution to the database
    
    Args:
        db_path: Path to SQLite database (can be absolute or relative, or project root)
        trade_data: Dictionary with keys:
            - project (str): Project name (e.g., 'Aureon', 'FlipFlow')
            - chain (str): Chain name (e.g., 'arbitrum', 'ethereum')
            - asset_borrowed (str): Token symbol (e.g., 'USDC')
            - amount_borrowed (float): Amount in token units
            - fee_paid (float): Aave fee paid
            - gross_profit (float): Profit before gas
            - gas_cost (float): Gas cost in USD
            - net_profit (float): profit - gas_cost
            - tx_hash (str): Transaction hash
            - contract_address (str): Smart contract address
            - initiator (str): Wallet that initiated
            - intermediate_token (str, optional): Intermediate token in arbitrage
            - buy_dex (str, optional): DEX for buy leg
            - sell_dex (str, optional): DEX for sell leg
    
    Returns:
        True if successful, False otherwise
    """
    
    # Handle project root vs direct db path
    if db_path.endswith('.db'):
        actual_db_path = os.path.expanduser(db_path)
    else:
        actual_db_path = find_database(db_path)
        if not actual_db_path:
            return False
    
    if not os.path.isfile(actual_db_path):
        logger.error(f"Database file not found: {actual_db_path}")
        return False
    
    conn = None
    try:
        conn = sqlite3.connect(actual_db_path)
        c = conn.cursor()

        c.execute("""
            INSERT INTO flash_trades
            (project, chain, asset_borrowed, amount_borrowed, fee_paid,
             intermediate_token, buy_dex, sell_dex,
             gross_profit, gas_cost, net_profit, tx_hash,
             contract_address, initiator_address, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'success')
        """, (
            trade_data.get('project', 'Unknown'),
            trade_data.get('chain', 'unknown'),
            trade_data.get('asset_borrowed', 'USDC'),
            float(trade_data.get('amount_borrowed', 0)),
            float(trade_data.get('fee_paid', 0)),
            trade_data.get('intermediate_token'),
            trade_data.get('buy_dex'),
            trade_data.get('sell_dex'),
            float(trade_data.get('gross_profit', 0)),
            float(trade_data.get('gas_cost', 0)),
            float(trade_data.get('net_profit', 0)),
            trade_data['tx_hash'],
            trade_data['contract_address'],
            trade_data.get('initiator'),
        ))

        conn.commit()

        logger.info(f"✓ Recorded: {trade_data['tx_hash'][:10]}... | "
                   f"Net: ${trade_data.get('net_profit', 0):.2f} | "
                   f"Chain: {trade_data.get('chain')}")
        return True

    except sqlite3.IntegrityError as e:
        # Most commonly a duplicate tx_hash (UNIQUE) — an already-recorded trade,
        # not a schema problem. Log and move on.
        logger.error(f"Trade not recorded (integrity): {e}")
        return False
    except sqlite3.OperationalError as e:
        logger.error(f"Database error: {e}")
        logger.error("Make sure database schema is initialized: run deploy_termux.sh")
        return False
    except KeyError as e:
        logger.error(f"Missing required field: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to record trade: {e}")
        return False
    finally:
        # Always close so an error path never leaks an open (locking) connection.
        if conn is not None:
            conn.close()

def record_withdrawal(db_path: str, withdrawal_data: Dict) -> bool:
    """
    Record a profit withdrawal from contract to wallet
    
    Args:
        db_path: Path to SQLite database (can be absolute or relative, or project root)
        withdrawal_data: Dictionary with keys:
            - project (str): Project name
            - chain (str): Chain name
            - token (str): Token symbol (e.g., 'USDC')
            - amount (float): Amount withdrawn
            - from_contract (str): Contract address
            - to_address (str): Destination wallet
            - tx_hash (str): Withdrawal transaction hash
            - gas_cost (float, optional): Gas cost for withdrawal
    
    Returns:
        True if successful, False otherwise
    """
    
    # Handle project root vs direct db path
    if db_path.endswith('.db'):
        actual_db_path = os.path.expanduser(db_path)
    else:
        actual_db_path = find_database(db_path)
        if not actual_db_path:
            return False
    
    if not os.path.isfile(actual_db_path):
        logger.error(f"Database file not found: {actual_db_path}")
        return False
    
    conn = None
    try:
        conn = sqlite3.connect(actual_db_path)
        c = conn.cursor()

        c.execute("""
            INSERT INTO withdrawals
            (project, chain, token, amount, from_contract, to_address, tx_hash, gas_cost, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'success')
        """, (
            withdrawal_data.get('project', 'Unknown'),
            withdrawal_data.get('chain', 'unknown'),
            withdrawal_data.get('token', 'USDC'),
            float(withdrawal_data.get('amount', 0)),
            withdrawal_data['from_contract'],
            withdrawal_data['to_address'],
            withdrawal_data['tx_hash'],
            float(withdrawal_data.get('gas_cost', 0))
        ))

        conn.commit()

        logger.info(f"✓ Recorded withdrawal: {withdrawal_data.get('amount', 0):.2f} "
                   f"{withdrawal_data.get('token')} from {withdrawal_data.get('chain')}")
        return True

    except sqlite3.IntegrityError as e:
        logger.error(f"Withdrawal not recorded (integrity): {e}")
        return False
    except sqlite3.OperationalError as e:
        logger.error(f"Database error: {e}")
        return False
    except KeyError as e:
        logger.error(f"Missing required field: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to record withdrawal: {e}")
        return False
    finally:
        if conn is not None:
            conn.close()

def get_database_path(project_root: str) -> Optional[str]:
    """
    Utility function to get the database path for a project
    
    Args:
        project_root: Root directory of project
    
    Returns:
        Absolute path to .db file
    """
    return find_database(project_root)

# ─────────────────────────────────────────────────────────────────────────
# USAGE EXAMPLES
# ─────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    
    # Example 1: Recording a trade (using project root)
    trade = {
        'project': 'Aureon',
        'chain': 'arbitrum',
        'asset_borrowed': 'USDC',
        'amount_borrowed': 1000.0,
        'fee_paid': 5.0,
        'intermediate_token': 'ETH',
        'buy_dex': 'uniswap_v3',
        'sell_dex': 'uniswap_v3',
        'gross_profit': 8.5,
        'gas_cost': 0.50,
        'net_profit': 8.0,
        'tx_hash': '0x1234567890abcdef',
        'contract_address': '0xAureonPayProcessor',
        'initiator': '0xYourWallet'
    }
    
    # Option A: Pass project root
    record_flash_arbitrage('~/my_projects/aureon', trade)
    
    # Option B: Pass database path directly
    record_flash_arbitrage('~/my_projects/aureon/data/aureon.db', trade)
    
    # Example 2: Recording a withdrawal
    withdrawal = {
        'project': 'Aureon',
        'chain': 'arbitrum',
        'token': 'USDC',
        'amount': 125.50,
        'from_contract': '0xAureonPayProcessor',
        'to_address': '0xYourWallet',
        'tx_hash': '0xwithdrawal_tx_hash',
        'gas_cost': 1.25
    }
    
    record_withdrawal('~/my_projects/aureon', withdrawal)
