from typing import Dict
from collections import deque

class ZScoreDetector:
    WINDOW = 20
    THRESHOLD = 2.0
    
    def __init__(self):
        self._h: Dict[str, deque] = {}
    
    def update(self, key: str, v: float) -> float:
        if key not in self._h:
            self._h[key] = deque(maxlen=self.WINDOW)
        self._h[key].append(v)
        h = list(self._h[key])
        if len(h) < 3:
            return 0.0
        mu = sum(h) / len(h)
        sd = (sum((x - mu)**2 for x in h) / len(h))**0.5
        return (v - mu) / sd if sd > 0 else 0.0
    
    def is_anomaly(self, key: str, v: float) -> bool:
        return abs(self.update(key, v)) > self.THRESHOLD
