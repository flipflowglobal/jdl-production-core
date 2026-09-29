"""
test_web3_contract.py — the import contract between this engine and ``web3``.

The failure this exists to catch
-------------------------------
``flash_loan_engine.py`` opens with::

    try:
        from web3 import Web3
        from web3.middleware import geth_poa_middleware
        WEB3_OK = True
        ...real shims and real constants...
    except ImportError:
        WEB3_OK = False
        def _gas_p(w): return 0.1
        def _nonce(w, a): return 0
        def _chain_id(w): return 0
        def _balance(w, a): return 0
        def _is_connected(w): return False
        def _inject_poa(w): pass
        ...

and the only broadcast site is gated on ``if CONTRACT and WEB3_OK and
LIVE_EXEC``. So a web3 that is missing — or, far more likely, a web3 that is
the *wrong major version* — does not crash the daemon. It sets ``WEB3_OK =
False``, the gate can never be satisfied, and the engine runs forever, scanning
and logging, structurally incapable of trading a single transaction. Nothing on
the console says "web3 is broken": the operator sees a healthy loop.

The specific break this repo actually shipped
---------------------------------------------
``python/pyproject.toml`` and ``python/requirements_flash.txt`` both pinned
``web3==8.0.0`` while every comment in both files documented 6.20.4. web3 7.0.0
renamed ``geth_poa_middleware`` to ``ExtraDataToPOAMiddleware`` and removed the
old name, so the engine's import raises ``ImportError`` on any 7.x or later.
(8.0.0 additionally declares ``Requires-Python: <4,>=3.10`` while this project
supports 3.9, so that exact pin cannot be installed on a supported interpreter
at all.) The whole test suite stayed green, because ``test_swarm_wiring.py``
*force-sets* ``e.WEB3_OK = True`` (line 61) to exercise the multi-wallet lanes
and therefore cannot observe the value the engine actually computed at import.
The suite was structurally incapable of reporting the one condition under which
the system cannot make money.

Why this is a separate file rather than more wiring cases
---------------------------------------------------------
``test_swarm_wiring.py`` must keep forcing ``WEB3_OK = True``: its subject is
lane routing, and it needs the broadcast gate open. That is a legitimate mock of
*its* dependency, but it makes that file the wrong home for "is the dependency
real". This file asserts the real thing and is registered in
``jdl_flash.cli._PACKAGED_TESTS`` so ``jdl test`` (and CI, which runs exactly
that list) executes it.

What it asserts
---------------
1. ``web3`` imports, and the engine's exact import line works.
2. The engine's own ``WEB3_OK`` is ``True`` — read from the real module, never
   assigned. Nothing in this file monkeypatches ``WEB3_OK``; that is the entire
   point.
3. The engine's module globals still hold the *real* ``geth_poa_middleware``
   object, and the ``except ImportError`` stub set is not the one bound. This is
   behavioural, not a flag read: each shim is exercised against a fake ``w3``
   and must read the value the fake provides, which is impossible for the stubs
   (they return the constants ``0.1`` / ``0`` / ``0`` / ``False`` / ``None``).
4. The two dependency files declare the *same* web3 pin, and that pin is the
   version actually installed. This is pure repo-internal consistency, so it
   catches the comment/pin drift above even on a machine where web3 happens to
   be fine.
5. The detector has teeth: a child interpreter in which ``web3`` is made
   un-importable must report ``WEB3_OK = False`` *and* land in the stub branch.
   If that probe ever stopped reproducing the failure, check 2 would be
   asserting something unfalsifiable, so the probe is part of the suite rather
   than a one-off manual experiment.

Safety
------
* **No database.** The engine opens ``sqlite3.connect`` only inside functions,
  never at import time, and this suite calls none of them. Belt and braces, it
  imports ``jdl_flash.test_db_guard`` before the engine: that module installs a
  process-wide tripwire on ``sqlite3.connect`` as an import side effect, so any
  future change that made engine import touch the ledger would raise
  ``LiveDatabaseError`` here instead of writing to the live revenue database.
  This is the same technique ``conftest.py`` and ``python/sitecustomize.py``
  already use; see ``test_db_guard.py`` for the full rationale.
* **No network.** Nothing here constructs a provider or dials an RPC. Importing
  ``web3`` is pure module loading; the fake ``w3`` objects below are local
  classes with no socket in them.

Run: cd python && python3 jdl_flash/test_web3_contract.py
"""
import importlib
import os
import re
import subprocess
import sys
from pathlib import Path

