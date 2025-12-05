from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List
import random

from .causal_graph import CausalGraph, InjuryModel, pace_adjustment
from .distribution import Distribution


@dataclass
class SimulationConfig:
    num_paths: int = 5000
    confidence_interval: float = 0.95


class MonteCarloSimulator:
    def __init__(self, config: SimulationConfig | None = None):
        self.config = config or SimulationConfig()
        self.injury_model = InjuryModel()

    def simulate_total_points(
        self,
        base_mean: float,
        base_std: float,
        injuries: Iterable[dict] | None = None,
        pace: float | None = None,
    ) -> Distribution:
        injuries = injuries or []
        adjusted_injuries = self.injury_model.estimate_impacts(injuries)
        graph = CausalGraph.from_injuries(adjusted_injuries)
        mean, std = graph.apply(base_mean, base_std)

        if pace:
            adj = pace_adjustment(pace)
            mean *= adj
            std *= adj

        samples = [random.gauss(mean, std) for _ in range(self.config.num_paths)]
        return Distribution.from_samples(samples)

    def percentile_interval(self, samples: List[float]) -> tuple[float, float]:
        samples_sorted = sorted(samples)
        lower_idx = int((1 - self.config.confidence_interval) / 2 * len(samples_sorted))
        upper_idx = int((self.config.confidence_interval + (1 - self.config.confidence_interval) / 2) * len(samples_sorted))
        lower_idx = max(0, lower_idx)
        upper_idx = min(len(samples_sorted) - 1, upper_idx)
        return samples_sorted[lower_idx], samples_sorted[upper_idx]


__all__: List[str] = ["MonteCarloSimulator", "SimulationConfig"]
