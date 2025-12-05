from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

try:
    from ..data.stats_fetcher import StatsFetcher
    from ..edge.detector import EdgeDetector, EdgeResult
    from ..models.monte_carlo import MonteCarloSimulator
    from ..models.distribution import Distribution
except ImportError:  # Allows importing when src/ is placed on sys.path directly
    from data.stats_fetcher import StatsFetcher
    from edge.detector import EdgeDetector, EdgeResult
    from models.monte_carlo import MonteCarloSimulator
    from models.distribution import Distribution


@dataclass
class GameInfo:
    game_id: str
    home_team: str
    away_team: str
    total_line: float
    over_odds: int
    under_odds: int
    injuries: Iterable[dict]


class NBAAnalyzer:
    def __init__(
        self,
        stats_fetcher: StatsFetcher | None = None,
        simulator: MonteCarloSimulator | None = None,
        detector: EdgeDetector | None = None,
    ):
        self.stats = stats_fetcher or StatsFetcher()
        self.simulator = simulator or MonteCarloSimulator()
        self.detector = detector or EdgeDetector()

    def analyze_game(
        self,
        game_id: str,
        home_team: str,
        away_team: str,
        total_line: float,
        over_odds: int,
        under_odds: int,
        injuries: Iterable[dict] | None = None,
    ) -> EdgeResult | None:
        injuries = injuries or []
        home_stats = self.stats.fetch_team_stats(home_team)
        away_stats = self.stats.fetch_team_stats(away_team)

        base_mean = (home_stats["offensive_rating"] + away_stats["offensive_rating"]) / 2
        base_std = 12.0
        pace = (home_stats["pace"] + away_stats["pace"]) / 2

        true_dist: Distribution = self.simulator.simulate_total_points(
            base_mean=base_mean, base_std=base_std, injuries=injuries, pace=pace
        )
        market_dist = Distribution.from_market_total(line=total_line, odds=over_odds)
        return self.detector.detect(true_dist=true_dist, market_dist=market_dist, odds=over_odds, bet_on_over=True)


__all__: List[str] = ["NBAAnalyzer", "GameInfo"]