# Path bootstrap. Every suite in jdl_flash/ starts with this; without it a bare
# `python3 jdl_flash/test_web3_contract.py` (how `jdl test` and CI run it) puts
# jdl_flash/ — not its parent — on sys.path and dies with ModuleNotFoundError.
_PYTHON_DIR = str(Path(__file__).resolve().parent.parent)
if _PYTHON_DIR not in sys.path:
    sys.path.insert(0, _PYTHON_DIR)

# Importing the guard arms the process-wide sqlite3.connect tripwire. It must
# happen before the engine is imported, and it is deliberately the first
# third-party import in this file.
from jdl_flash import test_db_guard  # noqa: E402,F401  (import arms the tripwire)

#: The exact import the engine performs, kept as a literal string so the test
#: fails if the engine's import line is ever changed without this file being
#: updated to match. Checked against the engine source in :func:`_check_source`.
ENGINE_IMPORT = "from web3.middleware import geth_poa_middleware"

#: The repository's own dependency files. Their web3 pins are read from disk,
#: never hardcoded here, so this test tracks whatever they actually say.
_DECLARED_PIN_FILES = ("pyproject.toml", "requirements_flash.txt")

# Matches a real dependency declaration, never a mention inside prose. Both
# comments in these files discuss web3 versions at length, so a naïve search for
# `web3==` would happily read a sentence instead of the pin.
_REQ_PIN_RE = re.compile(r"^web3==([0-9][^\s#;]*)")
_TOML_BLOCK_RE = re.compile(r"(?ms)^dependencies\s*=\s*\[(.*?)\]")
_TOML_PIN_RE = re.compile(r"web3==([0-9][^\"'\s,\]]*)")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _requirements_pin(text: str):
    """The web3 pin from a pip requirements file, ignoring comments."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        found = _REQ_PIN_RE.match(stripped)
        if found:
            return found.group(1)
    return None


def _pyproject_pin(text: str):
    """The web3 pin from pyproject.toml's ``[project].dependencies`` array."""
    block = _TOML_BLOCK_RE.search(text)
    if not block:
        return None
    found = _TOML_PIN_RE.search(block.group(1))
    return found.group(1) if found else None


def _declared_pins(python_dir: Path) -> dict:
    """Map each dependency file to the web3 pin it declares, or ``None``.

    Parsed from the real files rather than hardcoded, so this test tracks the
    pins instead of freezing a second copy of them that could itself drift. A
    file that is absent (wheel install) or has no pin maps to ``None``, which
    callers below treat as "nothing to check", not as a failure.
    """
    return {
        "pyproject.toml": _pyproject_pin(_read_text(python_dir / "pyproject.toml")),
        "requirements_flash.txt": _requirements_pin(
            _read_text(python_dir / "requirements_flash.txt")
        ),
    }


# ────────────────────────────────────────────────────────────────────────────
#  The check bodies
# ────────────────────────────────────────────────────────────────────────────

def _check_pin_files(check, python_dir: Path) -> None:
    """The two dependency files must declare the same, installable pin."""
    pins = _declared_pins(python_dir)
    present = [name for name in _DECLARED_PIN_FILES if (python_dir / name).is_file()]
    if not present:
        # A wheel install has no pyproject.toml/requirements_flash.txt next to
        # the package. Nothing to compare, and nothing to fail: the import
        # checks below still run and are the real contract.
        check(True, "web3 pin files present (skipped: not a repo checkout, "
                    "only a wheel/site-packages install)")
        return

    for name in present:
        pin = pins.get(name)
        check(pin is not None,
              f"{name} pins web3 exactly",
              f"no real 'web3==<version>' declaration found in {name}")

    if len(present) == 2 and all(pins.get(n) for n in present):
        a, b = pins["pyproject.toml"], pins["requirements_flash.txt"]
        check(a == b,
              "pyproject.toml and requirements_flash.txt pin the same web3 version",
              f"pyproject.toml={a} requirements_flash.txt={b}")
    elif len(present) == 1:
        name = present[0]
        check(True, f"only one pin file present ({name}); cross-file check skipped")


