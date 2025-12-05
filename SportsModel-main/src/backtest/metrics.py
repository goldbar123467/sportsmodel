from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List
import math


@dataclass
class PerformanceMetrics:
    roi: float
    sharpe_ratio: float
    max_drawdown: float


def compute_metrics(returns: Iterable[float]) -> PerformanceMetrics:
    returns_list = list(returns)
    if not returns_list:
        return PerformanceMetrics(roi=0.0, sharpe_ratio=0.0, max_drawdown=0.0)

    cumulative = []
    total = 0.0
    for r in returns_list:
        total += r
        cumulative.append(total)

    peak = []
    running_peak = -math.inf
    for value in cumulative:
        running_peak = max(running_peak, value)
        peak.append(running_peak)

    drawdown = [c - p for c, p in zip(cumulative, peak)]

    mean_return = sum(returns_list) / len(returns_list)
    variance = sum((r - mean_return) ** 2 for r in returns_list) / max(1, len(returns_list) - 1)
    std_dev = math.sqrt(variance)

    roi = cumulative[-1]
    sharpe = (mean_return / (std_dev + 1e-6)) * math.sqrt(252)
    max_dd = min(drawdown)
    return PerformanceMetrics(roi=roi, sharpe_ratio=sharpe, max_drawdown=max_dd)


__all__: List[str] = ["PerformanceMetrics", "compute_metrics"]
