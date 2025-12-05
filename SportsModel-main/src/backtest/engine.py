from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List
import random

from .metrics import PerformanceMetrics, compute_metrics


@dataclass
class BetResult:
    game_id: str
    stake_fraction: float
    edge: float
    outcome: float


class BacktestEngine:
    def __init__(self, initial_bankroll: float = 10000.0):
        self.initial_bankroll = initial_bankroll

    def run_backtest(self, edges: Iterable[BetResult]) -> PerformanceMetrics:
        returns: List[float] = []
        bankroll = self.initial_bankroll
        for bet in edges:
            stake = bankroll * bet.stake_fraction
            bankroll += stake * bet.outcome
            returns.append(bet.outcome)
        return compute_metrics(returns)

    def evaluate_outcomes(self, true_probs: Iterable[float]) -> List[float]:
        return [1 if random.random() < p else -1 for p in true_probs]


__all__: List[str] = ["BacktestEngine", "BetResult"]