def _check_installed_matches_pin(check, python_dir: Path) -> None:
    """The installed web3 must be the version the repo pins, exactly."""
    pins = {n: p for n, p in _declared_pins(python_dir).items() if p}
    if not pins:
        check(True, "installed web3 matches the declared pin (skipped: no pin file)")
        return
    # pyproject.toml is the package's own declared metadata, so it is the
    # authority when the two files disagree; the disagreement itself is
    # reported by _check_pin_files, not silently resolved here.
    declared = pins.get("pyproject.toml") or sorted(pins.values())[0]
    try:
        from importlib import metadata

        installed = metadata.version("web3")
    except Exception as exc:  # noqa: BLE001 - any failure is itself the finding
        check(False, f"installed web3 matches the declared pin (web3=={declared})",
              f"could not read the installed version: {type(exc).__name__}: {exc}")
        return
    check(installed == declared,
          f"installed web3 matches the declared pin (web3=={declared})",
          f"installed web3=={installed}")


def _check_import_path(check):
    """web3 imports, and the engine's exact import line resolves."""
    try:
        web3_mod = importlib.import_module("web3")
    except Exception as exc:  # noqa: BLE001
        check(False, "web3 is importable",
              f"{type(exc).__name__}: {exc} — the engine cannot broadcast anything")
        return None
    check(True, f"web3 is importable (version {getattr(web3_mod, '__version__', 'unknown')})")

    # Guarded like the import above: a suite that dies with a traceback reports
    # less than one that names the check that failed.
    try:
        middleware = importlib.import_module("web3.middleware")
    except Exception as exc:  # noqa: BLE001
        check(False, "web3.middleware exports geth_poa_middleware",
              f"{type(exc).__name__}: {exc}")
        return None

    has_poa = hasattr(middleware, "geth_poa_middleware")
    check(has_poa,
          "web3.middleware exports geth_poa_middleware",
          "absent — web3 7.0.0+ renamed it to ExtraDataToPOAMiddleware and removed "
          "the old name; the engine's import raises ImportError and WEB3_OK goes False")
    if not has_poa:
        return None
    check(callable(middleware.geth_poa_middleware),
          "geth_poa_middleware is callable (injectable as a middleware layer)")
    return middleware


def _check_engine(check) -> None:
    """The engine must import with its real WEB3_OK and its real shims."""
    try:
        engine = importlib.import_module("jdl_flash.flash_loan_engine")
    except Exception as exc:  # noqa: BLE001
        check(False, "jdl_flash.flash_loan_engine imports", f"{type(exc).__name__}: {exc}")
        return None

    check(True, "jdl_flash.flash_loan_engine imports")

    # The real flag, read from the module. Nothing here assigns to it — that is
    # the whole point of this file.
    web3_ok = getattr(engine, "WEB3_OK", None)
    check(web3_ok is True,
          "engine's own WEB3_OK is True (real import, not monkeypatched)",
          f"WEB3_OK={web3_ok!r} — the engine is running with the ImportError stub "
          f"set and can never satisfy 'if CONTRACT and WEB3_OK and LIVE_EXEC'")

    # The engine must still be holding the real middleware object. Under the
    # except branch the name is simply never bound, so this is a direct,
    # un-mockable assertion of which branch ran.
    bound = getattr(engine, "geth_poa_middleware", None)
    check(bound is not None,
          "engine's globals bind the real geth_poa_middleware",
          "the name is absent, which only happens on the except ImportError branch")
    if bound is not None:
        try:
            from web3.middleware import geth_poa_middleware
            check(bound is geth_poa_middleware,
                  "engine's geth_poa_middleware is the same object web3.middleware exports")
        except ImportError as exc:
            check(False, "engine's geth_poa_middleware is the same object web3.middleware exports",
                  str(exc))

    _check_shims(check, engine)
    return engine


