"""
NEXUS-ARB Composite Brain
Shapley value attribution for dynamic ensemble weighting across 4 AI engines.

Shapley value for player i in coalition N with characteristic function v:
  φᵢ = Σ_{S⊆N\\{i}} [|S|!(|N|-|S|-1)!/|N|!] × [v(S∪{i}) - v(S)]

With |N|=4, exact computation over 2⁴=16 subsets.
Weights are softmax-normalized Shapley values → sum to 1.

Engines:
  0: PPO     (reinforcement learning policy)
  1: Thompson (Bayesian bandit)
  2: UKF     (Kalman filter signal quality)
  3: CMA-ES  (parameter optimization confidence)
"""
import math
import logging
import itertools
import numpy as np
from dataclasses import dataclass, field
from typing import Optional

from ppo_engine           import PPOEngine
from thompson_engine      import ThompsonEngine
from ukf_engine           import UKFEngine
from cma_es_engine        import CMAESEngine
from scanner.route_finder import ArbitrageRoute

logger = logging.getLogger(__name__)

N_ENGINES = 4
ENGINE_NAMES = ["PPO", "Thompson", "UKF", "CMA-ES"]

# Minimum confidence to recommend execution
EXEC_THRESHOLD = 0.65

# Initial uniform weights (updated by Shapley after each outcome)
_INIT_WEIGHTS = np.array([0.25, 0.25, 0.25, 0.25])


@dataclass
class DecisionOutput:
    route:           ArbitrageRoute
    execute:         bool
    composite_score: float
    engine_scores:   dict[str, float]
    engine_weights:  dict[str, float]
    shapley_values:  dict[str, float]
    reasoning:       str

    def to_dict(self) -> dict:
        return {
            "route_id":        self.route.route_id,
            "execute":         self.execute,
            "composite_score": round(self.composite_score, 4),
            "engine_scores":   {k: round(v, 4) for k, v in self.engine_scores.items()},
            "engine_weights":  {k: round(v, 4) for k, v in self.engine_weights.items()},
            "shapley_values":  {k: round(v, 4) for k, v in self.shapley_values.items()},
            "reasoning":       self.reasoning,
        }


