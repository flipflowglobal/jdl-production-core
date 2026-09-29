#!/usr/bin/env python3
"""
flash_supervisor.py — Process Supervisor
Monitors flash_loan_engine.py daemon, auto-restarts on crash,
alerts when $1000 withdrawal threshold is reached.
"""
import os, sys, time, signal, sqlite3, logging, subprocess, importlib, contextlib
from pathlib import Path

log = logging.getLogger('Supervisor')

# This module lives unpackaged at python/ root, next to the jdl_flash package
# (see pyproject.toml), so the package may not be on sys.path when we are
# loaded by path from cli.py / integrate.py.
_PYTHON_DIR = Path(__file__).resolve().parent
_RESOLVER_MODULE = 'jdl_flash.paths'

@contextlib.contextmanager
def _package_importable():
    """Put this file's directory on sys.path for the duration of one import.

    flash_supervisor.py lives unpackaged at python/ root, so `import jdl_flash`
    only resolves when that directory is importable. It is normally already on
    sys.path (an installed package, or cwd when run as `python3
    flash_supervisor.py`), and in that case this is a no-op.

    It is deliberately *scoped* and reverted on exit. An earlier revision
    inserted the path permanently and never removed it, which left the whole
    daemon with a module-shadowing surface for the rest of its life: anything
    that later prepends to sys.path, or drops a same-named module next to this
    file, silently changes what the running supervisor imports. Scoping keeps
    the window to the single import that needs it.
    """
    entry = str(_PYTHON_DIR)
    if entry in sys.path:
        yield
        return
    sys.path.insert(0, entry)
    try:
        yield
    finally:
        try:
            sys.path.remove(entry)
        except ValueError:  # pragma: no cover - another frame removed it first
            pass

def _resolve_paths():
    """Resolve (DATA_DIR, DB_PATH) from the one shared resolver.

    The supervisor sums the same `executions` table the engine writes and arms a
    real `withdrawToken()` off that sum, so it must never decide for itself
    which database it is looking at. Both paths come from `jdl_flash.paths`,
    which honours the FLASH_DB_PATH / JDL_FLASH_DB_PATH / FLASH_LOAN_DB_PATH env
    aliases and otherwise returns the production location — identical to the
    `Path.home()/'.flash_loan_engine'` this module used to hard-code. Setting no
    env var therefore changes nothing in production, and setting one redirects
    the supervisor and the engine together.

    That resolver is *production* code. An earlier revision imported
    `jdl_flash.test_db_guard` to borrow it, which meant this production module
    imported a test module — shipping the test module and its `sqlite3.connect`
    tripwire into the production wheel via pyproject's `packages = ["jdl_flash"]`,
    and forcing a global monkeypatch to be armed on import and disarmed again on
    every daemon start. That arm/disarm dance is precisely the fragility that
    let total_profit() report $0.00 forever: had the disarm ever been skipped or
    the ordering ever shifted, the tripwire would have refused the real ledger
    and total_profit()'s bare `except: return 0.0` would have hidden it while the
    daemon carried on trading. Importing a module that has no side effects
    removes the failure mode instead of defending against it.

    The fallback is the pre-existing production default. It is only reachable if
    the shared resolver cannot be imported at all — a broken install, where the
    supervisor could not launch `jdl_flash.flash_loan_engine` either — and it
    warns rather than failing, so this wiring can never stop the daemon starting.
    """
    fallback_dir = Path.home()/'.flash_loan_engine'
    fallback_db  = fallback_dir/'flash.db'
    try:
        with _package_importable():
            resolver = importlib.import_module(_RESOLVER_MODULE)
    except Exception as e:  # noqa: BLE001 - never block daemon startup
        log.warning(f'Shared DB-path resolver unavailable ({e}); '
                    f'falling back to {fallback_db}')
        return fallback_dir, fallback_db
    return resolver.resolve_engine_data_dir(), resolver.resolve_db_path()

DATA_DIR, DB_PATH = _resolve_paths()
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
        self.proc=subprocess.Popen([sys.executable,'-m',self.script],stdout=fd,stderr=fd,
            cwd=str(Path(__file__).resolve().parent),
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

TARGETS = {
    'engine': 'jdl_flash.flash_loan_engine',
    'swarm':  'jdl_flash.swarm_daemon',
}

def resolve_target(spec=None):
    """Resolve a supervision target to a `python -m`-able module path.

    Accepts a TARGETS key ('engine', 'swarm'), a raw module path (anything
    containing a dot, passed through as-is), or None (falls back to the
    SUPERVISOR_TARGET env var, defaulting to 'engine' — unchanged behavior).
    """
    spec = spec if spec is not None else os.getenv('SUPERVISOR_TARGET', 'engine')
    if spec in TARGETS:
        return TARGETS[spec]
    if '.' in spec:
        return spec
    return TARGETS['engine']

class FlashSupervisor:
    def __init__(self, script=None):
        if script is None: script=resolve_target()  # run via python -m
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
    # Optional CLI override: `python3 flash_supervisor.py swarm` supervises the
    # always-on parallel scanner instead of the legacy single-process engine
    # (SUPERVISOR_TARGET env var works the same way; the CLI arg wins if both are set).
    target = sys.argv[1] if len(sys.argv) > 1 else None
    FlashSupervisor(script=resolve_target(target)).run()