class _FakeEth:
    """A stand-in for ``w3.eth`` using the modern (snake_case) web3 API.

    The attribute names here are the ones the engine's shims actually read
    (``gas_price``, ``chain_id``, ``block_number``, ``get_transaction_count``,
    ``get_balance``) — not merely plausible ones. A fake that omitted a name
    would send the shim down its v5 fallback and raise ``AttributeError``, which
    is a failure of the fake rather than of the code under test.
    """

    chain_id = 42161
    gas_price = 1_234_567
    block_number = 4242

    @staticmethod
    def get_transaction_count(_addr):
        return 7

    @staticmethod
    def get_balance(_addr):
        return 999


class _FakeOnion:
    """Records middleware injections instead of performing them."""

    def __init__(self):
        self.injected = []

    def inject(self, middleware, layer=0):
        self.injected.append((middleware, layer))


class _FakeW3:
    """Enough of a ``Web3`` for the shims to read from, and nothing more.

    Deliberately attribute-based rather than a Mock: a Mock would satisfy almost
    any attribute access, so it could not distinguish the real shims from the
    ``except ImportError`` stubs. These hold concrete values that differ from
    every stub constant, so each check below is a real discriminator.
    """

    def __init__(self):
        self.eth = _FakeEth()
        self.middleware_onion = _FakeOnion()

    @staticmethod
    def is_connected():
        return True


def _shim_result(callable_, *args):
    """Invoke a shim, returning ``(ok, value_or_exception)``.

    The real shims can raise ``AttributeError`` against a partial object; the
    stubs never do, they return a constant. Collapsing both outcomes into one
    comparable value keeps the call sites below readable.
    """
    try:
        return True, callable_(*args)
    except Exception as exc:  # noqa: BLE001
        return False, exc


def _check_shims(check, engine) -> None:
    """Exercise the v5/v6 shims so the stub set cannot be mistaken for them.

    The ``except ImportError`` branch redefines these same names as constants.
    Comparing each shim's output against the value the fake provides is a
    behavioural test of which definition is bound — independent of the
    ``WEB3_OK`` flag entirely.
    """
    w3 = _FakeW3()
    addr = "0x0000000000000000000000000000000000000000"

    cases = (
        ("_gas_p", (w3,), 1_234_567, 0.1, "_gas_p reads w3.eth.gas_price, not the 0.1 stub"),
        ("_chain_id", (w3,), 42161, 0, "_chain_id reads w3.eth.chain_id, not the 0 stub"),
        ("_blk", (w3,), 4242, 0, "_blk reads the block number, not the 0 stub"),
        ("_nonce", (w3, addr), 7, 0, "_nonce reads the transaction count, not the 0 stub"),
        ("_balance", (w3, addr), 999, 0, "_balance reads the balance, not the 0 stub"),
        ("_is_connected", (w3,), True, False, "_is_connected reports the provider state, not the False stub"),
    )
    for name, args, expected, stub, msg in cases:
        shim = getattr(engine, name, None)
        if shim is None:
            check(False, msg, f"engine has no {name}")
            continue
        ok, value = _shim_result(shim, *args)
        if not ok:
            check(False, msg, f"{name} raised {type(value).__name__}: {value}")
            continue
        check(value == expected and value != stub, msg, f"{name} returned {value!r}")

    # _inject_poa is the one shim that is a no-op on the stub branch, so it is
    # checked by what it injected rather than by a return value.
    inject_poa = getattr(engine, "_inject_poa", None)
    if inject_poa is None:
        check(False, "_inject_poa actually injects the POA middleware, not the no-op stub")
    else:
        _shim_result(inject_poa, w3)
        injected = w3.middleware_onion.injected
        expected = getattr(engine, "geth_poa_middleware", None)
        check(len(injected) == 1 and injected[0][0] is expected and injected[0][1] == 0,
              "_inject_poa actually injects the POA middleware, not the no-op stub",
              f"injected={injected!r}")