class CompositeBrain:
    """
    Orchestrates all 4 AI engines and dynamically re-weights them via
    exact Shapley value attribution computed over observed prediction errors.

    Performance tracking:
      - Each engine's score prediction is recorded per route.
      - After outcome known, Shapley marginal contributions computed.
      - Weights updated via exponential moving average of Shapley values.
    """

    def __init__(
        self,
        ppo:      Optional[PPOEngine]      = None,
        thompson: Optional[ThompsonEngine] = None,
        ukf:      Optional[UKFEngine]      = None,
        cma:      Optional[CMAESEngine]    = None,
    ):
        self._ppo      = ppo      or PPOEngine()
        self._thompson = thompson or ThompsonEngine()
        self._ukf      = ukf      or UKFEngine()
        self._cma      = cma      or CMAESEngine()

        self._weights  = _INIT_WEIGHTS.copy()
        self._history: list[dict] = []   # (predictions, outcome) for Shapley
        self._shapley_ema = np.ones(N_ENGINES) / N_ENGINES
        self._ema_alpha = 0.1

    # ── Decision API ───────────────────────────────────────────────────────────
    def evaluate(
        self,
        route:         ArbitrageRoute,
        gas_gwei:      float,
        hour:          int,
        vol_5min:      float,
        success_rate:  float,
        current_price: float,
    ) -> DecisionOutput:
        """
        Evaluate a candidate route and return execution decision with full audit trail.
        """
        # ── Collect engine scores ─────────────────────────────────────────────
        scores = self._collect_scores(
            route, gas_gwei, hour, vol_5min, success_rate, current_price
        )

        # ── Weighted composite ────────────────────────────────────────────────
        score_vec = np.array([scores[n] for n in ENGINE_NAMES])
        composite = float(np.dot(self._weights, score_vec))
        composite = np.clip(composite, 0.0, 1.0)

        execute = composite >= EXEC_THRESHOLD

        # ── Thompson bandit selection ─────────────────────────────────────────
        thompson_pick = self._thompson.select([route.route_id])
        thompson_selected = thompson_pick == route.route_id

        reasoning = self._build_reasoning(scores, composite, execute, thompson_selected)

        # Attach confidence to route for downstream use
        route.confidence = composite

        # Store prediction for Shapley update on outcome
        self._history.append({
            "route_id":  route.route_id,
            "scores":    score_vec.copy(),
            "composite": composite,
            "outcome":   None,   # filled by record_outcome()
        })
        if len(self._history) > 500:
            self._history.pop(0)

        return DecisionOutput(
            route=route,
            execute=execute,
            composite_score=composite,
            engine_scores={n: float(scores[n]) for n in ENGINE_NAMES},
            engine_weights={n: float(self._weights[i]) for i, n in enumerate(ENGINE_NAMES)},
            shapley_values={n: float(self._shapley_ema[i]) for i, n in enumerate(ENGINE_NAMES)},
            reasoning=reasoning,
        )

    def record_outcome(self, route_id: str, actual_profit_usd: float) -> None:
        """
        Record actual outcome for a route.
        Triggers Shapley recomputation and weight update.
        Also updates Thompson Sampling posterior.
        """
        self._thompson.update(route_id, actual_profit_usd)

        # Find matching history entry
        for entry in reversed(self._history):
            if entry["route_id"] == route_id and entry["outcome"] is None:
                entry["outcome"] = actual_profit_usd
                self._update_shapley(entry)
                break

    # ── Shapley Computation ───────────────────────────────────────────────────
    def _update_shapley(self, entry: dict) -> None:
        """
        Compute exact Shapley values for this prediction instance.
        Characteristic function v(S) = negative prediction error of coalition S:
          prediction_S = mean of scores in S weighted by uniform coalition weights
          v(S) = -|actual - prediction_S|²
        """
        scores  = entry["scores"]   # shape (4,)
        outcome = entry["outcome"]

        def v(coalition: tuple) -> float:
            if not coalition:
                return 0.0
            coalition_scores = scores[list(coalition)]
            prediction = coalition_scores.mean()
            return -float((outcome - prediction) ** 2)

        n     = N_ENGINES
        phi   = np.zeros(n)
        perms = list(itertools.permutations(range(n)))

        for perm in perms:
            for pos, player in enumerate(perm):
                S_without = tuple(sorted(perm[:pos]))
                S_with    = tuple(sorted(perm[:pos + 1]))
                phi[player] += v(S_with) - v(S_without)

        phi /= len(perms)   # average over all permutations

        # Shift Shapley values to be non-negative (add min)
        phi -= phi.min()
        phi += 1e-9   # prevent zero weights

        # Softmax normalize
        exp_phi = np.exp(phi - phi.max())
        phi_norm = exp_phi / exp_phi.sum()

        # EMA update of weights
        self._shapley_ema = (
            (1 - self._ema_alpha) * self._shapley_ema
            + self._ema_alpha * phi_norm
        )
        self._weights = self._shapley_ema / self._shapley_ema.sum()

        logger.debug(
            "Shapley update | weights: PPO=%.3f Thompson=%.3f UKF=%.3f CMA=%.3f",
            *self._weights
        )

    # ── Engine Score Collection ───────────────────────────────────────────────
    def _collect_scores(
        self,
        route:         ArbitrageRoute,
        gas_gwei:      float,
        hour:          int,
        vol_5min:      float,
        success_rate:  float,
        current_price: float,
    ) -> dict[str, float]:
        liq_log = math.log(max(sum(1 for _ in route.pool_addresses) * 1e6, 1))

        # PPO: build state vector and predict
        ppo_state = self._ppo.build_state(
            profit_bps=route.profit_bps,
            gas_gwei=gas_gwei,
            liquidity_log=liq_log,
            route_len=len(route.path),
            hour=hour,
            vol_5min=vol_5min,
            success_rate=success_rate,
        )
        ppo_score = self._ppo.predict_deterministic(ppo_state)

        # Thompson: posterior mean as score (normalized to [0,1])
        t_stats = self._thompson.arm_stats(route.route_id)
        if t_stats:
            raw_mu = t_stats["posterior_mean"]
            # Normalize: assume profits in [-10, 50] USD range → [0, 1]
            thompson_score = np.clip((raw_mu + 10) / 60, 0.0, 1.0)
        else:
            thompson_score = 0.5   # prior: neutral

        # UKF: use price uncertainty as signal quality → lower uncertainty = higher score
        self._ukf.predict()
        self._ukf.update(current_price)
        ukf_diag = self._ukf.diagnostics()
        uncertainty = ukf_diag["price_uncertainty"]
        ukf_score = math.exp(-uncertainty / max(current_price * 0.01, 1e-9))
        ukf_score = float(np.clip(ukf_score, 0.0, 1.0))

        # CMA-ES: compare route params against CMA-optimized params
        best = self._cma.best_params
        profit_match = np.clip(
            route.profit_bps / max(best["min_profit_bps"], 1), 0, 2
        )
        route_len_ok = 1.0 if len(route.path) - 1 <= best["route_max_hops"] else 0.3
        cma_score = float(np.clip(profit_match * route_len_ok / 2, 0.0, 1.0))

        return {
            "PPO":      float(ppo_score),
            "Thompson": float(thompson_score),
            "UKF":      float(ukf_score),
            "CMA-ES":   float(cma_score),
        }

    def _build_reasoning(
        self, scores: dict, composite: float, execute: bool, thompson_selected: bool
    ) -> str:
        parts = []
        for name, score in scores.items():
            signal = "↑" if score >= 0.65 else ("→" if score >= 0.45 else "↓")
            parts.append(f"{name}={score:.2f}{signal}")
        verdict = "EXECUTE" if execute else "SKIP"
        return (
            f"[{verdict}] composite={composite:.3f} | "
            + " ".join(parts)
            + f" | thompson_selected={thompson_selected}"
        )

    # ── Diagnostics ────────────────────────────────────────────────────────────
    def weight_report(self) -> dict:
        return {
            "weights":       {n: round(float(self._weights[i]), 4) for i, n in enumerate(ENGINE_NAMES)},
            "shapley_ema":   {n: round(float(self._shapley_ema[i]), 4) for i, n in enumerate(ENGINE_NAMES)},
            "history_len":   len(self._history),
            "exec_threshold": EXEC_THRESHOLD,
        }
