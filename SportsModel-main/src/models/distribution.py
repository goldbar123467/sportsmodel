from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, stdev
from typing import Iterable, List
import math
import random


@dataclass
class Distribution:
    """Simple wrapper around a normal distribution."""

    mean: float
    std: float

    def sample(self, n: int) -> list[float]:
        return [random.gauss(self.mean, self.std) for _ in range(n)]

    def shift(self, delta_mean: float = 0.0, delta_std: float = 0.0) -> "Distribution":
        return Distribution(mean=self.mean + delta_mean, std=max(0.1, self.std + delta_std))

    @staticmethod
    def from_samples(samples: Iterable[float]) -> "Distribution":
        samples_list = list(samples)
        if len(samples_list) < 2:
            raise ValueError("Need at least two samples to estimate distribution")
        return Distribution(mean=mean(samples_list), std=stdev(samples_list))

    @staticmethod
    def from_market_total(line: float, odds: int) -> "Distribution":
        implied_prob = american_to_probability(odds)
        volatility_padding = 12.0
        std = volatility_padding * (0.5 + abs(0.5 - implied_prob))
        return Distribution(mean=line, std=std)


def american_to_probability(odds: int) -> float:
    if odds == 0:
        raise ValueError("Odds cannot be zero")
    if odds > 0:
        return 100 / (odds + 100)
    return -odds / (-odds + 100)


def american_to_decimal(odds: int) -> float:
    if odds == 0:
        raise ValueError("Odds cannot be zero")
    if odds > 0:
        return 1 + odds / 100
    return 1 + 100 / -odds


def mean_absolute_difference(values_a: Iterable[float], values_b: Iterable[float]) -> float:
    sorted_a = sorted(values_a)
    sorted_b = sorted(values_b)
    count = min(len(sorted_a), len(sorted_b))
    if count == 0:
        return 0.0
    total = sum(abs(a - b) for a, b in zip(sorted_a[:count], sorted_b[:count]))
    return total / count


__all__: List[str] = [
    "Distribution",
    "american_to_probability",
    "american_to_decimal",
    "mean_absolute_difference",
]
