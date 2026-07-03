"""
revenue_reconciliation.py — self-audit for stuck funds in NexusFlashReceiver.

Rebuilt from an uploaded reference design after review surfaced it didn't fit
this system: it assumed a multi-chain treasury-style contract holding a balance
that should track a `flash_trades` ledger table. Neither assumption holds here —
this system is Arbitrum-only, and NexusFlashReceiver is a pass-through executor
that sweeps 100% of profit to the owner on every call (see NexusFlashReceiver.sol
_sweep()), so its balance of any token should be ~0 between trades.

That reframes what "reconciliation" means for this contract: not "does on-chain
balance match a ledger", but "does the contract currently hold ANY balance it
shouldn't" — which, if true, means funds are stuck (a partially-failed sequence,
or an unswept sweep target) and rescueTokens()/rescueETH() should be used. This
module is the monitoring half of that story: it tells you WHEN to reach for them.

Reuses the engine's already-verified ADV_TOKENS registry and get_w3() RPC
failover pool — no separate Web3 client, no separate multi-chain config.
"""
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_ERC20_BALANCE_OF_SELECTOR = "70a08231"  # balanceOf(address)


@dataclass
class TokenBalance:
    token: str
    address: str
    on_chain_raw: int
    decimals: int

    @property
    def on_chain_human(self) -> float:
        return self.on_chain_raw / (10 ** self.decimals)


@dataclass
class ReconciliationResult:
    timestamp: str
    contract_address: str
    balances: Dict[str, TokenBalance] = field(default_factory=dict)
    db_recorded_usd: Dict[str, float] = field(default_factory=dict)
    # token -> stuck balance in human units (only entries above the dust threshold)
    discrepancies: Dict[str, float] = field(default_factory=dict)
    status: str = "OK"  # OK | WARNING | ERROR


def _eth_call_balance_of(eth_call, token_addr: str, holder: str) -> Optional[int]:
    """Raw eth_call balanceOf(holder) — hand-rolled calldata, matching this
    project's convention (no contract ABI object needed).
    `eth_call(tx_dict) -> bytes-like` is injected so this is unit-testable
    without a live chain."""
    try:
        holder_padded = holder.lower().replace('0x', '').rjust(64, '0')
        data = '0x' + _ERC20_BALANCE_OF_SELECTOR + holder_padded
        raw = eth_call({'to': token_addr, 'data': data})
        raw = bytes(raw)
        if len(raw) < 32:
            return None
        return int.from_bytes(raw[:32], 'big')
    except Exception as e:
        logger.warning(f"balanceOf({token_addr}) failed: {e}")
        return None


class RevenueReconciliationEngine:
    """Detects stuck funds by checking NexusFlashReceiver's live token balances.

    Dependency-injected (eth_call function + db path + token registry) so it
    tests fully offline; the live engine wires it to get_w3().eth.call and
    ADV_TOKENS.
    """

    def __init__(self, eth_call, db_path: str, tokens: Dict[str, tuple]):
        self.eth_call = eth_call
        self.db_path = db_path
        self.tokens = tokens

    def get_on_chain_balance(self, contract_address: str, token_symbol: str) -> Optional[TokenBalance]:
        if token_symbol not in self.tokens:
            return None
        addr, decimals = self.tokens[token_symbol]
        raw = _eth_call_balance_of(self.eth_call, addr, contract_address)
        if raw is None:
            return None
        return TokenBalance(token=token_symbol, address=addr, on_chain_raw=raw, decimals=decimals)

    def get_db_recorded_usd(self, token_symbol: str) -> float:
        """Sum of amount_usd ever logged to revenue_log for this token (context
        only — informational, not part of the stuck-funds verdict, since a
        pass-through executor's live balance isn't expected to track a
        cumulative ledger)."""
        if not os.path.exists(self.db_path):
            return 0.0
        con = None
        try:
            con = sqlite3.connect(self.db_path)
            cur = con.cursor()
            cur.execute(
                "SELECT COALESCE(SUM(amount_usd), 0) FROM revenue_log WHERE token = ?",
                (token_symbol,),
            )
            row = cur.fetchone()
            return float(row[0]) if row else 0.0
        except Exception as e:
            logger.error(f"revenue_log query failed: {e}")
            return 0.0
        finally:
            if con is not None:
                con.close()

    def reconcile(self, contract_address: str, warn_usd: float = 1.0) -> ReconciliationResult:
        """Check every registered token's on-chain balance held by `contract_address`.

        status:
          OK      — every token balance is at/below the dust threshold.
          WARNING — one or more tokens (not all) show a stuck balance above the
                    threshold.
          ERROR   — every token checked shows a stuck balance (more likely a
                    systemic issue, e.g. checking the wrong contract address).
        """
        balances: Dict[str, TokenBalance] = {}
        db_recorded: Dict[str, float] = {}
        discrepancies: Dict[str, float] = {}

        for symbol in self.tokens:
            bal = self.get_on_chain_balance(contract_address, symbol)
            if bal is None:
                continue
            balances[symbol] = bal
            db_recorded[symbol] = self.get_db_recorded_usd(symbol)
            if bal.on_chain_human > warn_usd:
                discrepancies[symbol] = bal.on_chain_human
                logger.warning(
                    f"{symbol}: {bal.on_chain_human:.6f} sitting in {contract_address} "
                    f"— use rescueTokens()/rescueETH() to recover"
                )

        status = "OK"
        if discrepancies:
            status = "ERROR" if (balances and len(discrepancies) == len(balances)) else "WARNING"

        return ReconciliationResult(
            timestamp=datetime.now(timezone.utc).isoformat(),
            contract_address=contract_address,
            balances=balances,
            db_recorded_usd=db_recorded,
            discrepancies=discrepancies,
            status=status,
        )
