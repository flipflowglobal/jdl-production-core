#!/usr/bin/env python3
"""
flash_supervisor.py — Process Supervisor for Flash Zero Gas System
Monitors daemon health, auto-restarts on crash, tracks revenue threshold,
notifies when withdrawal is ready. Follows jdl_core.py daemon pattern.
"""
import os
import sys
import time
import signal
import sqlite3
import logging
import subprocess
import threading
from pathlib import Path
from typing  import Optional

log = logging.getLogger('FlashSupervisor')

DATA_DIR = Path.home() / '.flash_zero_gas'
DB_PATH  = DATA_DIR / 'flash.db'
PID_FILE = DATA_DIR / 'daemon.pid'
LOG_FILE = DATA_DIR / 'daemon.log'

CHECK_INTERVAL   = 30    # seconds between health checks
RESTART_DELAY     = 10   # seconds before restart after crash
MAX_RESTARTS      = 20   # max restarts in 1 hour window
WITHDRAW_THRESH   = 1000.0  # USD

# ── COLORS ───────────────────────────────────────────────────────────────────────────
class C:
    R=   "\033[0m";  B=  "\033[1m"
    RED= "\033[31m"; GRN="\033[32m"; YLW="\033[33m"
    CYN= "\033[36m"; BGRN="\033[92m"; BYLW="\033[93m"
    BCYN="\033[96m"


class RestartHistory:
    def __init__(self, window_s: int = 3600):
        self.window  = window_s
        self.history = []

    def record(self):
        now = time.time()
        self.history = [t for t in self.history if now - t < self.window]
        self.history.append(now)

    def count(self) -> int:
        now = time.time()
        return sum(1 for t in self.history if now - t < self.window)


class RevenueMonitor:
    def __init__(self, db_path: Path = DB_PATH, threshold: float = WITHDRAW_THRESH):
        self.db_path   = db_path
        self.threshold = threshold
        self._notified = False

    def total_profit(self) -> float:
        try:
            with sqlite3.connect(self.db_path) as cx:
                row = cx.execute(
                    'SELECT COALESCE(SUM(net_usd),0) FROM executions WHERE success=1'
                ).fetchone()
                return float(row[0]) if row else 0.0
        except Exception:
            return 0.0

    def executions(self) -> int:
        try:
            with sqlite3.connect(self.db_path) as cx:
                row = cx.execute('SELECT COUNT(*) FROM executions WHERE success=1').fetchone()
                return int(row[0]) if row else 0
        except Exception:
            return 0

    def check_threshold(self) -> bool:
        total = self.total_profit()
        if total >= self.threshold and not self._notified:
            self._notified = True
            print(f'\n{C.BYLW}{C.B}*** WITHDRAWAL THRESHOLD REACHED ***{C.R}')
            print(f'  Total profit: {C.BGRN}${total:,.2f}{C.R}')
            print(f'  Call withdrawToken() or withdrawETH() on contract {C.CYN}{os.getenv("FLASH_CONTRACT_ADDRESS", "(not set)")}{C.R}\n')
            return True
        return False


class DaemonProcess:
    def __init__(self, script: str):
        self.script  = script
        self.proc: Optional[subprocess.Popen] = None
        self.history = RestartHistory()
        self.starts  = 0

    def start(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        log_fd = open(LOG_FILE, 'a')
        self.proc = subprocess.Popen(
            [sys.executable, self.script],
            stdout=log_fd,
            stderr=log_fd,
            preexec_fn=os.setsid if hasattr(os, 'setsid') else None
        )
        self.starts += 1
        with open(PID_FILE, 'w') as f:
            f.write(str(self.proc.pid))
        log.info(f'Daemon started PID={self.proc.pid}')

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.proc and self.is_alive():
            os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            self.proc.wait(timeout=10)
        if PID_FILE.exists():
            PID_FILE.unlink()

    def restart(self):
        self.history.record()
        self.stop()
        time.sleep(RESTART_DELAY)
        self.start()


class FlashSupervisor:
    def __init__(self, daemon_script: str = None):
        if daemon_script is None:
            daemon_script = str(Path(__file__).parent / 'flash_loan_zero_gas.py')
        self.daemon  = DaemonProcess(daemon_script)
        self.rev     = RevenueMonitor()
        self.running = False

    def _print_status(self):
        alive  = self.daemon.is_alive()
        total  = self.rev.total_profit()
        execs  = self.rev.executions()
        pct    = min(total / WITHDRAW_THRESH * 100, 100)
        status = f'{C.BGRN}RUNNING{C.R}' if alive else f'{C.RED}DEAD{C.R}'
        print(
            f'  [{time.strftime("%H:%M:%S")}] daemon={status}  '
            f'execs={execs}  revenue={C.BYLW}${total:,.2f}{C.R}/{C.CYN}${WITHDRAW_THRESH:,.0f}{C.R} ({pct:.1f}%)  '
            f'restarts={self.daemon.history.count()}/{MAX_RESTARTS}'
        )

    def run(self):
        print(f'{C.BCYN}{C.B}\n Flash Zero Gas — Supervisor\n{C.R}')
        self.running = True
        self.daemon.start()

        def _handle_sig(sig, _):
            print(f'\n{C.YLW}Supervisor shutting down…{C.R}')
            self.running = False
            self.daemon.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT,  _handle_sig)
        signal.signal(signal.SIGTERM, _handle_sig)

        while self.running:
            time.sleep(CHECK_INTERVAL)
            self._print_status()
            self.rev.check_threshold()

            if not self.daemon.is_alive():
                rc = self.daemon.history.count()
                if rc >= MAX_RESTARTS:
                    print(f'{C.RED}Max restarts ({MAX_RESTARTS}) in 1h — stopping.{C.R}')
                    self.running = False
                    break
                print(f'{C.YLW}Daemon died — restarting ({rc+1}/{MAX_RESTARTS})…{C.R}')
                self.daemon.restart()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    sup = FlashSupervisor()
    sup.run()
