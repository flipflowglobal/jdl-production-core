"""
NEXUS-ARB Thompson Sampling Engine
Gaussian-Gaussian conjugate Thompson Sampling for DEX route arm selection.
UCB-1 fallback for cold-start arms with < MIN_PULLS observations.

Reference: Agrawal & Goyal (2012) "Analysis of Thompson Sampling for the
           Multi-armed Bandit Problem" — JMLR.

Each arm = a DEX route identifier (e.g. "WETH→USDC→DAI→WETH via pool_A,pool_B").
Reward = net_profit_usd (Gaussian-distributed, unknown μ and known σ²).

Conjugate update (Gaussian-Gaussian):
  Prior: μ ~ N(μ₀, σ₀²)
  Likelihood: r | μ ~ N(μ, σ²)
  Posterior: μ | r₁..rₙ ~ N(μₙ, σₙ²)
    where:
      σₙ² = 1 / (1/σ₀² + n/σ²)
      μₙ  = σₙ² × (μ₀/σ₀² + Σrᵢ/σ²)
"""
import math
import logging
import numpy as np
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

_PRIOR_MEAN   =  0.0    # prior belief: routes are break-even on average
_PRIOR_VAR    = 25.0    # high uncertainty initially (σ₀² = 25 USD²)
_NOISE_VAR    =  4.0    # observation noise variance (σ² = 4 USD²)
_MIN_PULLS    =  3      # pulls required before Thompson; UCB-1 used below this
_UCB_C        =  2.0    # exploration constant for UCB-1 fallback
_DECAY        =  0.99   # reward decay for non-stationarity adaptation


@dataclass
class ArmStats:
    arm_id: str
    mu_n:   float = _PRIOR_MEAN    # posterior mean
    var_n:  float = _PRIOR_VAR     # posterior variance
    n:      int   = 0              # number of observations
    sum_r:  float = 0.0            # sum of rewards
    sum_r2: float = 0.0            # sum of squared rewards (for variance tracking)
    last_reward: float = 0.0

    def update(self, reward: float) -> None:
        """Bayesian conjugate update for Gaussian-Gaussian model."""
        # Apply decay for non-stationarity (downweight old observations)
        self.sum_r  *= _DECAY
        self.sum_r2 *= _DECAY
        self.n       = max(0, int(self.n * _DECAY))   # effective sample count

        self.n       += 1
        self.sum_r   += reward
        self.sum_r2  += reward ** 2
        self.last_reward = reward

        # Conjugate posterior update
        prior_precision = 1.0 / _PRIOR_VAR
        data_precision  = self.n / _NOISE_VAR
        self.var_n = 1.0 / (prior_precision + data_precision)
        self.mu_n  = self.var_n * (
            _PRIOR_MEAN / _PRIOR_VAR + self.sum_r / _NOISE_VAR
        )

    def sample(self, rng: np.random.Generator) -> float:
        """Draw one sample from the posterior N(μₙ, σₙ²)."""
        return rng.normal(self.mu_n, math.sqrt(max(self.var_n, 1e-9)))

    def ucb_score(self, total_pulls: int) -> float:
        """UCB-1 score for cold-start exploration."""
        if self.n == 0:
            return float("inf")
        return self.mu_n + _UCB_C * math.sqrt(math.log(total_pulls + 1) / self.n)

    @property
    def std(self) -> float:
        return math.sqrt(max(self.var_n, 1e-9))

    def to_dict(self) -> dict:
        return {
            "arm_id":      self.arm_id,
            "posterior_mean": round(self.mu_n, 4),
            "posterior_std":  round(self.std, 4),
            "n_observations": self.n,
            "last_reward":    round(self.last_reward, 4),
        }


class ThompsonEngine:
    """
    Multi-armed bandit over DEX route arms using Gaussian-Gaussian
    Thompson Sampling with UCB-1 cold-start fallback.

    Usage:
      engine = ThompsonEngine()
      arm_id = engine.select(candidate_arms)   # pick best route to try
      engine.update(arm_id, observed_profit)   # record outcome
    """

    def __init__(self, seed: int = 42):
        self._rng  = np.random.default_rng(seed)
        self._arms: dict[str, ArmStats] = {}
        self._total_pulls = 0

    # ── Arm Initialization ─────────────────────────────────────────────────────
    def _get_or_create(self, arm_id: str) -> ArmStats:
        if arm_id not in self._arms:
            self._arms[arm_id] = ArmStats(arm_id=arm_id)
        return self._arms[arm_id]

    # ── Selection ──────────────────────────────────────────────────────────────
    def select(self, candidate_arm_ids: list[str]) -> str:
        """
        Select the best arm from candidates.
        Uses UCB-1 for arms with < MIN_PULLS, Thompson Sampling otherwise.
        Returns the arm_id with the highest sampled/UCB score.
        """
        if not candidate_arm_ids:
            raise ValueError("No candidate arms provided")

        scores = {}
        for arm_id in candidate_arm_ids:
            arm = self._get_or_create(arm_id)
            if arm.n < _MIN_PULLS:
                scores[arm_id] = arm.ucb_score(self._total_pulls)
            else:
                scores[arm_id] = arm.sample(self._rng)

        best = max(scores, key=lambda k: scores[k])
        logger.debug(
            "Thompson select: %s (score=%.4f) from %d arms",
            best[:40], scores[best], len(candidate_arm_ids)
        )
        return best

    def select_top_k(self, candidate_arm_ids: list[str], k: int = 3) -> list[str]:
        """Select top-k arms by Thompson sample score."""
        if not candidate_arm_ids:
            return []
        scores = {}
        for arm_id in candidate_arm_ids:
            arm = self._get_or_create(arm_id)
            scores[arm_id] = (
                arm.ucb_score(self._total_pulls)
                if arm.n < _MIN_PULLS
                else arm.sample(self._rng)
            )
        ranked = sorted(scores, key=lambda k: scores[k], reverse=True)
        return ranked[:k]

    # ── Update ─────────────────────────────────────────────────────────────────
    def update(self, arm_id: str, reward: float) -> None:
        """Record observed reward for arm_id and update posterior."""
        arm = self._get_or_create(arm_id)
        arm.update(reward)
        self._total_pulls += 1
        logger.debug(
            "Thompson update: %s | r=%.4f | μ=%.4f±%.4f | n=%d",
            arm_id[:30], reward, arm.mu_n, arm.std, arm.n
        )

    # ── Introspection ──────────────────────────────────────────────────────────
    def arm_stats(self, arm_id: str) -> Optional[dict]:
        arm = self._arms.get(arm_id)
        return arm.to_dict() if arm else None

    def top_arms(self, n: int = 10) -> list[dict]:
        """Return top-n arms by posterior mean."""
        ranked = sorted(self._arms.values(), key=lambda a: a.mu_n, reverse=True)
        return [a.to_dict() for a in ranked[:n]]

    def all_stats(self) -> dict:
        return {
            "total_arms":   len(self._arms),
            "total_pulls":  self._total_pulls,
            "top_arms":     self.top_arms(5),
        }