def _check_source(check, python_dir: Path) -> None:
    """The engine's import line must still be the one this file tests."""
    source = _read_text(python_dir / "jdl_flash" / "flash_loan_engine.py")
    if not source:
        check(True, "engine source still contains the import under test "
                    "(skipped: source not readable)")
        return
    check(ENGINE_IMPORT in source,
          f"engine source still contains `{ENGINE_IMPORT}`",
          "the engine's import changed; update ENGINE_IMPORT in this test to match, "
          "or the import contract here is no longer the engine's real one")


# ────────────────────────────────────────────────────────────────────────────
#  Teeth: prove the detection can actually detect
# ────────────────────────────────────────────────────────────────────────────
#
# Every check above is a claim about a state the engine can reach. Without this
# probe the suite would still pass if the engine were changed to hardcode
# WEB3_OK = True — the assertions would have quietly stopped testing anything.
# The probe reproduces the real failure (web3 un-importable) in a child
# interpreter and requires the engine to land on the stub branch.

_BLOCKED_PROBE = r'''
import sys

class _BlockWeb3:
    """A meta-path finder that makes `import web3` fail, the way a missing or
    too-new web3 does: ImportError raised at the engine's own call site."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "web3" or fullname.startswith("web3."):
            raise ImportError("web3 is blocked by test_web3_contract's probe")
        return None

    # find_module is the pre-3.4 hook; harmless, and keeps the intent obvious.
    find_module = find_spec

sys.meta_path.insert(0, _BlockWeb3())

# Same ordering as this file: guard first, engine second.
import jdl_flash.test_db_guard  # noqa: F401
import jdl_flash.flash_loan_engine as e

print("WEB3_OK=%r" % (e.WEB3_OK,))
print("STUB_GAS_P=%r" % (e._gas_p(None),))
print("STUB_INJECT_POA_BOUND=%r" % (e._inject_poa.__name__,))
'''


def _check_detector_has_teeth(check, python_dir: Path) -> None:
    """A blocked-web3 child must reproduce the failure this suite exists for."""
    env = dict(os.environ)
    existing = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
    if str(python_dir) not in existing:
        existing.insert(0, str(python_dir))
    env["PYTHONPATH"] = os.pathsep.join(existing)

    try:
        proc = subprocess.run(
            [sys.executable, "-c", _BLOCKED_PROBE],
            capture_output=True, text=True, cwd=str(python_dir), env=env, timeout=120,
        )
    except Exception as exc:  # noqa: BLE001
        check(False, "a web3-blocked child reproduces WEB3_OK = False",
              f"probe could not run: {type(exc).__name__}: {exc}")
        return

    out = proc.stdout
    check(proc.returncode == 0 and "WEB3_OK=False" in out,
          "a web3-blocked child reproduces WEB3_OK = False",
          (out + proc.stderr).strip().splitlines()[-1] if (out or proc.stderr) else
          f"probe exited {proc.returncode} with no output")
    check("STUB_GAS_P=0.1" in out,
          "…and lands on the except-ImportError stub set, not merely a false flag",
          f"probe stdout: {out.strip()!r}")


# ────────────────────────────────────────────────────────────────────────────
#  Suite
# ────────────────────────────────────────────────────────────────────────────

def main():
    passed = failed = 0

    def check(cond, msg, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  ✓ {msg}")
        else:
            failed += 1
            print(f"  ✗ {msg}" + (f"  ({detail})" if detail else ""))

    python_dir = Path(_PYTHON_DIR)
    print("test_web3_contract — engine/web3 import contract")

    # Repo-level declarations first: cheap, and they explain any later failure.
    _check_pin_files(check, python_dir)
    _check_installed_matches_pin(check, python_dir)
    _check_source(check, python_dir)

    # The real import, then the real engine flag and shims.
    _check_import_path(check)
    _check_engine(check)

    # ...and proof that those assertions can distinguish a working web3 from a
    # broken one, so a green run means something.
    _check_detector_has_teeth(check, python_dir)

    print(f"\nResults: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
