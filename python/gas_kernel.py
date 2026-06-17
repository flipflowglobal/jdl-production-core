#!/usr/bin/env python3
"""
gas_kernel.py — Zero-Gas Strategy Kernel
All 7 novel zero-upfront-gas execution strategies + UCB1 dispatcher.
Called by flash_loan_engine.py menu option [1] and [3].
"""
import os, json, time, math, hashlib, logging, asyncio
from typing import Dict, Optional
from pathlib import Path
from dotenv import load_dotenv

try: import aiohttp; AIOHTTP_OK=True
except ImportError: AIOHTTP_OK=False

try:
    from web3 import Web3
    from eth_account import Account
    from eth_account.messages import encode_defunct
    WEB3_OK=True
except ImportError: WEB3_OK=False

load_dotenv(os.path.expanduser('~/jdl/.env'))
WALLET    = os.getenv('WALLET_ADDRESS','')
PRIV_KEY  = os.getenv('PRIVATE_KEY','')
FB_SECRET = os.getenv('FLASHBOTS_SECRET','')
CONTRACT  = os.getenv('FLASH_CONTRACT_ADDRESS','')
ALCH_ARB  = os.getenv('ALCHEMY_ARB_KEY','')
BICON_K   = os.getenv('BICONOMY_API_KEY','')
PAYMSTR   = os.getenv('PAYMASTER_ADDRESS','')

RPC_ARB = f'https://arb-mainnet.g.alchemy.com/v2/{ALCH_ARB}' if ALCH_ARB else 'https://arb1.arbitrum.io/rpc'
FLASHBOTS = 'https://relay.flashbots.net'
GELATO    = 'https://relay.gelato.digital/relays/v2/call-with-sync-fee'
BICON_URL = 'https://sdk-relayer.prod.biconomy.io/api/v1/send/gasless'
MEV_SHARE = 'https://mev-share.flashbots.net'
ENTRY_PT  = '0x5FF137D4b0FDCD49DcA30c7CF57E578a026d2789'

log = logging.getLogger('GasKernel')

# ── 1. Flashbots PEG ─────────────────────────────────────────────────────

class FlashbotsPEG:
    name = 'FLASHBOTS_PEG'
    async def submit(self, calldata: str, profit_wei: int) -> Optional[str]:
        if not (WEB3_OK and AIOHTTP_OK and PRIV_KEY and CONTRACT): return None
        try:
            w3  = Web3(Web3.HTTPProvider(RPC_ARB,request_kwargs={'timeout':10}))
            acc = Account.from_key(PRIV_KEY)
            fb  = Account.from_key(FB_SECRET) if FB_SECRET else acc
            nonce = w3.eth.get_transaction_count(acc.address)
            raw = acc.sign_transaction({
                'to':Web3.to_checksum_address(CONTRACT),'data':calldata,
                'gas':600_000,'gasPrice':0,'nonce':nonce,'chainId':42161,'value':0
            }).rawTransaction.hex()
            body = json.dumps({'jsonrpc':'2.0','id':1,'method':'eth_sendBundle',
                'params':[{'txs':[raw],'blockNumber':hex(w3.eth.block_number+1)}]})
            msg = encode_defunct(text='0x'+hashlib.sha256(body.encode()).hexdigest())
            sig = fb.sign_message(msg).signature.hex()
            async with aiohttp.ClientSession() as s:
                async with s.post(FLASHBOTS,data=body,
                    headers={'X-Flashbots-Signature':f'{fb.address}:{sig}','Content-Type':'application/json'},
                    timeout=aiohttp.ClientTimeout(total=12)) as r:
                    d = await r.json(content_type=None)
                    return d.get('result',{}).get('bundleHash')
        except Exception as e: log.warning(f'PEG: {e}'); return None

# ── 2. MEV-Share Backrun ─────────────────────────────────────────────────

