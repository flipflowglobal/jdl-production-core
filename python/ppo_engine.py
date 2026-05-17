"""
NEXUS-ARB PPO Engine
Proximal Policy Optimization with Generalized Advantage Estimation (GAE).
Pure NumPy implementation — no PyTorch/TF dependency for ARM64 compatibility.

Reference: Schulman et al. (2017) "Proximal Policy Optimization Algorithms"
           arXiv:1707.06347

State vector (8-dim):
  [profit_bps, gas_gwei_norm, liquidity_log, route_len, hour_sin, hour_cos,
   vol_5min, success_rate_ema]

Action: scalar ∈ [0, 1] — execution confidence multiplier.
"""
import math
import logging
import numpy as np
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

STATE_DIM   = 8
ACTION_DIM  = 1
HIDDEN_DIM  = 32

# PPO hyperparameters
LR_ACTOR    = 3e-4
LR_CRITIC   = 1e-3
GAMMA       = 0.99    # discount factor
LAMBDA_GAE  = 0.95   # GAE lambda
CLIP_EPS    = 0.2    # PPO clip ratio
ENTROPY_C   = 0.01   # entropy bonus coefficient
VALUE_C     = 0.5    # value loss coefficient
EPOCHS      = 4      # update epochs per batch
BATCH_SIZE  = 32


def _relu(x: np.ndarray) -> np.ndarray:
    return np.maximum(0, x)


def _softplus(x: np.ndarray) -> np.ndarray:
    return np.log1p(np.exp(np.clip(x, -20, 20)))


