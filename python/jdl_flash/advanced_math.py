"""Numerical toolkit for flash-loan arbitrage — stdlib only, fault-tolerant."""
import math

__all__ = ["AdvancedMath","ewma","zscore","softmax","sigmoid","logit","sma","ema","stddev","covariance","correlation"]

def _ncdf(x):
    """Standard normal CDF via erf."""
    return 0.5*(1.0+math.erf(x/math.sqrt(2.0)))

class AdvancedMath:
    """Static numerical methods: Cholesky, Black-Scholes, root finders, DFT, ridge regression."""

    @staticmethod
    def cholesky(M):
        """Cholesky decomposition of SPD matrix -> lower-triangular list[list]."""
        try:
            n=len(M); L=[[0.0]*n for _ in range(n)]
            for i in range(n):
                for j in range(i+1):
                    s=sum(L[i][k]*L[j][k] for k in range(j))
                    if i==j:
                        v=M[i][i]-s
                        if v<=0: return None
                        L[i][j]=math.sqrt(v)
                    else:
                        if not L[j][j]: return None
                        L[i][j]=(M[i][j]-s)/L[j][j]
            return L
        except Exception: return None

    @staticmethod
    def solve_lower(L,b):
        """Forward substitution: solve L x = b."""
        try:
            n=len(L); x=[0.0]*n
            for i in range(n):
                if not L[i][i]: return None
                x[i]=(b[i]-sum(L[i][j]*x[j] for j in range(i)))/L[i][i]
            return x
        except Exception: return None

    @staticmethod
    def solve_upper(U,b):
        """Back substitution: solve U x = b."""
        try:
            n=len(U); x=[0.0]*n
            for i in range(n-1,-1,-1):
                if not U[i][i]: return None
                x[i]=(b[i]-sum(U[i][j]*x[j] for j in range(i+1,n)))/U[i][i]
            return x
        except Exception: return None

    @staticmethod
    def spd_solve(A,b):
        """Solve A x = b for SPD A via Cholesky factorisation."""
        try:
            L=AdvancedMath.cholesky(A)
            if L is None: return None
            y=AdvancedMath.solve_lower(L,b)
            if y is None: return None
            n=len(L); Lt=[[L[j][i] for j in range(n)] for i in range(n)]
            return AdvancedMath.solve_upper(Lt,y)
        except Exception: return None

    @staticmethod
    def black_scholes_call(S,K,t,r,sigma):
        """Black-Scholes European call price."""
        try:
            if any(v<=0 for v in (S,K,t,sigma)): return None
            sq=math.sqrt(t); d1=(math.log(S/K)+(r+0.5*sigma**2)*t)/(sigma*sq)
            return S*_ncdf(d1)-K*math.exp(-r*t)*_ncdf(d1-sigma*sq)
        except Exception: return None

    @staticmethod
    def black_scholes_put(S,K,t,r,sigma):
        """Black-Scholes European put price."""
        try:
            if any(v<=0 for v in (S,K,t,sigma)): return None
            sq=math.sqrt(t); d1=(math.log(S/K)+(r+0.5*sigma**2)*t)/(sigma*sq); d2=d1-sigma*sq
            return K*math.exp(-r*t)*_ncdf(-d2)-S*_ncdf(-d1)
        except Exception: return None

    @staticmethod
    def newton(f,df,x0,iters=20,tol=1e-10):
        """Newton-Raphson root finder."""
        try:
            x=float(x0)
            for _ in range(iters):
                fx=f(x)
                if abs(fx)<tol: return x
                dfx=df(x)
                if not dfx: return None
                x-=fx/dfx
            return x
        except Exception: return None

    @staticmethod
    def secant(f,x0,x1,iters=50,tol=1e-10):
        """Secant method root finder."""
        try:
            a,b=float(x0),float(x1)
            for _ in range(iters):
                fa,fb=f(a),f(b)
                if abs(fb)<tol: return b
                d=fb-fa
                if not d: return None
                a,b=b,b-fb*(b-a)/d
            return b
        except Exception: return None

    @staticmethod
    def rfft_mag(samples):
        """Real DFT magnitudes for non-negative frequencies (O(n^2), n<=512)."""
        try:
            n=len(samples)
            if not n: return []
            out=[]
            for k in range(n//2+1):
                re=sum(samples[j]*math.cos(2*math.pi*k*j/n) for j in range(n))
                im=sum(samples[j]*math.sin(2*math.pi*k*j/n) for j in range(n))
                out.append(math.hypot(re,im))
            return out
        except Exception: return []

    @staticmethod
    def ridge_fit(X,y,lam):
        """Ridge regression coefficients via normal equations + spd_solve."""
        try:
            n=len(X)
            if not n: return None
            p=len(X[0])
            XtX=[[sum(X[i][a]*X[i][b] for i in range(n)) for b in range(p)] for a in range(p)]
            for j in range(p): XtX[j][j]+=lam
            Xty=[sum(X[i][j]*y[i] for i in range(n)) for j in range(p)]
            return AdvancedMath.spd_solve(XtX,Xty)
        except Exception: return None

    @staticmethod
    def ridge_predict(coef,row):
        """Predict from ridge coefficients: dot(coef, row)."""
        try: return sum(c*x for c,x in zip(coef,row))
        except Exception: return None

def ewma(series,alpha):
    """Exponentially weighted moving average."""
    try:
        if not series: return []
        r=[series[0]]
        for v in series[1:]: r.append(alpha*v+(1-alpha)*r[-1])
        return r
    except Exception: return []
def zscore(series):
    """Z-score normalise series (sample std)."""
    try:
        n=len(series)
        if n<2: return []
        mu=sum(series)/n; sd=math.sqrt(sum((x-mu)**2 for x in series)/(n-1))
        return [0.0]*n if not sd else [(x-mu)/sd for x in series]
    except Exception: return []
def softmax(v):
    """Softmax of a numeric vector."""
    try:
        m=max(v); e=[math.exp(x-m) for x in v]; s=sum(e); return [x/s for x in e]
    except Exception: return []
def sigmoid(x):
    """Logistic sigmoid."""
    try: return 1.0/(1.0+math.exp(-x))
    except Exception: return None
def logit(p):
    """Inverse sigmoid (log-odds); None outside (0,1)."""
    try: return math.log(p/(1.0-p)) if 0.0<p<1.0 else None
    except Exception: return None
def sma(series,window):
    """Simple moving average."""
    try: return [sum(series[i:i+window])/window for i in range(len(series)-window+1)]
    except Exception: return []
def ema(series,span):
    """EMA with alpha=2/(span+1)."""
    try: return ewma(series,2.0/(span+1))
    except Exception: return []
def stddev(series):
    """Sample standard deviation."""
    try:
        n=len(series)
        if n<2: return None
        mu=sum(series)/n; return math.sqrt(sum((x-mu)**2 for x in series)/(n-1))
    except Exception: return None
def covariance(x,y):
    """Sample covariance of two equal-length series."""
    try:
        n=len(x)
        if n!=len(y) or n<2: return None
        mx,my=sum(x)/n,sum(y)/n; return sum((a-mx)*(b-my) for a,b in zip(x,y))/(n-1)
    except Exception: return None
def correlation(x,y):
    """Pearson correlation coefficient."""
    try:
        c=covariance(x,y); sx,sy=stddev(x),stddev(y)
        return None if c is None or not sx or not sy else c/(sx*sy)
    except Exception: return None
