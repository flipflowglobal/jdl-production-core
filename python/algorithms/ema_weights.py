from typing import Dict

class EMAWeights:
    ALPHA = 0.15
    def __init__(self):
        self._w: Dict[str, float] = {}
    
    def update(self, pair: str, found: bool) -> float:
        w = self._w.get(pair, 0.5)
        self._w[pair] = self.ALPHA * (1.0 if found else 0.0) + (1 - self.ALPHA) * w
        return self._w[pair]
    
    def get(self, pair: str) -> float:
        return self._w.get(pair, 0.5)
    
    def ranked(self, pairs: list) -> list:
        return sorted(pairs, key=lambda p: self.get(str(p)), reverse=True)