class LinearActor:
    """
    Two-layer linear actor: state → [mean, log_std] of Gaussian policy.
    Outputs action mean ∈ (0,1) via sigmoid.
    """

    def __init__(self, rng: np.random.Generator):
        scale = 0.1
        self.W1 = rng.normal(0, scale, (HIDDEN_DIM, STATE_DIM))
        self.b1 = np.zeros(HIDDEN_DIM)
        self.W2 = rng.normal(0, scale, (ACTION_DIM, HIDDEN_DIM))
        self.b2 = np.zeros(ACTION_DIM)
        self.log_std = np.full(ACTION_DIM, -1.0)   # trainable

        # Adam state
        self._m  = {k: np.zeros_like(v) for k, v in self._params().items()}
        self._v2 = {k: np.zeros_like(v) for k, v in self._params().items()}
        self._t  = 0

    def _params(self) -> dict:
        return {"W1": self.W1, "b1": self.b1, "W2": self.W2,
                "b2": self.b2, "log_std": self.log_std}

    def forward(self, state: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Returns (mean, std) for Gaussian policy."""
        h  = _relu(self.W1 @ state + self.b1)
        raw = self.W2 @ h + self.b2
        mean = 1.0 / (1.0 + np.exp(-raw))  # sigmoid → (0, 1)
        std  = _softplus(self.log_std) + 1e-6
        return mean, std

    def log_prob(self, state: np.ndarray, action: np.ndarray) -> float:
        """Gaussian log-probability of action given state."""
        mean, std = self.forward(state)
        return float(
            -0.5 * np.sum(((action - mean) / std) ** 2)
            - np.sum(np.log(std))
            - 0.5 * ACTION_DIM * math.log(2 * math.pi)
        )

    def entropy(self, state: np.ndarray) -> float:
        _, std = self.forward(state)
        return float(0.5 * np.sum(np.log(2 * math.pi * math.e * std ** 2)))

    def sample(self, state: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        mean, std = self.forward(state)
        raw = mean + std * rng.standard_normal(ACTION_DIM)
        return np.clip(raw, 0.0, 1.0)

    def adam_update(self, grads: dict, lr: float, beta1=0.9, beta2=0.999, eps=1e-8):
        self._t += 1
        for k, g in grads.items():
            self._m[k]  = beta1 * self._m[k]  + (1 - beta1) * g
            self._v2[k] = beta2 * self._v2[k] + (1 - beta2) * g ** 2
            m_hat = self._m[k]  / (1 - beta1 ** self._t)
            v_hat = self._v2[k] / (1 - beta2 ** self._t)
            getattr(self, k)[:] -= lr * m_hat / (np.sqrt(v_hat) + eps)


class LinearCritic:
    """Two-layer linear value function: state → scalar V(s)."""

    def __init__(self, rng: np.random.Generator):
        scale = 0.1
        self.W1 = rng.normal(0, scale, (HIDDEN_DIM, STATE_DIM))
        self.b1 = np.zeros(HIDDEN_DIM)
        self.W2 = rng.normal(0, scale, (1, HIDDEN_DIM))
        self.b2 = np.zeros(1)

        self._m  = {k: np.zeros_like(v) for k, v in self._params().items()}
        self._v2 = {k: np.zeros_like(v) for k, v in self._params().items()}
        self._t  = 0

    def _params(self) -> dict:
        return {"W1": self.W1, "b1": self.b1, "W2": self.W2, "b2": self.b2}

    def forward(self, state: np.ndarray) -> float:
        h = _relu(self.W1 @ state + self.b1)
        return float((self.W2 @ h + self.b2)[0])

    def adam_update(self, grads: dict, lr: float, beta1=0.9, beta2=0.999, eps=1e-8):
        self._t += 1
        for k, g in grads.items():
            self._m[k]  = beta1 * self._m[k]  + (1 - beta1) * g
            self._v2[k] = beta2 * self._v2[k] + (1 - beta2) * g ** 2
            m_hat = self._m[k]  / (1 - beta1 ** self._t)
            v_hat = self._v2[k] / (1 - beta2 ** self._t)
            getattr(self, k)[:] -= lr * m_hat / (np.sqrt(v_hat) + eps)


@dataclass
class Transition:
    state:      np.ndarray
    action:     np.ndarray
    reward:     float
    next_state: np.ndarray
    done:       bool
    log_prob:   float
    value:      float


class PPOEngine:
    """
    PPO agent for arbitrage execution decisions.

    Inputs a state vector describing market conditions.
    Outputs a confidence multiplier ∈ [0, 1].
    Trained online: transitions collected during live scanning,
    policy updated every BATCH_SIZE steps.
    """

    def __init__(self, seed: int = 42, checkpoint_path: Optional[Path] = None):
        self._rng     = np.random.default_rng(seed)
        self._actor   = LinearActor(self._rng)
        self._critic  = LinearCritic(self._rng)
        self._buffer:  list[Transition] = []
        self._step    = 0
        self._episode_rewards: list[float] = []

        if checkpoint_path and checkpoint_path.exists():
            self.load(checkpoint_path)

    # ── Inference ──────────────────────────────────────────────────────────────
    def predict(self, state: np.ndarray) -> float:
        """Sample action from policy. Returns scalar ∈ [0,1]."""
        s = self._normalize(state)
        action = self._actor.sample(s, self._rng)
        return float(np.clip(action[0], 0.0, 1.0))

    def predict_deterministic(self, state: np.ndarray) -> float:
        """Return policy mean (no exploration). Use during evaluation."""
        s = self._normalize(state)
        mean, _ = self._actor.forward(s)
        return float(np.clip(mean[0], 0.0, 1.0))

    # ── Learning ───────────────────────────────────────────────────────────────
    def record(self, state: np.ndarray, action: float, reward: float,
               next_state: np.ndarray, done: bool) -> None:
        s = self._normalize(state)
        t = Transition(
            state=s,
            action=np.array([action]),
            reward=reward,
            next_state=self._normalize(next_state),
            done=done,
            log_prob=self._actor.log_prob(s, np.array([action])),
            value=self._critic.forward(s),
        )
        self._buffer.append(t)
        self._step += 1
        if len(self._buffer) >= BATCH_SIZE:
            self._update()
            self._buffer.clear()

    def _compute_gae(self, transitions: list[Transition]) -> tuple[np.ndarray, np.ndarray]:
        """Generalized Advantage Estimation."""
        n = len(transitions)
        advantages = np.zeros(n)
        returns    = np.zeros(n)
        last_gae   = 0.0

        for i in reversed(range(n)):
            t = transitions[i]
            if t.done:
                next_val = 0.0
            else:
                next_val = self._critic.forward(t.next_state)
            delta = t.reward + GAMMA * next_val - t.value
            last_gae = delta + GAMMA * LAMBDA_GAE * last_gae
            advantages[i] = last_gae
            returns[i]    = advantages[i] + t.value

        # Normalize advantages
        adv_std = advantages.std() + 1e-8
        advantages = (advantages - advantages.mean()) / adv_std
        return advantages, returns

    def _update(self) -> None:
        """PPO update: clip ratio + entropy bonus."""
        transitions = self._buffer
        advantages, returns = self._compute_gae(transitions)

        for _ in range(EPOCHS):
            indices = self._rng.permutation(len(transitions))
            for i in indices:
                t = transitions[i]
                adv = advantages[i]
                ret = returns[i]

                # ── Actor update ──────────────────────────────────────────
                new_log_prob = self._actor.log_prob(t.state, t.action)
                old_log_prob = t.log_prob
                ratio = math.exp(new_log_prob - old_log_prob)
                clip_ratio = np.clip(ratio, 1 - CLIP_EPS, 1 + CLIP_EPS)
                policy_loss = -min(ratio * adv, clip_ratio * adv)
                entropy_bonus = -ENTROPY_C * self._actor.entropy(t.state)
                actor_loss = policy_loss + entropy_bonus

                # Finite-difference gradient for actor (analytical for linear nets)
                eps = 1e-5
                actor_grads = {}
                for param_name in ["W1", "b1", "W2", "b2", "log_std"]:
                    param = getattr(self._actor, param_name)
                    grad = np.zeros_like(param)
                    it = np.nditer(param, flags=["multi_index"])
                    while not it.finished:
                        idx = it.multi_index
                        orig = param[idx]
                        param[idx] = orig + eps
                        lp_plus = self._actor.log_prob(t.state, t.action)
                        param[idx] = orig - eps
                        lp_minus = self._actor.log_prob(t.state, t.action)
                        param[idx] = orig
                        r_plus  = math.exp(lp_plus  - old_log_prob)
                        r_minus = math.exp(lp_minus - old_log_prob)
                        grad[idx] = -(
                            min(r_plus  * adv, np.clip(r_plus,  1-CLIP_EPS, 1+CLIP_EPS) * adv)
                          - min(r_minus * adv, np.clip(r_minus, 1-CLIP_EPS, 1+CLIP_EPS) * adv)
                        ) / (2 * eps)
                        it.iternext()
                    actor_grads[param_name] = grad
                self._actor.adam_update(actor_grads, LR_ACTOR)

                # ── Critic update ─────────────────────────────────────────
                val = self._critic.forward(t.state)
                critic_loss = VALUE_C * (val - ret) ** 2
                critic_grads = {}
                for param_name in ["W1", "b1", "W2", "b2"]:
                    param = getattr(self._critic, param_name)
                    grad = np.zeros_like(param)
                    it = np.nditer(param, flags=["multi_index"])
                    while not it.finished:
                        idx = it.multi_index
                        orig = param[idx]
                        param[idx] = orig + eps
                        v_plus = self._critic.forward(t.state)
                        param[idx] = orig - eps
                        v_minus = self._critic.forward(t.state)
                        param[idx] = orig
                        grad[idx] = VALUE_C * (
                            (v_plus - ret)**2 - (v_minus - ret)**2
                        ) / (2 * eps)
                        it.iternext()
                    critic_grads[param_name] = grad
                self._critic.adam_update(critic_grads, LR_CRITIC)

        logger.debug(
            "PPO update | steps=%d | mean_adv=%.4f | mean_ret=%.4f",
            self._step, advantages.mean(), returns.mean()
        )

    # ── Persistence ────────────────────────────────────────────────────────────
    def save(self, path: Path) -> None:
        np.savez(
            path,
            actor_W1=self._actor.W1, actor_b1=self._actor.b1,
            actor_W2=self._actor.W2, actor_b2=self._actor.b2,
            actor_log_std=self._actor.log_std,
            critic_W1=self._critic.W1, critic_b1=self._critic.b1,
            critic_W2=self._critic.W2, critic_b2=self._critic.b2,
            step=np.array([self._step]),
        )
        logger.info("PPO checkpoint saved → %s", path)

    def load(self, path: Path) -> None:
        data = np.load(path)
        self._actor.W1, self._actor.b1     = data["actor_W1"], data["actor_b1"]
        self._actor.W2, self._actor.b2     = data["actor_W2"], data["actor_b2"]
        self._actor.log_std                = data["actor_log_std"]
        self._critic.W1, self._critic.b1   = data["critic_W1"], data["critic_b1"]
        self._critic.W2, self._critic.b2   = data["critic_W2"], data["critic_b2"]
        self._step                         = int(data["step"][0])
        logger.info("PPO checkpoint loaded ← %s (step=%d)", path, self._step)

    @staticmethod
    def _normalize(state: np.ndarray) -> np.ndarray:
        """Clip and return state as float64."""
        return np.clip(state, -10.0, 10.0).astype(np.float64)

    def build_state(
        self,
        profit_bps: float,
        gas_gwei: float,
        liquidity_log: float,
        route_len: int,
        hour: int,
        vol_5min: float,
        success_rate: float,
    ) -> np.ndarray:
        """Construct normalized state vector for the policy."""
        return np.array([
            np.clip(profit_bps / 100.0, 0, 10),     # profit in % (0–10)
            np.clip(gas_gwei / 100.0, 0, 1),         # gas norm (0–1)
            np.clip(liquidity_log / 20.0, 0, 1),     # log liquidity norm
            (route_len - 2) / 4.0,                   # route hops norm (0–1)
            math.sin(2 * math.pi * hour / 24),       # hour encoding sin
            math.cos(2 * math.pi * hour / 24),       # hour encoding cos
            np.clip(vol_5min, 0, 1),                 # volatility 0–1
            np.clip(success_rate, 0, 1),             # EMA success rate
        ], dtype=np.float64)
