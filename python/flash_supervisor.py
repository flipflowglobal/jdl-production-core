#!/usr/bin/env python3
"""
flash_supervisor.py — Process Supervisor
Monitors flash_loan_engine.py daemon, auto-restarts on crash,
alerts when $1000 withdrawal threshold is reached.
"""
import os, sys, time, signal, sqlite3, logging, subprocess
from pathlib import Path

log = logging.getLogger('Supervisor')
DATA_DIR = Path.home()/'.flash_loan_engine'
DB_PATH  = DATA_DIR/'flash.db'
PID_FILE = DATA_DIR/'daemon.pid'
LOG_FILE = DATA_DIR/'daemon.log'

CHECK_S      = 30
RESTART_S    = 10
MAX_RESTARTS = 20
THRESHOLD    = 1000.0

class C:
    R="\033[0m"; B="\033[1m"; RED="\033[31m"; GRN="\033[32m"
    YLW="\033[33m"; CYN="\033[36m"; BGRN="\033[92m"; BYLW="\033[93m"; BCYN="\033[96m"

def total_profit() -> float:
    try:
        con=sqlite3.connect(DB_PATH)
        r=con.execute('SELECT COALESCE(SUM(net_usd),0) FROM executions WHERE success=1').fetchone()
        con.close(); return float(r[0])
    except: return 0.0

def exec_count() -> int:
    try:
        con=sqlite3.connect(DB_PATH)
        r=con.execute('SELECT COUNT(*) FROM executions WHERE success=1').fetchone()
        con.close(); return int(r[0])
    except: return 0

class DaemonProc:
    def __init__(self, script):
        self.script=script; self.proc=None; self._hist=[]; self._starts=0
    def start(self):
        DATA_DIR.mkdir(parents=True,exist_ok=True)
        fd=open(LOG_FILE,'a')
        self.proc=subprocess.Popen([sys.executable,self.script],stdout=fd,stderr=fd,
            preexec_fn=os.setsid if hasattr(os,'setsid') else None)
        self._starts+=1
        PID_FILE.write_text(str(self.proc.pid))
        log.info(f'Daemon PID={self.proc.pid}')
    def alive(self): return self.proc is not None and self.proc.poll() is None
    def stop(self):
        if self.proc and self.alive():
            try: os.killpg(os.getpgid(self.proc.pid),signal.SIGTERM); self.proc.wait(timeout=8)
            except: pass
        if PID_FILE.exists(): PID_FILE.unlink()
    def restart(self):
        now=time.time(); self._hist=[t for t in self._hist if now-t<3600]; self._hist.append(now)
        self.stop(); time.sleep(RESTART_S); self.start()
    def restart_count(self): now=time.time(); return sum(1 for t in self._hist if now-t<3600)

class FlashSupervisor:
    def __init__(self, script=None):
        if script is None: script=str(Path(__file__).parent/'flash_loan_engine.py')
        self.d=DaemonProc(script); self._notified=False

    def _status(self):
        tot=total_profit(); ex=exec_count()
        pct=min(tot/THRESHOLD*100,100)
        bar=int(pct/5); pb=f"[{'#'*bar}{'.'*(20-bar)}]"
        st=f'{C.BGRN}RUNNING{C.R}' if self.d.alive() else f'{C.RED}DEAD{C.R}'
        print(f'  [{time.strftime("%H:%M:%S")}] daemon={st}  '
              f'execs={C.BYLW}{ex}{C.R}  '
              f'revenue={C.BGRN}${tot:,.2f}{C.R}/{C.CYN}${THRESHOLD:,.0f}{C.R} {pb} ({pct:.0f}%)  '
              f'restarts={self.d.restart_count()}/{MAX_RESTARTS}')
        if tot>=THRESHOLD and not self._notified:
            self._notified=True
            print(f'\n  {C.BYLW}{C.B}*** WITHDRAWAL THRESHOLD REACHED ***{C.R}')
            print(f'  Call withdrawToken() on contract: {C.BCYN}{os.getenv("FLASH_CONTRACT_ADDRESS","(not set)")}{C.R}\n')

    def run(self):
        print(f'{C.BCYN}{C.B}\n  Flash Loan Engine — Supervisor\n{C.R}')
        self.d.start()
        def _sig(s,_): self.d.stop(); sys.exit(0)
        signal.signal(signal.SIGINT,_sig); signal.signal(signal.SIGTERM,_sig)
        while True:
            time.sleep(CHECK_S)
            self._status()
            if not self.d.alive():
                if self.d.restart_count()>=MAX_RESTARTS:
                    print(f'{C.RED}Max restarts reached. Stopping.{C.R}'); break
                print(f'{C.YLW}Daemon died — restarting…{C.R}')
                self.d.restart()

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')
    FlashSupervisor().run()
