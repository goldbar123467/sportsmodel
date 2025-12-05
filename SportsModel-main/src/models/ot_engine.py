from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List
import math

from .distribution import Distribution


@dataclass
class OTResult:
    distance: float
    transport_cost: float
    method: str
    mean_shift: float
    scale_shift: float
    gradient_mean: float
    gradient_scale: float


class OTEngine:
    def __init__(self, p_norm: int = 2, reg: float = 0.01):
        self.p_norm = p_norm
        self.reg = reg

    def distance(self, true_samples: Iterable[float], market_samples: Iterable[float]) -> OTResult:
        dist = self._quantile_wasserstein(true_samples, market_samples, self.p_norm)
        cost = dist / (1 + self.reg)
        return OTResult(
            distance=dist,
            transport_cost=cost,
            method="quantile",
            mean_shift=0.0,
            scale_shift=0.0,
            gradient_mean=0.0,
            gradient_scale=0.0,
        )

    def distance_between_distributions(self, true_dist: Distribution, market_dist: Distribution) -> OTResult:
        analytic = self._gaussian_distance(true_dist, market_dist)
        # Lightweight Monte Carlo refinement to capture tail mismatches without heavy sampling.
        true_samples = true_dist.sample(512)
        market_samples = market_dist.sample(512)
        empirical = self.distance(true_samples, market_samples)

        # Blend analytic structure (linear algebra on mean/variance) with empirical tail signal.
        blended_distance = math.hypot(analytic.distance, empirical.distance)
        cost = blended_distance / (1 + self.reg)
        return OTResult(
            distance=blended_distance,
            transport_cost=cost,
            method="gaussian+quantile",
            mean_shift=analytic.mean_shift,
            scale_shift=analytic.scale_shift,
            gradient_mean=analytic.gradient_mean,
            gradient_scale=analytic.gradient_scale,
        )

    def _gaussian_distance(self, true_dist: Distribution, market_dist: Distribution) -> OTResult:
        mean_shift = true_dist.mean - market_dist.mean
        scale_shift = max(true_dist.std, 1e-6) - max(market_dist.std, 1e-6)

        # Wasserstein-p distance between 1D Gaussians; for p=2 this reduces to L2 on mean/std.
        if self.p_norm == 1:
            distance = abs(mean_shift) + abs(scale_shift)
            gradient_mean = math.copysign(1.0, mean_shift) if mean_shift != 0 else 0.0
            gradient_scale = math.copysign(1.0, scale_shift) if scale_shift != 0 else 0.0
        else:
            distance = (abs(mean_shift) ** self.p_norm + abs(scale_shift) ** self.p_norm) ** (
                1.0 / self.p_norm
            )
            # Calculus-based sensitivity of the distance w.r.t. mean and scale terms.
            eps = 1e-9
            gradient_mean = (abs(mean_shift) ** (self.p_norm - 1) * math.copysign(1.0, mean_shift)) / (
                (distance ** (self.p_norm - 1)) + eps
            )
            gradient_scale = (abs(scale_shift) ** (self.p_norm - 1) * math.copysign(1.0, scale_shift)) / (
                (distance ** (self.p_norm - 1)) + eps
            )

        cost = distance / (1 + self.reg)
        return OTResult(
            distance=distance,
            transport_cost=cost,
            method="gaussian",
            mean_shift=mean_shift,
            scale_shift=scale_shift,
            gradient_mean=gradient_mean,
            gradient_scale=gradient_scale,
        )

    @staticmethod
    def _quantile_wasserstein(values_a: Iterable[float], values_b: Iterable[float], p_norm: int) -> float:
        sorted_a = sorted(values_a)
        sorted_b = sorted(values_b)
        n_points = max(32, min(len(sorted_a), len(sorted_b)))
        if n_points == 0:
            return 0.0

        def percentile(sorted_vals: List[float], q: float) -> float:
            pos = q * (len(sorted_vals) - 1)
            low = int(math.floor(pos))
            high = int(math.ceil(pos))
            if low == high:
                return sorted_vals[low]
            weight = pos - low
            return sorted_vals[low] * (1 - weight) + sorted_vals[high] * weight

        acc = 0.0
        for i in range(n_points):
            q = i / (n_points - 1)
            a_q = percentile(sorted_a, q)
            b_q = percentile(sorted_b, q)
            acc += abs(a_q - b_q) ** p_norm

        return (acc / n_points) ** (1.0 / p_norm)


__all__: List[str] = ["OTEngine", "OTResult"]
