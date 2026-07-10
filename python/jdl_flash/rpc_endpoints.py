"""
rpc_endpoints.py — the ONE definition of the Arbitrum RPC endpoint list.

Extracted so the engine (flash_loan_engine.build_rpc_endpoints) and `jdl
integrate` (integrate.check_rpc_endpoints) compute the exact same list from the
same rules and can never drift. Pure and dependency-free (no web3), so the
lightweight integrate path can import it without pulling in the whole engine.

The rules are the engine's long-standing behavior — reproduced here verbatim:
  • `_env_first` picks the first NON-EMPTY alias (before any validity check), so
    a placeholder RPC_URL shadows a later real ARB_RPC_URL (they're a group, and
    the first one set wins — even if it turns out invalid).
  • Explicit/numbered RPC URLs must pass `is_valid_rpc` (rejects the YOUR_ALCHEMY
    /YOUR_KEY placeholders and URLs containing spaces).
  • ALCHEMY_ARB_KEY / ALCHEMY_ARBITRUM_KEY (first non-empty) plus EVERY non-empty
    ALCHEMY_KEY_* are turned into arb-mainnet URLs, then run through the SAME
    `is_valid_rpc` filter as explicit URLs — so a placeholder key like
    YOUR_ALCHEMY_KEY_HERE (which would only 401) is dropped and never counted as
    a real source.
  • A public Arbitrum node is always appended last, and the whole list is
    de-duplicated preserving order.
"""
from __future__ import annotations

from typing import List, Mapping

ALCHEMY_ARB_URL = "https://arb-mainnet.g.alchemy.com/v2/{}"
PUBLIC_ARB_RPC = "https://arb1.arbitrum.io/rpc"
# Sort key for RPC_URL* names with non-numeric suffixes (e.g. RPC_URL_BACKUP);
# must be larger than any realistic numeric suffix so they sort after numbered ones.
_NON_NUMERIC_SUFFIX_SORT_KEY = 10 ** 9


def _env_first(env: Mapping[str, str], *names: str, default: str = "") -> str:
    """First non-empty (after strip) value among the alias names — the engine's
    `_env()`. Note: this does NOT skip placeholders; the first alias that is set
    wins, and validity is judged afterwards by the caller."""
    for name in names:
        val = env.get(name, "")
        if val and val.strip():
            return val.strip()
    return default


def is_valid_rpc(url: str) -> bool:
    """A usable explicit RPC URL: non-empty, no YOUR_ALCHEMY/YOUR_KEY placeholder,
    no spaces. Marker checks are case-INSENSITIVE (like env_autowire.is_placeholder),
    so a lower-case template value `.../your_alchemy_key_here` is rejected too."""
    low = url.lower()
    return bool(url) and "your_alchemy" not in low and "your_key" not in low and " " not in url.strip()


def build_rpc_endpoints(env: Mapping[str, str]) -> List[str]:
    """The de-duplicated Arbitrum RPC URL list, in the order the engine tries
    them. Pass os.environ (engine) or a parsed .env dict (integrate)."""
    eps: List[str] = []
    # 1) Alchemy — dedicated key group first, then every ALCHEMY_KEY_*. Build the
    #    URL and keep it only if valid, so a placeholder key (YOUR_ALCHEMY_KEY_HERE)
    #    — which would only 401 — is dropped, not counted as a real source.
    alch = _env_first(env, "ALCHEMY_ARB_KEY", "ALCHEMY_ARBITRUM_KEY")
    if alch:
        url = ALCHEMY_ARB_URL.format(alch)
        if is_valid_rpc(url):
            eps.append(url)
    for name, val in env.items():
        if name.startswith("ALCHEMY_KEY_") and val and val.strip():
            url = ALCHEMY_ARB_URL.format(val.strip())
            if is_valid_rpc(url):
                eps.append(url)
    # 2) Explicit URL group (first non-empty alias, then validated), then RPC_URLn.
    rpc = _env_first(env, "RPC_URL", "ARBITRUM_RPC_URL", "ARB_RPC_URL")
    if is_valid_rpc(rpc):
        eps.append(rpc.strip())
    numbered = []
    for name, val in env.items():
        if name.startswith("RPC_URL") and name != "RPC_URL" and is_valid_rpc(val):
            suffix = name[len("RPC_URL"):]
            numbered.append((int(suffix) if suffix.isdigit() else _NON_NUMERIC_SUFFIX_SORT_KEY, val.strip()))
    for _, url in sorted(numbered):
        eps.append(url)
    # 3) RPC_FALLBACKS comma list.
    for url in (_env_first(env, "RPC_FALLBACKS", default="") or "").split(","):
        if is_valid_rpc(url):
            eps.append(url.strip())
    # 4) Public node, always appended last.
    eps.append(PUBLIC_ARB_RPC)
    seen, out = set(), []
    for url in eps:
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out
