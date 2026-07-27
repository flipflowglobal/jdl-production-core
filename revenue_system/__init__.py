"""Revenue tracking and RPC monitoring for flipflowglobal DeFi projects.

This is the standalone, project-agnostic revenue toolkit deployed to devices
(Termux/Ubuntu) by ``scripts/deploy_termux.sh``. It is intentionally decoupled
from ``python/jdl_flash`` so it can be dropped into any project directory that
only has a SQLite ``.db`` and stdlib + ``requests``/``web3`` available.

Public API:
    record_flash_arbitrage, record_withdrawal  -- write trade/withdrawal rows
    ChainHealthChecker, ChainMonitorDaemon      -- RPC health monitoring
"""
from .revenue_recording import record_flash_arbitrage, record_withdrawal
from .chain_monitor_fixed import ChainHealthChecker, ChainMonitorDaemon

__all__ = [
    "record_flash_arbitrage",
    "record_withdrawal",
    "ChainHealthChecker",
    "ChainMonitorDaemon",
]
