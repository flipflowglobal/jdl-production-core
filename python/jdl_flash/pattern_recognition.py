"""Pattern recognition for flash-loan arbitrage: RSI, MACD, Bollinger, breakout, regime, candlestick, score."""
from __future__ import annotations
import math, statistics
from typing import List, Optional, Dict, Tuple, Any

__all__ = ["PatternRecognition"]
_EPS = 1e-10

def _ema(vals: List[float], p: int) -> List[float]:
    if not vals or p <= 0: return []
    k = 2.0 / (p + 1); r = [vals[0]]
    for v in vals[1:]: r.append(v * k + r[-1] * (1 - k))
    return r

def _znorm(s: List[float]) -> List[float]:
    try:
        mu = statistics.mean(s); sd = statistics.pstdev(s)
        return [0.0]*len(s) if sd < _EPS else [(x-mu)/sd for x in s]
    except Exception: return [0.0]*len(s)

class PatternRecognition:
    """Technical pattern recognition for price series. All methods return safe defaults on short/bad input."""

    def rsi(self, prices: List[float], period: int = 14) -> Optional[float]:
        """Relative Strength Index."""
        try:
            if len(prices) < period + 1: return None
            d = [prices[i]-prices[i-1] for i in range(1, len(prices))]
            ag = sum(x for x in d[:period] if x > 0) / period
            al = sum(-x for x in d[:period] if x < 0) / period
            for x in d[period:]:
                ag = (ag*(period-1) + (x if x>0 else 0)) / period
                al = (al*(period-1) + (-x if x<0 else 0)) / period
            return 100.0 if al < _EPS else 100.0 - 100.0/(1.0 + ag/al)
        except Exception: return None

    def macd(self, prices: List[float], fast: int=12, slow: int=26, signal: int=9
             ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """MACD line, signal line, histogram."""
        try:
            if len(prices) < slow + signal: return None, None, None
            ml = [f-s for f,s in zip(_ema(prices,fast), _ema(prices,slow))]
            sl = _ema(ml, signal)
            return ml[-1], sl[-1], ml[-1]-sl[-1]
        except Exception: return None, None, None

    def bollinger(self, prices: List[float], period: int=20, num_std: float=2.0
                  ) -> Dict[str, Optional[float]]:
        """Bollinger Bands: upper, middle, lower."""
        e: Dict[str, Optional[float]] = {"upper": None, "middle": None, "lower": None}
        try:
            if len(prices) < period: return e
            w = prices[-period:]; m = statistics.mean(w); sd = statistics.pstdev(w)
            return {"upper": m+num_std*sd, "middle": m, "lower": m-num_std*sd}
        except Exception: return e

    def support_resistance(self, prices: List[float], window: int=5) -> Dict[str, List[float]]:
        """Local minima/maxima clustering for support and resistance levels."""
        e: Dict[str, List[float]] = {"support": [], "resistance": []}
        try:
            if len(prices) < window*2+1: return e
            mins, maxs = [], []
            for i in range(window, len(prices)-window):
                seg = prices[i-window:i+window+1]
                if prices[i] == min(seg): mins.append(prices[i])
                if prices[i] == max(seg): maxs.append(prices[i])
            spread = (max(prices)-min(prices))*0.02 if len(prices)>1 else 1.0
            def cluster(v: List[float]) -> List[float]:
                if not v: return []
                v = sorted(v); grps, g = [], [v[0]]
                for x in v[1:]:
                    if x-g[-1] <= spread: g.append(x)
                    else: grps.append(statistics.mean(g)); g=[x]
                grps.append(statistics.mean(g)); return grps
            return {"support": cluster(mins), "resistance": cluster(maxs)}
        except Exception: return e

    def detect_breakout(self, prices: List[float]) -> Dict[str, Any]:
        """Detect breakout direction and z-score strength."""
        e: Dict[str, Any] = {"dir": None, "strength": 0.0}
        try:
            if len(prices) < 20: return e
            base = prices[-20:-5]; rec = prices[-5:]
            bm = statistics.mean(base); bs = statistics.pstdev(base) or _EPS
            z = (statistics.mean(rec)-bm)/bs
            d = "up" if z>1.5 else ("down" if z<-1.5 else None)
            return {"dir": d, "strength": round(min(abs(z)/3.0, 1.0), 4)}
        except Exception: return e

    def matrix_profile_lite(self, prices: List[float], m: int) -> List[float]:
        """Brute-force z-normalized matrix profile (n<=400 cap)."""
        try:
            n = len(prices)
            if n > 400 or m <= 0 or n < m*2: return []
            ns = n-m+1
            zs = [_znorm(prices[i:i+m]) for i in range(ns)]
            prof = []
            for i in range(ns):
                best = math.inf
                for j in range(ns):
                    if abs(i-j) < m: continue
                    d = math.sqrt(sum((a-b)**2 for a,b in zip(zs[i],zs[j])))
                    if d < best: best = d
                prof.append(round(best,6) if best != math.inf else 0.0)
            return prof
        except Exception: return []

    def regime_shift(self, prices: List[float]) -> bool:
        """Detect regime shift via rolling mean/vol change."""
        try:
            if len(prices) < 30: return False
            h = len(prices)//2; a,b = prices[:h], prices[h:]
            s1 = statistics.pstdev(a) or _EPS; s2 = statistics.pstdev(b) or _EPS
            ms = abs(statistics.mean(b)-statistics.mean(a))/((s1+s2)/2)
            vr = max(s1,s2)/(min(s1,s2) or _EPS)
            return ms > 1.0 or vr > 2.0
        except Exception: return False

    def candlestick(self, ohlc: List[Tuple[float,float,float,float]]) -> List[str]:
        """Detect doji, hammer, shooting_star, engulfing patterns."""
        try:
            pats = []
            for i,(o,h,l,c) in enumerate(ohlc):
                body=abs(c-o); rng=h-l or _EPS
                uw=h-max(o,c); lw=min(o,c)-l
                if body/rng < 0.1: pats.append("doji")
                elif lw>2*body and uw<body: pats.append("hammer")
                elif uw>2*body and lw<body: pats.append("shooting_star")
                if i>0:
                    po,_,_,pc=ohlc[i-1]; pb=abs(pc-po)
                    if pb>_EPS and body>pb and (
                       (pc<po and c>o and c>po and o<pc) or
                       (pc>po and c<o and c<po and o>pc)): pats.append("engulfing")
            return pats
        except Exception: return []

    def score(self, prices: List[float]) -> Dict[str, Any]:
        """Aggregate directional score in [-1,1] with confidence."""
        try:
            if len(prices) < 30: return {"score":0.0,"confidence":0.0,"signals":{}}
            sigs: Dict[str,float] = {}
            rv = self.rsi(prices)
            if rv is not None: sigs["rsi"] = (rv-50.0)/50.0
            _,_,hist = self.macd(prices)
            if hist is not None:
                scale = max(abs(p) for p in prices[-30:]) - min(abs(p) for p in prices[-30:]) or _EPS
                sigs["macd"] = max(-1.0, min(1.0, hist/(scale*0.01+_EPS)))
            bb = self.bollinger(prices)
            if all(v is not None for v in bb.values()):
                last=prices[-1]; br=(bb["upper"]-bb["lower"]) or _EPS  # type: ignore
                sigs["bb"] = max(-1.0, min(1.0, (last-bb["middle"])/(br/2)))  # type: ignore
            bk = self.detect_breakout(prices)
            if bk["dir"] == "up": sigs["breakout"] = bk["strength"]
            elif bk["dir"] == "down": sigs["breakout"] = -bk["strength"]
            if not sigs: return {"score":0.0,"confidence":0.0,"signals":{}}
            avg = sum(sigs.values())/len(sigs)
            return {"score":round(max(-1.0,min(1.0,avg)),4),
                    "confidence":round(min(1.0,len(sigs)/4.0),4),
                    "signals":{k:round(v,4) for k,v in sigs.items()}}
        except Exception: return {"score":0.0,"confidence":0.0,"signals":{}}
