"""Online/incremental forecasting for flash-loan arbitrage. Stdlib only."""
from collections import deque
import math

__all__ = ["OnlineAR","RidgeForecaster","EdgeClassifier","EWMAForecast","ConfidenceScorer"]

def _dot(a,b): return sum(x*y for x,y in zip(a,b))
def _mat_vec(M,v): return [_dot(r,v) for r in M]
def _outer_add(M,v,s=1.0): n=len(v); return [[M[i][j]+s*v[i]*v[j] for j in range(n)] for i in range(n)]
def _vadd(a,b,s=1.0): return [x+s*y for x,y in zip(a,b)]
def _eye(n): return [[1.0 if i==j else 0.0 for j in range(n)] for i in range(n)]
def _sig(x):
    try:
        return (1.0/(1.0+math.exp(-x))) if x>=0 else (lambda e: e/(1+e))(math.exp(x))
    except OverflowError: return 0.0 if x<0 else 1.0

class OnlineAR:
    """AR(p) via recursive least squares (forgetting factor lam)."""
    def __init__(self, p=5, lam=0.99):
        self.p=max(1,int(p)); self.lam=float(lam)
        self._buf=deque(maxlen=self.p+1); self._w=[0.0]*self.p; self._P=_eye(self.p)
    def update(self, x):
        try:
            self._buf.append(float(x))
            if len(self._buf)<self.p+1: return
            phi=list(self._buf)[:self.p][::-1]; tgt=list(self._buf)[self.p]
            Pp=_mat_vec(self._P,phi); d=self.lam+_dot(phi,Pp)
            if abs(d)<1e-12: return
            k=[v/d for v in Pp]; err=tgt-_dot(self._w,phi)
            self._w=_vadd(self._w,k,err)
            self._P=[[(self._P[i][j]-k[i]*Pp[j])/self.lam for j in range(self.p)] for i in range(self.p)]
        except Exception: pass
    def predict(self):
        try:
            if len(self._buf)<self.p: return None
            return _dot(self._w, list(self._buf)[-self.p:][::-1])
        except Exception: return None

class RidgeForecaster:
    """Incremental ridge regression on p lagged features."""
    def __init__(self, p=5, alpha=1.0):
        self.p=max(1,int(p)); self.alpha=float(alpha)
        self._buf=deque(maxlen=self.p+1)
        self._XtX=[[alpha*(1.0 if i==j else 0.0) for j in range(self.p)] for i in range(self.p)]
        self._Xty=[0.0]*self.p; self._w=[0.0]*self.p; self._ok=False
    def _solve(self):
        n=self.p; A=[r[:]+[self._Xty[i]] for i,r in enumerate(self._XtX)]
        for c in range(n):
            pv=A[c][c]
            if abs(pv)<1e-12: return
            for r in range(n):
                if r==c: continue
                f=A[r][c]/pv; A[r]=[A[r][j]-f*A[c][j] for j in range(n+1)]
        self._w=[A[i][n]/A[i][i] if abs(A[i][i])>1e-12 else 0.0 for i in range(n)]
    def update(self, x):
        try:
            self._buf.append(float(x))
            if len(self._buf)<self.p+1: return
            phi=list(self._buf)[:self.p][::-1]; tgt=float(list(self._buf)[self.p])
            self._XtX=_outer_add(self._XtX,phi); self._Xty=_vadd(self._Xty,phi,tgt)
            self._solve(); self._ok=True
        except Exception: pass
    def forecast(self, h=1):
        try:
            if not self._ok or len(self._buf)<self.p: return None
            buf=list(self._buf)[-self.p:]; val=None
            for _ in range(max(1,int(h))):
                phi=buf[-self.p:][::-1]; val=_dot(self._w,phi); buf.append(val)
            return val
        except Exception: return None

class EdgeClassifier:
    """Online logistic regression (SGD) -> P(profitable) in [0,1]."""
    def __init__(self, n_features, lr=0.01, l2=1e-4):
        self.n=max(1,int(n_features)); self.lr=float(lr); self.l2=float(l2)
        self._w=[0.0]*self.n; self._b=0.0; self._ok=False
    def _prep(self, features):
        f=[float(v) for v in features[:self.n]]; f+=[0.0]*(self.n-len(f)); return f
    def update(self, features, label):
        try:
            f=self._prep(features); y=float(label)
            p=_sig(_dot(self._w,f)+self._b); e=p-y
            self._w=[w-self.lr*(e*fv+self.l2*w) for w,fv in zip(self._w,f)]
            self._b-=self.lr*e; self._ok=True
        except Exception: pass
    def predict_proba(self, features):
        try:
            if not self._ok: return 0.5
            return _sig(_dot(self._w,self._prep(features))+self._b)
        except Exception: return 0.5

class EWMAForecast:
    """Holt double-exponential smoothing (level+trend) one-step forecast."""
    def __init__(self, alpha=0.2, beta=0.1):
        self.alpha=float(alpha); self.beta=float(beta); self._l=None; self._t=None
    def update(self, x):
        try:
            x=float(x)
            if self._l is None: self._l=x; self._t=0.0; return
            pl=self._l; self._l=self.alpha*x+(1-self.alpha)*(self._l+self._t)
            self._t=self.beta*(self._l-pl)+(1-self.beta)*self._t
        except Exception: pass
    def forecast(self):
        try: return None if self._l is None else self._l+self._t
        except Exception: return None

class ConfidenceScorer:
    """Blend model agreement + rolling hit-rate into a [0,1] confidence."""
    def __init__(self, window=50, agreement_weight=0.5):
        self.aw=max(0.0,min(1.0,float(agreement_weight)))
        self._hits=deque(maxlen=max(1,int(window)))
    def record_outcome(self, predicted_profitable, was_profitable):
        try: self._hits.append(1.0 if predicted_profitable==was_profitable else 0.0)
        except Exception: pass
    def score(self, probas):
        """probas: list of floats in [0,1] from constituent models."""
        try:
            valid=[float(p) for p in probas if p is not None and 0.0<=float(p)<=1.0]
            if not valid: return 0.5
            mu=sum(valid)/len(valid)
            agreement=1.0-min(1.0,math.sqrt(sum((p-mu)**2 for p in valid)/len(valid))*4)
            hr=sum(self._hits)/len(self._hits) if self._hits else 0.5
            return max(0.0,min(1.0,self.aw*agreement+(1-self.aw)*hr))
        except Exception: return 0.5
