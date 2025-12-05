from __future__ import annotations

from dataclasses import dataclass
from typing import List

try:
    from ..models.distribution import american_to_decimal, american_to_probability
except ImportError:  # Allows imports when edge is treated as a top-level package
    from models.distribution import american_to_decimal, american_to_probability


@dataclass
class ExpectedValueResult:
    expected_value: float
    true_prob: float
    market_prob: float


def compute_expected_value(true_prob: float, odds: int) -> ExpectedValueResult:
    market_prob = american_to_probability(odds)
    payout = american_to_decimal(odds) - 1
    # Expected return per unit stake: win probability multiplied by payout
    # minus the probability of losing the stake entirely. Using the market
    # implied probability here would understate value for true-positive edges
    # because it bakes in the bookmaker's vig.
    ev = true_prob * payout - (1 - true_prob)
    return ExpectedValueResult(expected_value=ev, true_prob=true_prob, market_prob=market_prob)


__all__: List[str] = ["ExpectedValueResult", "compute_expected_value"]