class MEVShareBackrun:
    name = 'MEV_SHARE_BACKRUN'
    def __init__(self, peg: FlashbotsPEG): self.peg=peg; self._seen=set()
    async def submit(self, calldata: str, profit_wei: int) -> Optional[str]:
        if not AIOHTTP_OK: return None
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(MEV_SHARE,headers={'Accept':'text/event-stream'},
                    timeout=aiohttp.ClientTimeout(total=30)) as r:
                    seen=0
                    async for line in r.content:
                        text=line.decode('utf-8').strip()
                        if not text.startswith('data:'): continue
                        try: evt=json.loads(text[5:].strip())
                        except: continue
                        h=evt.get('hash','')
                        if h in self._seen: continue
                        self._seen.add(h)
                        if evt.get('logs'):   # swap hint
                            result = await self.peg.submit(calldata,profit_wei)
                            if result: return result
                        seen+=1
                        if seen>=20: break
        except Exception as e: log.warning(f'MEVShare: {e}')
        return None

# ── 3. Gelato Free Relay ─────────────────────────────────────────────────

class GelatoFreeRelay:
    name = 'GELATO_FREE'
    async def submit(self, calldata: str, profit_wei: int) -> Optional[str]:
        if not (AIOHTTP_OK and CONTRACT): return None
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(GELATO,json={
                    'chainId':str(42161),'target':CONTRACT,'data':calldata,
                    'feeToken':'0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE'
                },timeout=aiohttp.ClientTimeout(total=12)) as r:
                    return (await r.json(content_type=None)).get('taskId')
        except Exception as e: log.warning(f'Gelato: {e}'); return None

# ── 4. Biconomy Meta-Tx ──────────────────────────────────────────────────

class BiconomyMetaTx:
    name = 'BICONOMY_META_TX'
    async def submit(self, calldata: str, profit_wei: int) -> Optional[str]:
        if not (AIOHTTP_OK and BICON_K and CONTRACT and WALLET): return None
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(BICON_URL,
                    json={'to':CONTRACT,'apiId':BICON_K,'params':[WALLET,calldata,'0x'],'from':WALLET},
                    headers={'x-api-key':BICON_K},timeout=aiohttp.ClientTimeout(total=12)) as r:
                    return (await r.json(content_type=None)).get('txHash')
        except Exception as e: log.warning(f'Biconomy: {e}'); return None

# ── 5. EIP-4337 Paymaster ────────────────────────────────────────────────

