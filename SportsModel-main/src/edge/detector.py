from __future__ import annotations

from dataclasses import dataclass
from typing import List

try:
    from ..models.ot_engine import OTEngine, OTResult
    from ..models.distribution import Distribution
except ImportError:  # Allows top-level imports when src/ is on sys.path
    from models.ot_engine import OTEngine, OTResult
    from models.distribution import Distribution
from .ev_calculator import ExpectedValueResult, compute_expected_value
from .kelly import kelly_fraction


@dataclass
class EdgeResult:
    recommendation: str
    line: float
    expected_value: float
    kelly_fraction: float
    true_prob: float
    market_prob: float
    wasserstein_distance: float


class EdgeDetector:
    def __init__(self, ot_threshold: float = 0.15, min_ev: float = 0.03, kelly_cap: float = 0.05):
        self.ot_threshold = ot_threshold
        self.min_ev = min_ev
        self.kelly_cap = kelly_cap
        self.engine = OTEngine()

    def detect(
        self, true_dist: Distribution, market_dist: Distribution, odds: int, bet_on_over: bool = True
    ) -> EdgeResult | None:
        ot_result: OTResult = self.engine.distance_between_distributions(true_dist, market_dist)
        true_prob = self._probability_true_beats_line(true_dist, market_dist.mean, bet_on_over)
        ev_result: ExpectedValueResult = compute_expected_value(true_prob=true_prob, odds=odds)
        kelly = min(self.kelly_cap, kelly_fraction(win_prob=true_prob, odds=odds, fraction=0.5))

        if ot_result.distance < self.ot_threshold or ev_result.expected_value < self.min_ev:
            return None

        recommendation = "OVER" if bet_on_over else "UNDER"
        return EdgeResult(
            recommendation=recommendation,
            line=market_dist.mean,
            expected_value=ev_result.expected_value,
            kelly_fraction=kelly,
            true_prob=true_prob,
            market_prob=ev_result.market_prob,
            wasserstein_distance=ot_result.distance,
        )

    @staticmethod
    def _probability_true_beats_line(dist: Distribution, line: float, bet_on_over: bool) -> float:
        # For a normal distribution, probability of exceeding a threshold.
        z = (line - dist.mean) / (dist.std or 1e-6)
        if bet_on_over:
            return 1 - _cdf_standard_normal(z)
        return _cdf_standard_normal(z)


def _cdf_standard_normal(z: float) -> float:
    # Abramowitz & Stegun approximation for Phi(z).
    import math

    t = 1.0 / (1.0 + 0.2316419 * abs(z))
    d = 0.3989423 * math.exp(-z * z / 2)
    prob = d * t * (
        0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274)))
    )
    if z > 0:
        prob = 1 - prob
    return prob


__all__: List[str] = ["EdgeDetector", "EdgeResult"]
