"""
NEXUS-ARB UKF Engine
Unscented Kalman Filter for price signal quality estimation.
"""

import math
import logging
import numpy as np

logger = logging.getLogger(__name__)


class UKFEngine:
    """
    Unscented Kalman Filter for price signal quality estimation.

    State: [price, trend] (2D)
      price  — current estimated price
      trend  — instantaneous rate of change

    The filter maintains a covariance matrix P that tracks uncertainty.
    Lower uncertainty = higher confidence in the price signal,
    which the composite brain uses to weight UKF's score contribution.

    Unscented Transform: 2n+1 sigma points capture the true mean/covariance
    to second order (vs EKF's first-order linearisation).
    """

    def __init__(self):
        # State: [price, trend]
        self._x = np.array([0.0, 0.0], dtype=np.float64)
        # Covariance — uncertainty estimate
        self._P = np.eye(2, dtype=np.float64) * 0.1
        # Process noise
        self._Q = np.diag([0.01, 0.001])
        # Measurement noise (price observation)
        self._R = np.array([[0.05]])

        self._n = 2          # state dimension
        self._kappa = 0.0
        self._alpha = 1.0
        self._beta = 2.0
        self._lam = self._alpha ** 2 * (self._n + self._kappa) - self._n

        self._dt = 1.0
        self._initialized = False
        self._price_uncertainty = 0.05

    # ── Unscented Transform ──────────────────────────────────────────────────

    def _sigma_points(self) -> tuple:
        """Generate 2n+1 sigma points and weight vectors."""
        n = self._n
        lam = self._lam
        try:
            L = np.linalg.cholesky((n + lam) * self._P)
        except np.linalg.LinAlgError:
            self._P += np.eye(n) * 1e-6
            L = np.linalg.cholesky((n + lam) * self._P)

        sigma = np.zeros((2 * n + 1, n))
        sigma[0] = self._x
        for i in range(n):
            sigma[i + 1]     = self._x + L[:, i]
            sigma[n + i + 1] = self._x - L[:, i]

        Wm = np.full(2 * n + 1, 1.0 / (2.0 * (n + lam)))
        Wc = Wm.copy()
        Wm[0] = lam / (n + lam)
        Wc[0] = lam / (n + lam) + (1.0 - self._alpha ** 2 + self._beta)

        return sigma, Wm, Wc

    # ── Predict ──────────────────────────────────────────────────────────────

    def predict(self) -> None:
        """
        Predict next state using a constant-velocity process model.

        x_{k|k-1} = F @ x_{k-1}

        F = [[1, dt],
             [0, γ]]    where γ = 0.95 (trend decays toward zero over time)
        """
        if not self._initialized:
            self._initialized = True
            return

        sigma, Wm, Wc = self._sigma_points()
        n = self._n

        F = np.array([[1.0, self._dt],
                      [0.0, 0.95]], dtype=np.float64)

        sigma_pred = np.array([F @ s for s in sigma])

        # Predicted mean
        self._x = np.sum(Wm[:, None] * sigma_pred, axis=0)

        # Predicted covariance
        self._P = np.zeros((n, n))
        for i in range(2 * n + 1):
            diff = sigma_pred[i] - self._x
            self._P += Wc[i] * np.outer(diff, diff)
        self._P += self._Q

    # ── Update ───────────────────────────────────────────────────────────────

    def update(self, current_price: float) -> None:
        """
        Incorporate a new price observation via the Unscented Transform.

        Measurement model: z = h(x) = price (first component of state).

        Kalman gain K = P_{xz} / P_{zz}
        x_{k|k} = x_{k|k-1} + K * (z - z_predicted)
        """
        if not self._initialized:
            self._x = np.array([current_price, 0.0], dtype=np.float64)
            self._price_uncertainty = float(np.sqrt(self._P[0, 0]))
            return

        sigma, Wm, Wc = self._sigma_points()
        n = self._n

        # Measurement sigma points: h(x) = price = x[0]
        Z = sigma[:, 0]

        # Predicted measurement
        z_mean = np.sum(Wm * Z)

        # Innovation covariance P_{zz}
        Pzz = np.sum(Wc * (Z - z_mean) ** 2) + self._R[0, 0]

        # Cross covariance P_{xz}
        Pxz = np.zeros(n)
        for i in range(2 * n + 1):
            Pxz += Wc[i] * (sigma[i] - self._x) * (Z[i] - z_mean)

        # Kalman gain
        K = Pxz / max(Pzz, 1e-12)

        # State update
        innovation = current_price - z_mean
        self._x += K * innovation

        # Covariance update
        self._P -= np.outer(K, Pxz)
        self._P = (self._P + self._P.T) * 0.5  # enforce symmetry

        self._price_uncertainty = float(np.sqrt(max(self._P[0, 0], 1e-12)))

    # ── Diagnostics ──────────────────────────────────────────────────────────

    def diagnostics(self) -> dict:
        return {
            "price_uncertainty": self._price_uncertainty,
            "estimated_price":   float(self._x[0]),
            "estimated_trend":   float(self._x[1]),
        }