class EIP4337Paymaster:
    name = 'EIP4337_PAYMASTER'
    BUNDLERS = [
        'https://bundler.biconomy.io/api/v2/42161/nJPK7B3ru.dd7f7861-190d-45ic-af80-6877f74b8f44',
        'https://api.stackup.sh/v1/node/arb',
    ]
    def __init__(self): self._w3=Web3(Web3.HTTPProvider(RPC_ARB)) if WEB3_OK else None
    async def submit(self, calldata: str, profit_wei: int) -> Optional[str]:
        if not (AIOHTTP_OK and PAYMASTER and WALLET and CONTRACT and self._w3): return None
        pp_data = (PAYMASTER.lower().replace('0x','') + CONTRACT.lower().replace('0x','') +
                   format(5_000_000,'064x') + format(profit_wei//2000,'064x'))
        uop = {'sender':WALLET,'nonce':hex(self._w3.eth.get_transaction_count(WALLET)),
               'initCode':'0x','callData':calldata,
               'callGasLimit':hex(600_000),'verificationGasLimit':hex(150_000),
               'preVerificationGas':hex(21_000),
               'maxFeePerGas':hex(int(self._w3.eth.gas_price*1.2)),
               'maxPriorityFeePerGas':hex(1_000_000),
               'paymasterAndData':'0x'+pp_data,'signature':'0x'}
        for url in self.BUNDLERS:
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.post(url,
                        json={'jsonrpc':'2.0','id':1,'method':'eth_sendUserOperation',
                              'params':[uop,ENTRY_PT]},
                        timeout=aiohttp.ClientTimeout(total=12)) as r:
                        d=await r.json(content_type=None)
                        if 'result' in d: return d['result']
            except Exception as e: log.warning(f'4337 {url}: {e}')
        return None

# ── 6. Recursive Flash Stack ─────────────────────────────────────────────

class RecursiveFlashStack:
    name = 'RECURSIVE_FLASH'
    WETH_USDC_POOL = '0xC6962004f452bE9203591991D15f6b388e09E8D0'
    WETH           = '0x82aF49447D8a07e3bd95BD0d56f35241523fBab1'
    ABI = '[{"inputs":[{"name":"wethPool","type":"address"},{"name":"wethForGas","type":"uint256"},{"name":"arbAsset","type":"address"},{"name":"arbAmount","type":"uint256"},{"name":"tokenInter","type":"address"},{"name":"buyFee","type":"uint24"},{"name":"sellFee","type":"uint24"},{"name":"minProfit","type":"uint256"},{"name":"builderFee","type":"uint256"}],"name":"recursiveFlashStack","outputs":[],"stateMutability":"nonpayable","type":"function"}]'
    def __init__(self, peg): self.peg=peg
    async def submit(self, calldata: str, profit_wei: int) -> Optional[str]:
        if not (WEB3_OK and CONTRACT): return None
        w3 = Web3(Web3.HTTPProvider(RPC_ARB))
        c  = w3.eth.contract(address=Web3.to_checksum_address(CONTRACT),abi=json.loads(self.ABI))
        cd = c.encodeABI('recursiveFlashStack',args=[
            Web3.to_checksum_address(self.WETH_USDC_POOL), int(0.002*1e18),
            Web3.to_checksum_address('0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8'),
            int(100_000*1e6),
            Web3.to_checksum_address(self.WETH),
            500, 3000, profit_wei//2, profit_wei//20
        ])
        return await self.peg.submit(cd, profit_wei)

# ── 7. TWAP Lag Arb ──────────────────────────────────────────────────────

class TWAPLagArb:
    name = 'TWAP_LAG_ARB'
    POOL = '0xC6962004f452bE9203591991D15f6b388e09E8D0'
    USDC = '0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8'
    WETH = '0x82aF49447D8a07e3bd95BD0d56f35241523fBab1'
    ABI  = '[{"inputs":[{"name":"pool","type":"address"},{"name":"assetUnderpriced","type":"address"},{"name":"assetOverpriced","type":"address"},{"name":"flashAmount","type":"uint256"},{"name":"buyFee","type":"uint24"},{"name":"sellFee","type":"uint24"},{"name":"minProfit","type":"uint256"},{"name":"builderFee","type":"uint256"}],"name":"twapArbitrage","outputs":[],"stateMutability":"nonpayable","type":"function"}]'
    def __init__(self, peg): self.peg=peg
    async def submit(self, calldata: str, profit_wei: int) -> Optional[str]:
        if not (WEB3_OK and CONTRACT): return None
        w3 = Web3(Web3.HTTPProvider(RPC_ARB))
        c  = w3.eth.contract(address=Web3.to_checksum_address(CONTRACT),abi=json.loads(self.ABI))
        cd = c.encodeABI('twapArbitrage',args=[
            Web3.to_checksum_address(self.POOL),
            Web3.to_checksum_address(self.USDC),
            Web3.to_checksum_address(self.WETH),
            int(100_000*1e6), 500, 3000,
            profit_wei//2, profit_wei//20
        ])
        return await self.peg.submit(cd, profit_wei)

# ── Kernel ────────────────────────────────────────────────────────────────

class GasKernel:
    """UCB1 dispatcher for all 7 zero-gas strategies."""
    def __init__(self):
        peg = FlashbotsPEG()
        self.strategies = [
            peg, MEVShareBackrun(peg), GelatoFreeRelay(),
            BiconomyMetaTx(), EIP4337Paymaster(),
            RecursiveFlashStack(peg), TWAPLagArb(peg)
        ]
        self.counts  = [0]*7; self.rewards=[0.0]*7; self.N=0

    def _ucb1(self) -> int:
        for i,c in enumerate(self.counts):
            if c==0: return i
        return max(range(7), key=lambda i:
            self.rewards[i]/self.counts[i]+math.sqrt(2*math.log(self.N)/self.counts[i]))

    async def execute(self, calldata: str, profit_wei: int) -> Optional[str]:
        arm = self._ucb1()
        result = await self.strategies[arm].submit(calldata, profit_wei)
        self.counts[arm]+=1; self.rewards[arm]+= profit_wei/1e18 if result else -0.001; self.N+=1
        return result

    def status(self) -> str:
        lines = []
        for i,s in enumerate(self.strategies):
            avg = self.rewards[i]/max(self.counts[i],1)
            lines.append(f'  {s.name:<25} tries={self.counts[i]:4d}  avg={avg:+.5f} ETH')
        return '\n'.join(lines)

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    k=GasKernel(); print('GasKernel OK'); print(k.status())
