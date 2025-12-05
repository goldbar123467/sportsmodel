from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List
import math


@dataclass
class CausalEffect:
    feature: str
    delta_mean: float
    delta_std: float = 0.0


class CausalGraph:
    """A minimal causal graph abstraction."""

    def __init__(self, effects: Iterable[CausalEffect] | None = None):
        self.effects: List[CausalEffect] = list(effects or [])

    def add_effect(self, feature: str, delta_mean: float, delta_std: float = 0.0) -> None:
        self.effects.append(CausalEffect(feature, delta_mean, delta_std))

    def apply(self, base_mean: float, base_std: float) -> tuple[float, float]:
        mean = base_mean
        std = base_std
        for effect in self.effects:
            mean += effect.delta_mean
            std = max(0.1, std + effect.delta_std)
        return mean, std

    @staticmethod
    def from_injuries(injuries: Iterable[dict]) -> "CausalGraph":
        graph = CausalGraph()
        for injury in injuries:
            impact = float(injury.get("impact", 0.0))
            graph.add_effect(feature=injury.get("player", "injury"), delta_mean=impact)
        return graph


class InjuryModel:
    """Translate injuries into scoring impacts using simple heuristics."""

    def estimate_impacts(self, injuries: Iterable[dict]) -> List[dict]:
        estimated = []
        for injury in injuries:
            status = injury.get("status", "").lower()
            base_impact = float(injury.get("impact", 0.0))
            if status in {"out", "doubtful"}:
                delta = -abs(base_impact) or -2.0
            elif status == "questionable":
                delta = -0.5 * abs(base_impact)
            else:
                delta = 0.0
            estimated.append({**injury, "impact": delta})
        return estimated


def pace_adjustment(pace: float) -> float:
    return max(0.8, min(pace / 100.0, 1.2))


__all__ = [
    "CausalGraph",
    "CausalEffect",
    "InjuryModel",
    "pace_adjustment",
]
