from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

try:
    from ..data.stats_fetcher import StatsFetcher
    from ..edge.detector import EdgeDetector, EdgeResult
    from ..models.distribution import Distribution
    from ..models.monte_carlo import MonteCarloSimulator
except ImportError:  # Allows importing when src/ is on sys.path directly
    from data.stats_fetcher import StatsFetcher
    from edge.detector import EdgeDetector, EdgeResult
    from models.distribution import Distribution
    from models.monte_carlo import MonteCarloSimulator


@dataclass
class GameInfo:
    game_id: str
    home_team: str
    away_team: str
    total_line: float
    over_odds: int
    under_odds: int
    injuries: Iterable[dict]


class SoccerAnalyzer:
    def __init__(
        self,
        odds_api_client=None,
        stats_fetcher: StatsFetcher | None = None,
        simulator: MonteCarloSimulator | None = None,
        detector: EdgeDetector | None = None,
    ):
        self.stats = stats_fetcher or StatsFetcher(odds_api_client=odds_api_client)
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
        sport: str = "soccer_epl",
    ) -> EdgeResult | None:
        injuries = injuries or []
        home_stats = self.stats.fetch_team_stats(home_team, sport=sport)
        away_stats = self.stats.fetch_team_stats(away_team, sport=sport)

        base_mean = (home_stats["offensive_rating"] + away_stats["offensive_rating"])
        base_std = 0.85
        pace = (home_stats["pace"] + away_stats["pace"]) / 2

        true_dist = self.simulator.simulate_total_points(
            base_mean=base_mean, base_std=base_std, injuries=injuries, pace=pace
        )
        market_dist = Distribution.from_market_total(line=total_line, odds=over_odds)
        return self.detector.detect(true_dist=true_dist, market_dist=market_dist, odds=over_odds, bet_on_over=True)


__all__: List[str] = ["SoccerAnalyzer", "GameInfo"]
