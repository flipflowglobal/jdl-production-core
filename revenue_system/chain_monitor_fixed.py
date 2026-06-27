#!/usr/bin/env python3
# ════════════════════════════════════════════════════════════════════════════
# CHAIN MONITOR v2 (FIXED)
# Projects: D.L, Aureon, FlipFlow, NEXUS-ARB
# Fixes: Silent exception handling → explicit logging + .db recording
# ════════════════════════════════════════════════════════════════════════════

import json
import sqlite3
import logging
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass, field
from enum import Enum
import traceback
from threading import Thread, Event
import requests

# ─────────────────────────────────────────────────────────────────────────
# CONFIGURATION & SETUP
# ─────────────────────────────────────────────────────────────────────────

class ChainStatus(Enum):
    """Health status of a chain"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNREACHABLE = "unreachable"
    UNKNOWN = "unknown"

@dataclass
class ChainConfig:
    """RPC & monitor config"""
    name: str
    rpc_url: str
    rpc_backup: Optional[str] = None
    block_time_seconds: int = 12
    expected_block_interval: int = 100  # Check if RPC is lagging
    timeout_seconds: int = 10

CHAINS = {
    'ethereum': ChainConfig(
        name='ethereum',
        rpc_url='https://eth.llamarpc.com',
        rpc_backup='https://eth.getblock.io/mainnet/',
        block_time_seconds=12,
        expected_block_interval=100
    ),
    'arbitrum': ChainConfig(
        name='arbitrum',
        rpc_url='https://arb1.arbitrum.io/rpc',
        rpc_backup='https://arbitrum.getblock.io/mainnet/',
        block_time_seconds=0.25,
        expected_block_interval=400
    ),
    'optimism': ChainConfig(
        name='optimism',
        rpc_url='https://mainnet.optimism.io',
        rpc_backup='https://optimism.getblock.io/mainnet/',
        block_time_seconds=2,
        expected_block_interval=50
    ),
    'base': ChainConfig(
        name='base',
        rpc_url='https://mainnet.base.org',
        rpc_backup='https://base.getblock.io/mainnet/',
        block_time_seconds=2,
        expected_block_interval=50
    ),
    'polygon': ChainConfig(
        name='polygon',
        rpc_url='https://polygon-rpc.com',
        rpc_backup='https://polygon.getblock.io/mainnet/',
        block_time_seconds=2,
        expected_block_interval=256
    ),
    'bsc': ChainConfig(
        name='bsc',
        rpc_url='https://bsc-dataseed.bnbchain.org',
        rpc_backup='https://bsc.getblock.io/mainnet/',
        block_time_seconds=3,
        expected_block_interval=20
    )
}

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s | %(name)-15s | %(levelname)-8s | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('chain_monitor.log')
    ]
)
logger = logging.getLogger('ChainMonitor')

# ─────────────────────────────────────────────────────────────────────────
# HEALTH CHECK ENGINE
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class HealthCheckResult:
    """Result of a single health check"""
    chain: str
    timestamp: datetime
    status: ChainStatus
    block_number: Optional[int] = None
    block_age_seconds: Optional[float] = None
    gas_price: Optional[float] = None
    peer_count: Optional[int] = None
    latency_ms: Optional[float] = None
    error_msg: Optional[str] = None
    rpc_used: str = 'primary'

class ChainHealthChecker:
    """RPC health check engine"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Create chain_health table if not exists"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute("""
            CREATE TABLE IF NOT EXISTS chain_health (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chain TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL,
                block_number INTEGER,
                block_age_seconds REAL,
                gas_price REAL,
                peer_count INTEGER,
                latency_ms REAL,
                error_msg TEXT,
                rpc_used TEXT DEFAULT 'primary'
            )
        """)
        
        c.execute("""
            CREATE TABLE IF NOT EXISTS rpc_diagnostics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chain TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                rpc_url TEXT,
                http_status INTEGER,
                response_time_ms REAL,
                error_type TEXT,
                error_msg TEXT,
                test_method TEXT
            )
        """)
        
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_chain_health_chain ON chain_health(chain)
        """)
        
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_chain_health_timestamp ON chain_health(timestamp)
        """)
        
        conn.commit()
        conn.close()
        logger.info("Database tables initialized")
    
    def _rpc_call(self, chain: str, method: str, params: List = None, 
                  rpc_url: Optional[str] = None) -> Tuple[Optional[Dict], Optional[str]]:
        """
        Make JSON-RPC call with error handling
        Returns: (response_data, error_msg)
        """
        
        if not rpc_url:
            cfg = CHAINS.get(chain)
            if not cfg:
                return None, f"Chain {chain} not configured"
            rpc_url = cfg.rpc_url
        
        payload = {
            'jsonrpc': '2.0',
            'method': method,
            'params': params or [],
            'id': 1
        }
        
        try:
            start_time = time.time()
            response = requests.post(
                rpc_url,
                json=payload,
                timeout=CHAINS[chain].timeout_seconds if chain in CHAINS else 10,
                headers={'Content-Type': 'application/json'}
            )
            elapsed_ms = (time.time() - start_time) * 1000
            
            # Record diagnostics
            self._record_diagnostic(chain, rpc_url, response.status_code, elapsed_ms, method)
            
            if response.status_code != 200:
                error_msg = f"HTTP {response.status_code}"
                logger.error(f"[{chain}] RPC HTTP error: {error_msg} from {rpc_url}")
                return None, error_msg
            
            data = response.json()
            
            # Check for JSON-RPC error
            if 'error' in data and data['error']:
                error_msg = data['error'].get('message', 'Unknown RPC error')
                logger.error(f"[{chain}] JSON-RPC error ({method}): {error_msg}")
                return None, error_msg
            
            logger.debug(f"[{chain}] {method} succeeded ({elapsed_ms:.1f}ms)")
            return data.get('result'), None
            
        except requests.exceptions.Timeout:
            error_msg = f"RPC timeout ({CHAINS.get(chain, ChainConfig('', '')).timeout_seconds}s)"
            logger.error(f"[{chain}] {error_msg} from {rpc_url}")
            self._record_diagnostic(chain, rpc_url, None, None, method, 'timeout', error_msg)
            return None, error_msg
            
        except requests.exceptions.ConnectionError as e:
            error_msg = f"Connection refused: {str(e)[:50]}"
            logger.error(f"[{chain}] {error_msg}")
            self._record_diagnostic(chain, rpc_url, None, None, method, 'connection_error', error_msg)
            return None, error_msg
            
        except json.JSONDecodeError as e:
            error_msg = f"Invalid JSON response: {str(e)[:50]}"
            logger.error(f"[{chain}] {error_msg}")
            self._record_diagnostic(chain, rpc_url, 200, None, method, 'json_decode_error', error_msg)
            return None, error_msg
            
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)[:100]}"
            logger.error(f"[{chain}] {error_msg}\n{traceback.format_exc()}")
            self._record_diagnostic(chain, rpc_url, None, None, method, type(e).__name__, error_msg)
            return None, error_msg
    
    def _record_diagnostic(self, chain: str, rpc_url: str, http_status: Optional[int],
                          response_time_ms: Optional[float], method: str,
                          error_type: Optional[str] = None, error_msg: Optional[str] = None):
        """Log RPC diagnostic to database"""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            
            c.execute("""
                INSERT INTO rpc_diagnostics
                (chain, rpc_url, http_status, response_time_ms, test_method, error_type, error_msg)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (chain, rpc_url, http_status, response_time_ms, method, error_type, error_msg))
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to record diagnostic: {e}")
    
    def check_chain(self, chain: str) -> HealthCheckResult:
        """
        Comprehensive health check for a chain
        Tests: block number, gas price, peer count, latency
        """
        
        logger.info(f"Starting health check for [{chain}]")
        
        cfg = CHAINS.get(chain)
        if not cfg:
            logger.error(f"Chain {chain} not configured")
            return HealthCheckResult(
                chain=chain,
                timestamp=datetime.now(),
                status=ChainStatus.UNKNOWN,
                error_msg="Chain not configured"
            )
        
        # Try primary RPC
        block_number, err = self._rpc_call(chain, 'eth_blockNumber')
        
        # Try backup RPC if primary failed
        rpc_used = 'primary'
        if err and cfg.rpc_backup:
            logger.warning(f"[{chain}] Primary RPC failed, trying backup...")
            block_number, err = self._rpc_call(chain, 'eth_blockNumber', rpc_url=cfg.rpc_backup)
            rpc_used = 'backup'
        
        # If both failed, return unhealthy
        if err:
            logger.error(f"[{chain}] ❌ UNREACHABLE: {err}")
            result = HealthCheckResult(
                chain=chain,
                timestamp=datetime.now(),
                status=ChainStatus.UNREACHABLE,
                error_msg=err,
                rpc_used=rpc_used
            )
            self._save_result(result)
            return result
        
        # Convert hex block number to int
        try:
            if isinstance(block_number, str):
                block_number = int(block_number, 16)
        except (ValueError, TypeError) as e:
            logger.error(f"[{chain}] Failed to parse block number: {block_number}")
            block_number = None
        
        # Estimate block age (last 100 blocks typically older than 1 second per block)
        block_age_seconds = None
        if block_number:
            estimated_blocks_since_genesis = block_number
            block_age_seconds = estimated_blocks_since_genesis * (cfg.block_time_seconds / 1000)
        
        # Get gas price
        gas_price_result, gas_err = self._rpc_call(chain, 'eth_gasPrice')
        gas_price = None
        if gas_price_result and not gas_err:
            try:
                gas_price_wei = int(gas_price_result, 16) if isinstance(gas_price_result, str) else gas_price_result
                gas_price = gas_price_wei / 1e9  # Convert to Gwei
            except (ValueError, TypeError):
                pass
        
        # Determine status
        status = ChainStatus.HEALTHY
        if gas_err or gas_price is None:
            status = ChainStatus.DEGRADED
            logger.warning(f"[{chain}] Gas price check failed: {gas_err}")
        
        result = HealthCheckResult(
            chain=chain,
            timestamp=datetime.now(),
            status=status,
            block_number=block_number,
            block_age_seconds=block_age_seconds,
            gas_price=gas_price,
            error_msg=gas_err,
            rpc_used=rpc_used
        )
        
        logger.info(f"[{chain}] ✅ HEALTHY | Block: {block_number} | Gas: {gas_price:.2f} Gwei | RPC: {rpc_used}")
        
        self._save_result(result)
        return result
    
    def _save_result(self, result: HealthCheckResult):
        """Save health check result to database"""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            
            c.execute("""
                INSERT INTO chain_health
                (chain, status, block_number, block_age_seconds, gas_price, error_msg, rpc_used)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                result.chain,
                result.status.value,
                result.block_number,
                result.block_age_seconds,
                result.gas_price,
                result.error_msg,
                result.rpc_used
            ))
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to save health check result: {e}")
    
    def check_all_chains(self) -> Dict[str, HealthCheckResult]:
        """Health check all configured chains"""
        logger.info(f"\n{'='*70}")
        logger.info(f"CHAIN HEALTH CHECK - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"{'='*70}\n")
        
        results = {}
        for chain_name in CHAINS.keys():
            results[chain_name] = self.check_chain(chain_name)
            time.sleep(0.5)  # Stagger requests
        
        self._print_summary(results)
        return results
    
    def _print_summary(self, results: Dict[str, HealthCheckResult]):
        """Print health check summary"""
        logger.info(f"\n{'='*70}")
        logger.info(f"SUMMARY")
        logger.info(f"{'='*70}")
        
        healthy = sum(1 for r in results.values() if r.status == ChainStatus.HEALTHY)
        degraded = sum(1 for r in results.values() if r.status == ChainStatus.DEGRADED)
        unreachable = sum(1 for r in results.values() if r.status == ChainStatus.UNREACHABLE)
        
        logger.info(f"✅ Healthy:      {healthy}")
        logger.info(f"⚠️  Degraded:     {degraded}")
        logger.info(f"❌ Unreachable:  {unreachable}")
        
        for chain, result in results.items():
            status_symbol = {
                ChainStatus.HEALTHY: '✅',
                ChainStatus.DEGRADED: '⚠️ ',
                ChainStatus.UNREACHABLE: '❌'
            }.get(result.status, '?')
            
            logger.info(
                f"{status_symbol} {chain:<12} | Block: {result.block_number or 'N/A':<10} | "
                f"Gas: {(f'{result.gas_price:.2f} Gwei' if result.gas_price else 'N/A'):<15} | "
                f"RPC: {result.rpc_used}"
            )
        
        logger.info(f"{'='*70}\n")

# ─────────────────────────────────────────────────────────────────────────
# CONTINUOUS MONITORING (Optional background thread)
# ─────────────────────────────────────────────────────────────────────────

class ChainMonitorDaemon:
    """Background monitor that checks chains periodically"""
    
    def __init__(self, db_path: str, check_interval_seconds: int = 60):
        self.db_path = db_path
        self.check_interval = check_interval_seconds
        self.checker = ChainHealthChecker(db_path)
        self.stop_event = Event()
        self.thread = None
    
    def start(self):
        """Start daemon thread"""
        logger.info(f"Starting ChainMonitorDaemon (interval: {self.check_interval}s)")
        self.thread = Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
    
    def stop(self):
        """Stop daemon thread"""
        logger.info("Stopping ChainMonitorDaemon")
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
    
    def _monitor_loop(self):
        """Continuous monitoring loop"""
        while not self.stop_event.is_set():
            try:
                self.checker.check_all_chains()
            except Exception as e:
                logger.error(f"Monitor loop error: {e}\n{traceback.format_exc()}")
            
            # Sleep in small increments so we can stop quickly
            for _ in range(self.check_interval):
                if self.stop_event.wait(1):
                    return

# ─────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINTS
# ─────────────────────────────────────────────────────────────────────────

def main_once(db_path: str):
    """Run health check once and exit"""
    checker = ChainHealthChecker(db_path)
    checker.check_all_chains()

def main_daemon(db_path: str, interval_seconds: int = 60):
    """Run continuous monitoring"""
    daemon = ChainMonitorDaemon(db_path, interval_seconds)
    daemon.start()
    
    try:
        logger.info(f"Daemon running. Press Ctrl+C to stop.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
        daemon.stop()

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Chain Health Monitor')
    parser.add_argument('db_path', help='Path to SQLite database (.db file)')
    parser.add_argument('--daemon', action='store_true', help='Run as continuous daemon')
    parser.add_argument('--interval', type=int, default=60, help='Check interval (seconds, default 60)')
    parser.add_argument('--chains', nargs='+', help='Specific chains to check (default: all)')
    
    args = parser.parse_args()
    
    if args.daemon:
        main_daemon(args.db_path, args.interval)
    else:
        main_once(args.db_path)
