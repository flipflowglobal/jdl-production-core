#!/usr/bin/env python3
"""
gas_kernel.py — Zero-Gas Strategy Kernel
All 7 novel zero-upfront-gas execution methods with UCB1 selection.
Runs as a subprocess launched by flash_supervisor.py.
"""
import os
import json
import time
import math
import hashlib
import logging
import asyncio
import aiohttp
from typing import Dict, Optional, List
from pathlib import Path
from dotenv import load_dotenv
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct

load_dotenv(os.path.expanduser('~/jdl/.env'))

WALLET     = os.getenv('WALLET_ADDRESS', '')
PRIV_KEY   = os.getenv('PRIVATE_KEY', '')
FB_SECRET  = os.getenv('FLASHBOTS_SECRET', '')
CONTRACT   = os.getenv('FLASH_CONTRACT_ADDRESS', '')
ALCH_ARB   = os.getenv('ALCHEMY_ARB_KEY', '')
BICONOMY_K = os.getenv('BICONOMY_API_KEY', '')

RPC_ARB = f'https://arb-mainnet.g.alchemy.com/v2/{ALCH_ARB}' if ALCH_ARB else 'https://arb1.arbitrum.io/rpc'

FLASHBOTS_RELAY  = 'https://relay.flashbots.net'
MEV_SHARE_STREAM = 'https://mev-share.flashbots.net'
GELATO_RELAY     = 'https://relay.gelato.digital/relays/v2/call-with-sync-fee'
BICONOMY_URL     = 'https://sdk-relayer.prod.biconomy.io/api/v1/send/gasless'

log = logging.getLogger('GasKernel')


# ══════════════════════════════════════════════════════════════════════════
# STRATEGY 1 — Flashbots PEG (block.coinbase payment)
# The tx has gasPrice=0 but block.coinbase.transfer(fee) inside pays the builder.
# ══════════════════════════════════════════════════════════════════════════

