"""
env_autowire.py — universal, zero-prompt .env wiring.

The problem this solves: a fresh install needs PRIVATE_KEY, ALCHEMY_ARB_KEY,
FLASH_CONTRACT_ADDRESS etc. in ~/jdl/.env, and very often the *real* values
already exist somewhere on the machine — a previous checkout, a sibling repo,
an old ~/.env from a different project directory — just not in the one file
this system reads. Nobody should have to hunt those down and copy-paste them
by hand.

Algorithm (no directory names hard-coded, no user input):
  1. Ensure the canonical target file (~/jdl/.env) exists, seeded from
     .env.template if missing.
  2. Work out which keys in it are still unset or holding an obvious
     placeholder (empty, "0x000...0", "<...>", "YOUR_...", etc.).
  3. Walk every directory reachable from the repo root and $HOME (skipping
     .git/node_modules/venvs/build output — the only places .env files with
     real values could ever live are project checkouts and a user's home
     directory), collecting every file matching `.env*`.
  4. For each still-missing key, scan those files one at a time, in a stable
     order, and take the first real (non-placeholder) value found. Never
     invents a value nobody set anywhere — those keys are reported back as
     unresolved so a human can fill them in.
  5. Writes results back in place: existing lines get their value replaced,
     nothing is duplicated, keys with no match anywhere are left untouched.
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Dict, Iterable, List, Optional

CANONICAL_ENV = Path.home() / "jdl" / ".env"

# Directories that never contain a real .env value worth harvesting, and are
# often huge — pruning them keeps the walk fast and avoids vendored/generated
# copies of template files.
_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".flash_venv",
    "dist", "build", "out", "target", ".cache", ".mypy_cache", ".pytest_cache",
    ".pytype", ".tox", ".cargo", ".rustup", ".npm", "site-packages",
    ".Trash", "Trash", "AppData", "Library",
}

# Placeholders that must be the *entire* value (an otherwise-real-looking
# string shouldn't be flagged just because it happens to start with one of
# these).
_FULL_PLACEHOLDER_RE = re.compile(r"^$|^0x0*$|^<.*>$|^xxx+$|^todo$", re.IGNORECASE)

# Placeholders that can appear anywhere in the value — .env.template's real
# default is `RPC_URL=https://arb-mainnet.g.alchemy.com/v2/YOUR_ALCHEMY_KEY_HERE`,
# with the marker mid-string, not at position 0. Matches the same convention
# flash_loan_engine.py already uses (`_valid_rpc`: `'YOUR_ALCHEMY' not in u`).
_SUBSTRING_PLACEHOLDER_RE = re.compile(r"your[_-]|change[_-]?me|replace[_-]?me", re.IGNORECASE)

_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def is_placeholder(value: str) -> bool:
    v = value.strip().strip('"').strip("'")
    return bool(_FULL_PLACEHOLDER_RE.match(v) or _SUBSTRING_PLACEHOLDER_RE.search(v))


def _strip_value(raw: str) -> str:
    val = raw.split(" #", 1)[0].rstrip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        val = val[1:-1]
    return val.strip()


def parse_env_file(path: Path) -> Dict[str, str]:
    """key -> value for every KEY=value line in `path`. Best-effort: unreadable
    or binary files just yield no keys rather than raising."""
    values: Dict[str, str] = {}
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return values
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _LINE_RE.match(stripped)
        if not m:
            continue
        values[m.group(1)] = _strip_value(m.group(2))
    return values


# How many directory levels below each root to descend. $HOME can contain an
# effectively unbounded tree (caches, unrelated projects, media libraries);
# without a cap a fresh install's first `jdl install`/setup.sh run could walk
# all of it before wiring a single value. Project checkouts (where real .env
# files actually live) are rarely more than a few levels deep, so this stays
# generous enough to find them without scanning the whole disk.
_MAX_WALK_DEPTH = 6


def find_env_files(roots: Iterable[Path], max_depth: int = _MAX_WALK_DEPTH) -> List[Path]:
    """Every `.env*`-named file within `max_depth` directory levels of `roots`,
    deduplicated, heavy/irrelevant directories pruned rather than merely
    skipped (so the walk never descends into them at all)."""
    seen = set()
    found: List[Path] = []
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        root_depth = len(root.resolve().parts)
        for dirpath, dirnames, filenames in os.walk(root):
            depth = len(Path(dirpath).resolve().parts) - root_depth
            if depth >= max_depth:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fn in filenames:
                if fn == ".env" or fn.startswith(".env."):
                    p = Path(dirpath) / fn
                    resolved = p.resolve()
                    if resolved in seen:
                        continue
                    seen.add(resolved)
                    found.append(p)
    return found


def repo_root() -> Path:
    # python/jdl_flash/env_autowire.py -> python/ -> repo root
    return Path(__file__).resolve().parent.parent.parent


def default_search_roots() -> List[Path]:
    roots = [repo_root()]
    home = Path.home()
    if home not in roots:
        roots.append(home)
    return roots


def default_template() -> Path:
    return repo_root() / ".env.template"


def _sort_key(path: Path) -> tuple:
    # Templates/examples are last resort — they're the source of placeholders
    # in the first place, so a real value living elsewhere always wins.
    name = path.name.lower()
    is_template = "template" in name or "example" in name
    return (is_template, str(path))


def autowire(
    target: Path = CANONICAL_ENV,
    template: Optional[Path] = None,
    search_roots: Optional[Iterable[Path]] = None,
    quiet: bool = False,
) -> Dict[str, object]:
    """Fill every placeholder/unset key in `target` from any `.env*` file
    found under `search_roots`, one key at a time, first real value wins.
    Returns {"filled": {key: source_path}, "unresolved": [key, ...]}.
    """
    template = template or default_template()
    target = Path(target)

    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(template.read_text() if template.is_file() else "")
    os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)  # 0o600 — always enforce, even on upgrade

    template_keys = list(parse_env_file(template).keys()) if template.is_file() else []
    current = parse_env_file(target)
    all_keys = list(dict.fromkeys(template_keys + list(current.keys())))
    missing = [k for k in all_keys if is_placeholder(current.get(k, ""))]

    roots = list(search_roots) if search_roots is not None else default_search_roots()
    candidates = sorted(
        (p for p in find_env_files(roots) if p.resolve() != target.resolve()),
        key=_sort_key,
    )

    filled: Dict[str, tuple] = {}
    remaining = list(missing)
    for key in missing:
        for cand in candidates:
            val = parse_env_file(cand).get(key)
            if val and not is_placeholder(val):
                filled[key] = (val, str(cand))
                remaining.remove(key)
                break

    if filled:
        _write_back(target, filled)

    if not quiet:
        for key, (val, src) in filled.items():
            shown = val if len(val) <= 10 else val[:6] + "…"
            print(f"  ✓ {key} <- {shown}  (found in {src})")
        for key in remaining:
            print(f"  ✗ {key} still unresolved — not found in any .env file on this machine")

    return {"filled": {k: v[1] for k, v in filled.items()}, "unresolved": remaining}


def set_values(
    values: Dict[str, str],
    target: Path = CANONICAL_ENV,
    template: Optional[Path] = None,
) -> List[str]:
    """Persist explicit, user-supplied key=value pairs into `target`, in place
    (existing lines replaced, no duplicates), enforcing 0o600 — the write path
    behind interactive `.env` entry. Seeds the file from the template if it
    doesn't exist yet. Blank or placeholder inputs are skipped, so pressing
    Enter to keep an existing value never clobbers a real one. Returns the
    sorted list of keys actually written.
    """
    template = template or default_template()
    target = Path(target)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(template.read_text() if template.is_file() else "")
    os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)  # 0o600 — enforce before writing secrets

    to_write = {
        k: (str(v), "user input")
        for k, v in values.items()
        if v is not None and str(v).strip() and not is_placeholder(str(v))
    }
    if to_write:
        _write_back(target, to_write)
    return sorted(to_write)


def _write_back(target: Path, filled: Dict[str, tuple]) -> None:
    lines = target.read_text().splitlines()
    written = set()
    out_lines = []
    for line in lines:
        stripped = line.strip()
        m = _LINE_RE.match(stripped) if stripped and not stripped.startswith("#") else None
        key = m.group(1) if m else None
        if key in filled:
            if key in written:
                continue  # drop duplicate lines instead of writing the value twice
            out_lines.append(f"{key}={filled[key][0]}")
            written.add(key)
        else:
            out_lines.append(line)
    for key, (val, _src) in filled.items():
        if key not in written:
            out_lines.append(f"{key}={val}")
    target.write_text("\n".join(out_lines) + "\n")
    os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)  # keep 0o600 after rewrite
