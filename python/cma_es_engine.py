"""
NEXUS-ARB CMA-ES Engine
Covariance Matrix Adaptation Evolution Strategy for parameter optimization.
"""

import logging
import numpy as np

logger = logging.getLogger(__name__)


class CMAESEngine:
    """
    Covariance Matrix Adaptation Evolution Strategy for parameter optimisation.

    Maintains a multivariate normal distribution N(m, σ²C) over the parameter
    space and adapts both the mean m and covariance C to maximise fitness.

    Parameters optimised:
      min_profit_bps  — minimum profit in basis points before executing [1, 50]
      route_max_hops  — maximum allowed hops in an arbitrage route      [2, 6]

    Reference: Hansen & Ostermeier (2001) "Completely Derandomized
    Self-Adaptation in Evolution Strategies" — MIT Press.
    """

    def __init__(self, seed: int = 42):
        self._rng = np.random.default_rng(seed)

        self._best_params = {
            "min_profit_bps": 5,
            "route_max_hops": 3,
        }

        # CMA-ES state
        self._dim = 2
        # Parameter bounds: [min_profit_bps, route_max_hops]
        self._lower = np.array([1.0, 2.0], dtype=np.float64)
        self._upper = np.array([50.0, 6.0], dtype=np.float64)

        # Initial mean (centre of search space in normalised [0,1]²)
        self._mean = np.array([0.15, 0.25], dtype=np.float64)
        self._sigma = 0.3          # global step-size in normalised space
        self._C = np.eye(self._dim)
        self._pop_size = 6 + int(3 * np.sqrt(self._dim))   # λ = 10
        self._generation = 0
        self._best_fitness = -1e9

        # Strategy parameters (derived from pop_size per Hansen recipe)
        mu = self._pop_size // 2                               # μ = 5
        weights = np.log(mu + 0.5) - np.log(np.arange(1, mu + 1))
        self._weights = weights / weights.sum()
        self._mu_eff = 1.0 / np.sum(self._weights ** 2)

        n = self._dim
        self._cc  = (4 + self._mu_eff / n) / (n + 4 + 2 * self._mu_eff / n)
        self._cs  = (self._mu_eff + 2) / (n + self._mu_eff + 5)
        self._c1  = 2.0 / ((n + 1.3) ** 2 + self._mu_eff)
        self._cmu = min(1.0 - self._c1,
                        2.0 * (self._mu_eff - 2.0 + 1.0 / self._mu_eff) /
                        ((n + 2.0) ** 2 + self._mu_eff))
        self._damps = (1.0 + 2.0 * max(0.0, np.sqrt((self._mu_eff - 1.0) /
                       (n + 1.0)) - 1.0) + self._cs)

        # Evolution paths
        self._pc = np.zeros(n)
        self._ps = np.zeros(n)

        # Eigendecomposition cache
        self._eigenval = np.ones(n)
        self._eigenvec = np.eye(n)
        self._eigen_needs_update = False

        self._expected_norm = (
            np.sqrt(n) * (1.0 - 1.0 / (4.0 * n) + 1.0 / (21.0 * n ** 2))
        )

    # ── Public API ───────────────────────────────────────────────────────────

    @property
    def best_params(self) -> dict:
        return self._best_params

    def optimize(self, fitness_func, n_generations: int = 20) -> dict:
        """
        Run CMA-ES to find optimal trading parameters.

        Parameters
        ----------
        fitness_func : callable
            Function f(normalised_params: np.ndarray) -> float that returns
            a scalar fitness to maximise.  Higher = better.
        n_generations : int
            Number of CMA-ES generations to run (default 20).

        Returns
        -------
        dict — the best parameters found, mapped to the original space:
            { "min_profit_bps": int, "route_max_hops": int }
        """
        for _ in range(n_generations):
            self._generation += 1
            population = self._sample()

            # Evaluate fitness for each individual
            fitness = np.array([fitness_func(p) for p in population])

            # Track global best
            best_idx = np.argmax(fitness)
            if fitness[best_idx] > self._best_fitness:
                self._best_fitness = fitness[best_idx]
                raw = self._denormalise(population[best_idx])
                self._best_params = {
                    "min_profit_bps": int(round(raw[0])),
                    "route_max_hops": int(round(raw[1])),
                }

            self._update(population, fitness)

        logger.info(
            "CMA-ES finished after %d gens | best fitness=%.4f | params=%s",
            self._generation, self._best_fitness, self._best_params,
        )
        return self._best_params

    # ── Sampling ─────────────────────────────────────────────────────────────

    def _sample(self) -> np.ndarray:
        """Sample λ individuals from current distribution N(m, σ²C)."""
        if self._eigen_needs_update:
            self._eigenval, self._eigenvec = np.linalg.eigh(self._C)
            self._eigen_needs_update = False

        A = self._eigenvec @ np.diag(np.sqrt(np.maximum(self._eigenval, 1e-12)))
        z = self._rng.normal(0, 1, (self._pop_size, self._dim))
        raw = self._mean + self._sigma * (z @ A.T)

        # Clamp to [0, 1] normalised space
        return np.clip(raw, 0.0, 1.0)

    # ── Distribution update ──────────────────────────────────────────────────

    def _update(self, population: np.ndarray, fitness: np.ndarray) -> None:
        """Update mean, covariance C and step-size σ."""
        n = self._dim

        # Sort by fitness descending
        idx = np.argsort(fitness)[::-1]

        # Store old mean before updating
        x_old = self._mean.copy()

        # ── Update weighted mean ──────────────────────────────────────────
        self._mean = np.sum(
            self._weights[:, None] * population[idx[:len(self._weights)]],
            axis=0,
        )

        # ── Update evolution paths ────────────────────────────────────────
        # Conjugate evolution path p_s (step-size adaptation)
        inv_sqrt_C = self._eigenvec @ np.diag(
            1.0 / np.sqrt(np.maximum(self._eigenval, 1e-12))
        ) @ self._eigenvec.T
        self._ps = ((1.0 - self._cs) * self._ps
                    + np.sqrt(self._cs * (2.0 - self._cs) * self._mu_eff)
                    * inv_sqrt_C @ (self._mean - x_old) / self._sigma)

        hsig = (np.linalg.norm(self._ps) / np.sqrt(
                1.0 - (1.0 - self._cs) ** (2.0 * self._generation))
                < 1.4 + 2.0 / (n + 1.0))

        # Anisotropic evolution path p_c (covariance adaptation)
        self._pc = ((1.0 - self._cc) * self._pc
                    + hsig * np.sqrt(self._cc * (2.0 - self._cc) * self._mu_eff)
                    * (self._mean - x_old) / self._sigma)

        # ── Update covariance C ──────────────────────────────────────────
        artmp = (population[idx[:len(self._weights)]] - x_old) / self._sigma
        self._C = ((1.0 - self._c1 - self._cmu) * self._C
                   + self._c1 * (np.outer(self._pc, self._pc)
                                 + (1.0 - hsig)
                                 * self._cc * (2.0 - self._cc) * self._C)
                   + self._cmu * (artmp.T @ np.diag(self._weights) @ artmp))

        # Enforce symmetry
        self._C = (self._C + self._C.T) * 0.5

        # ── Update step-size σ ───────────────────────────────────────────
        self._sigma *= np.exp(
            (self._cs / self._damps)
            * (np.linalg.norm(self._ps) / self._expected_norm - 1.0)
        )

        # Clamp step-size
        self._sigma = np.clip(self._sigma, 0.01, 1.0)
        self._eigen_needs_update = True

    # ── Mapping ──────────────────────────────────────────────────────────────

    # ── Default fitness (used by bridge dispatcher) ──────────────────────────

    def _default_fitness(self, norm_params: np.ndarray) -> float:
        """
        Built-in objective that rewards:
          - higher min_profit_bps (fewer false positives)
          - moderate route_max_hops (3–4 is sweet spot)
        """
        raw = self._denormalise(norm_params)
        profit_bps = raw[0]
        hops       = raw[1]

        # Prefer min_profit_bps around 5–15 (moderate threshold)
        profit_score = np.exp(-0.5 * ((profit_bps - 10.0) / 5.0) ** 2)

        # Prefer 3–4 hops (more hops = more fees, fewer = fewer opportunities)
        hop_score = np.exp(-0.5 * ((hops - 3.5) / 1.0) ** 2)

        return float(profit_score * hop_score)

    def _denormalise(self, norm_params: np.ndarray) -> np.ndarray:
        """Map normalised [0,1]² parameters back to original space."""
        return self._lower + norm_params * (self._upper - self._lower)