class FlashbotsPEG:
    name = 'FLASHBOTS_PEG'

    def __init__(self, w3: Web3):
        self.w3  = w3
        self.acc = Account.from_key(PRIV_KEY) if PRIV_KEY else None
        self.fb_acc = Account.from_key(FB_SECRET) if FB_SECRET else None

    def _fb_sign(self, body: str) -> str:
        if not self.fb_acc:
            return ''
        msg = encode_defunct(text='0x' + hashlib.sha256(body.encode()).hexdigest())
        sig = self.fb_acc.sign_message(msg).signature.hex()
        return f'{self.fb_acc.address}:{sig}'

    async def submit(self, calldata: str, profit_wei: int) -> Optional[str]:
        if not (self.acc and PRIV_KEY and CONTRACT):
            return None
        try:
            nonce = self.w3.eth.get_transaction_count(self.acc.address)
            raw_tx = self.acc.sign_transaction({
                'to':       Web3.to_checksum_address(CONTRACT),
                'data':     calldata,
                'gas':      600_000,
                'gasPrice': 0,
                'nonce':    nonce,
                'chainId':  42161,
                'value':    0,
            }).rawTransaction.hex()

            target_block = self.w3.eth.block_number + 1
            bundle_body  = json.dumps({
                'jsonrpc': '2.0', 'id': 1,
                'method': 'eth_sendBundle',
                'params': [{'txs': [raw_tx], 'blockNumber': hex(target_block)}]
            })
            async with aiohttp.ClientSession() as sess:
                async with sess.post(
                    FLASHBOTS_RELAY,
                    data=bundle_body,
                    headers={
                        'Content-Type':          'application/json',
                        'X-Flashbots-Signature': self._fb_sign(bundle_body),
                    },
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as r:
                    data = await r.json(content_type=None)
                    bh   = data.get('result', {}).get('bundleHash')
                    log.info(f'PEG bundle {bh}')
                    return bh
        except Exception as e:
            log.warning(f'PEG submit: {e}')
            return None


# ══════════════════════════════════════════════════════════════════════════
# STRATEGY 2 — MEV-Share Backrun
# Listen to Flashbots MEV-Share SSE stream, backrun profitable txs
# using flash loans to capture the leftover value.
# ══════════════════════════════════════════════════════════════════════════

class MEVShareBackrun:
    name = 'MEV_SHARE_BACKRUN'

    def __init__(self, w3: Web3, fb_peg: FlashbotsPEG):
        self.w3  = w3
        self.peg = fb_peg
        self.seen_hashes: set = set()

    async def listen_and_backrun(self, calldata_builder, max_events: int = 50) -> Optional[str]:
        """
        SSE listener for MEV-Share events.
        On each event with a swap hint, attempt to backrun with flash arb.
        """
        seen = 0
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(
                    MEV_SHARE_STREAM,
                    headers={'Accept': 'text/event-stream'},
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as r:
                    async for line in r.content:
                        text = line.decode('utf-8').strip()
                        if not text.startswith('data:'):
                            continue
                        try:
                            evt = json.loads(text[5:].strip())
                        except Exception:
                            continue

                        tx_hash = evt.get('hash', '')
                        if tx_hash in self.seen_hashes:
                            continue
                        self.seen_hashes.add(tx_hash)

                        # Look for swap hints
                        logs = evt.get('logs', [])
                        if not logs:
                            seen += 1
                            if seen >= max_events:
                                break
                            continue

                        # Attempt backrun with flash arb calldata
                        calldata = calldata_builder(evt)
                        if calldata:
                            result = await self.peg.submit(calldata, 0)
                            if result:
                                return result
                        seen += 1
                        if seen >= max_events:
                            break
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            log.warning(f'MEV-Share listen: {e}')
        return None


# ══════════════════════════════════════════════════════════════════════════
# STRATEGY 3 — Gelato Free Relay (bootstrap)
# ══════════════════════════════════════════════════════════════════════════

class GelatoFreeRelay:
    name = 'GELATO_FREE'

    async def submit(self, calldata: str, chain_id: int = 42161) -> Optional[str]:
        if not CONTRACT:
            return None
        payload = {
            'chainId':  str(chain_id),
            'target':   CONTRACT,
            'data':     calldata,
            'feeToken': '0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE',
        }
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.post(
                    GELATO_RELAY, json=payload,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as r:
                    data = await r.json(content_type=None)
                    task = data.get('taskId')
                    log.info(f'Gelato task {task}')
                    return task
        except Exception as e:
            log.warning(f'Gelato relay: {e}')
            return None


# ══════════════════════════════════════════════════════════════════════════
# STRATEGY 4 — Biconomy Meta-Transactions (ERC-20 fee)
# ══════════════════════════════════════════════════════════════════════════

class BiconomyMetaTx:
    name = 'BICONOMY_META_TX'

    async def submit(self, calldata: str, from_addr: str, sig: str) -> Optional[str]:
        if not (BICONOMY_K and CONTRACT):
            return None
        payload = {
            'to':        CONTRACT,
            'apiId':     BICONOMY_K,
            'params':    [from_addr, calldata, sig],
            'from':      from_addr,
        }
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.post(
                    BICONOMY_URL, json=payload,
                    headers={'x-api-key': BICONOMY_K},
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as r:
                    data = await r.json(content_type=None)
                    tx   = data.get('txHash')
                    log.info(f'Biconomy tx {tx}')
                    return tx
        except Exception as e:
            log.warning(f'Biconomy relay: {e}')
            return None


# ══════════════════════════════════════════════════════════════════════════
# STRATEGY 5 — EIP-4337 Profit-Paymaster
# Smart account UserOperation; paymaster sponsors gas only if profit >= cost.
# ══════════════════════════════════════════════════════════════════════════

ENTRY_POINT_ARB = '0x5FF137D4b0FDCD49DcA30c7CF57E578a026d2789'

class EIP4337Paymaster:
    name = 'EIP4337_PAYMASTER'

    BUNDLER_URLS = [
        'https://bundler.biconomy.io/api/v2/42161/nJPK7B3ru.dd7f7861-190d-45ic-af80-6877f74b8f44',
        'https://api.stackup.sh/v1/node/arb',
    ]
    PAYMASTER_ADDR = os.getenv('PAYMASTER_ADDRESS', '')

    def __init__(self, w3: Web3):
        self.w3 = w3

    async def submit(self, calldata: str, projected_profit_usdc6: int) -> Optional[str]:
        if not (self.PAYMASTER_ADDR and WALLET):
            return None

        # paymasterAndData: [paymaster(20)] + [flash_contract(20)] + [minProfit(32)] + [projectedProfit(32)]
        paymaster_data = (
            self.PAYMASTER_ADDR.lower().replace('0x', '') +
            CONTRACT.lower().replace('0x', '') +
            format(5_000_000, '064x') +          # $5 min profit
            format(projected_profit_usdc6, '064x')
        )

        user_op = {
            'sender':               WALLET,
            'nonce':                hex(self.w3.eth.get_transaction_count(WALLET)),
            'initCode':             '0x',
            'callData':             calldata,
            'callGasLimit':         hex(600_000),
            'verificationGasLimit': hex(150_000),
            'preVerificationGas':   hex(21_000),
            'maxFeePerGas':         hex(int(self.w3.eth.gas_price * 1.2)),
            'maxPriorityFeePerGas': hex(1_000_000),
            'paymasterAndData':     '0x' + paymaster_data,
            'signature':            '0x',
        }

        for url in self.BUNDLER_URLS:
            try:
                async with aiohttp.ClientSession() as sess:
                    async with sess.post(
                        url,
                        json={'jsonrpc':'2.0','id':1,'method':'eth_sendUserOperation','params':[user_op, ENTRY_POINT_ARB]},
                        timeout=aiohttp.ClientTimeout(total=15)
                    ) as r:
                        data = await r.json(content_type=None)
                        if 'result' in data:
                            log.info(f'4337 userOpHash {data["result"]}')
                            return data['result']
            except Exception as e:
                log.warning(f'4337 bundler {url}: {e}')
        return None


# ══════════════════════════════════════════════════════════════════════════
# STRATEGY 6 — Recursive Flash Stack
# Borrow WETH flash → unwrap ETH → fund gas → run arb → wrap profit → repay
# ══════════════════════════════════════════════════════════════════════════

RECURSIVE_ABI = '[{"inputs":[{"name":"wethPool","type":"address"},{"name":"wethForGas","type":"uint256"},{"name":"arbAsset","type":"address"},{"name":"arbAmount","type":"uint256"},{"name":"tokenInter","type":"address"},{"name":"buyFee","type":"uint24"},{"name":"sellFee","type":"uint24"},{"name":"minProfit","type":"uint256"},{"name":"builderFee","type":"uint256"}],"name":"recursiveFlashStack","outputs":[],"stateMutability":"nonpayable","type":"function"}]'

class RecursiveFlashStack:
    name = 'RECURSIVE_FLASH'
    WETH_USDC_POOL = '0xC6962004f452bE9203591991D15f6b388e09E8D0'
    WETH_ARB       = '0x82aF49447D8a07e3bd95BD0d56f35241523fBab1'

    def __init__(self, w3: Web3, peg: FlashbotsPEG):
        self.w3  = w3
        self.peg = peg
        if CONTRACT:
            self.contract = w3.eth.contract(
                address=Web3.to_checksum_address(CONTRACT),
                abi=json.loads(RECURSIVE_ABI)
            )
        else:
            self.contract = None

    async def submit(self, opp: Dict, eth_price: float) -> Optional[str]:
        if not self.contract:
            return None
        gas_reserve_eth = 0.002  # 0.002 ETH for gas
        weth_for_gas    = int(gas_reserve_eth * 1e18)
        arb_amount      = int(opp['loan_usdc'] / eth_price * 1e18)
        calldata = self.contract.encodeABI(
            fn_name='recursiveFlashStack',
            args=[
                Web3.to_checksum_address(self.WETH_USDC_POOL),
                weth_for_gas,
                Web3.to_checksum_address(opp['asset']),
                arb_amount,
                Web3.to_checksum_address(opp['token_inter']),
                opp['buy_fee'],
                opp['sell_fee'],
                int(opp['profit_usd'] / eth_price * 1e18 * 0.5),
                int(opp['profit_usd'] / eth_price * 1e18 * 0.05),
            ]
        )
        return await self.peg.submit(calldata, 0)


# ══════════════════════════════════════════════════════════════════════════
# STRATEGY 7 — TWAP Lag Arbitrage
# Exploit the gap between 30-min TWAP oracle price and spot price.
# Buy on TWAP-priced pool (stale), sell on spot pool. Profit = oracle lag.
# ══════════════════════════════════════════════════════════════════════════

TWAP_ABI = '[{"inputs":[{"name":"pool","type":"address"},{"name":"assetUnderpriced","type":"address"},{"name":"assetOverpriced","type":"address"},{"name":"flashAmount","type":"uint256"},{"name":"buyFee","type":"uint24"},{"name":"sellFee","type":"uint24"},{"name":"minProfit","type":"uint256"},{"name":"builderFee","type":"uint256"}],"name":"twapArbitrage","outputs":[],"stateMutability":"nonpayable","type":"function"}]'

class TWAPLagArb:
    name = 'TWAP_LAG_ARB'
    WETH_USDC_POOL = '0xC6962004f452bE9203591991D15f6b388e09E8D0'
    USDC           = '0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8'
    WETH           = '0x82aF49447D8a07e3bd95BD0d56f35241523fBab1'

    def __init__(self, w3: Web3, peg: FlashbotsPEG):
        self.w3  = w3
        self.peg = peg
        if CONTRACT:
            self.contract = w3.eth.contract(
                address=Web3.to_checksum_address(CONTRACT),
                abi=json.loads(TWAP_ABI)
            )
        else:
            self.contract = None

    async def submit(self, opp: Dict, eth_price: float) -> Optional[str]:
        if not self.contract:
            return None
        flash_amount = int(opp['loan_usdc'] * 1e6)  # USDC 6 decimals
        calldata = self.contract.encodeABI(
            fn_name='twapArbitrage',
            args=[
                Web3.to_checksum_address(self.WETH_USDC_POOL),
                Web3.to_checksum_address(self.USDC),
                Web3.to_checksum_address(self.WETH),
                flash_amount,
                opp.get('buy_fee', 500),
                opp.get('sell_fee', 3000),
                int(opp['profit_usd'] * 1e6 * 0.5),
                int(opp['profit_usd'] / eth_price * 1e18 * 0.05),
            ]
        )
        return await self.peg.submit(calldata, 0)


# ══════════════════════════════════════════════════════════════════════════
# KERNEL — UCB1 dispatcher
# ══════════════════════════════════════════════════════════════════════════

class GasKernel:
    """
    Dispatches flash loan transactions using the best available zero-gas strategy.
    UCB1 bandit learns which strategy succeeds most on this network/block.
    """
    def __init__(self):
        w3       = Web3(Web3.HTTPProvider(RPC_ARB))
        peg      = FlashbotsPEG(w3)
        self.strategies = [
            peg,
            MEVShareBackrun(w3, peg),
            GelatoFreeRelay(),
            BiconomyMetaTx(),
            EIP4337Paymaster(w3),
            RecursiveFlashStack(w3, peg),
            TWAPLagArb(w3, peg),
        ]
        self.counts  = [0]   * len(self.strategies)
        self.rewards = [0.0] * len(self.strategies)
        self.N       = 0

    def _ucb1_arm(self) -> int:
        for i, c in enumerate(self.counts):
            if c == 0:
                return i
        return max(
            range(len(self.strategies)),
            key=lambda i: (
                self.rewards[i]/self.counts[i] +
                math.sqrt(2 * math.log(self.N) / self.counts[i])
            )
        )

    async def execute(self, opp: Dict, eth_price: float, calldata: str = '') -> Optional[str]:
        arm      = self._ucb1_arm()
        strategy = self.strategies[arm]
        log.info(f'GasKernel: trying {strategy.name}')

        result = None
        if isinstance(strategy, FlashbotsPEG):
            result = await strategy.submit(calldata or '0x', 0)
        elif isinstance(strategy, GelatoFreeRelay):
            result = await strategy.submit(calldata or '0x')
        elif isinstance(strategy, RecursiveFlashStack):
            result = await strategy.submit(opp, eth_price)
        elif isinstance(strategy, TWAPLagArb):
            result = await strategy.submit(opp, eth_price)
        elif isinstance(strategy, EIP4337Paymaster):
            result = await strategy.submit(calldata or '0x', int(opp.get('profit_usd', 0) * 1e6))
        elif isinstance(strategy, MEVShareBackrun):
            result = await strategy.listen_and_backrun(lambda _: calldata)
        else:
            result = None

        reward = opp.get('profit_usd', 0) if result else -0.5
        self.counts[arm]  += 1
        self.rewards[arm] += reward
        self.N            += 1
        return result

    def status(self) -> str:
        lines = []
        for i, s in enumerate(self.strategies):
            avg = self.rewards[i] / max(self.counts[i], 1)
            lines.append(f'  {s.name:25s}  tries={self.counts[i]:4d}  avg_reward=${avg:+.3f}')
        return '\n'.join(lines)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    kernel = GasKernel()
    print('Gas Kernel initialized.', kernel.status())
