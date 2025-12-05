import pathlib
import random
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from edge.detector import EdgeDetector
from models.monte_carlo import MonteCarloSimulator, SimulationConfig
from sports.nba import NBAAnalyzer


class StubStatsFetcher:
    def __init__(self, team_stats):
        self.team_stats = team_stats

    def fetch_team_stats(self, team: str, sport: str = "basketball_nba"):
        return self.team_stats[team]


class TestNBAAnalyzerSynthetic(unittest.TestCase):
    def test_high_edge_detected_from_synthetic_totals(self):
        random.seed(42)
        stats = StubStatsFetcher(
            {
                "Team A": {"pace": 110.0, "offensive_rating": 122.0},
                "Team B": {"pace": 108.0, "offensive_rating": 120.0},
            }
        )

        analyzer = NBAAnalyzer(
            stats_fetcher=stats,
            simulator=MonteCarloSimulator(SimulationConfig(num_paths=750)),
            detector=EdgeDetector(ot_threshold=0.1, min_ev=0.03),
        )

        edge = analyzer.analyze_game(
            game_id="g1",
            home_team="Team A",
            away_team="Team B",
            total_line=125.0,
            over_odds=-110,
            under_odds=-110,
            injuries=[],
        )

        self.assertIsNotNone(edge)
        self.assertGreater(edge.wasserstein_distance, 0.1)
        self.assertGreater(edge.expected_value, 0.03)
        self.assertGreater(edge.true_prob, edge.market_prob)

    def test_conservative_threshold_blocks_low_edges(self):
        random.seed(7)
        stats = StubStatsFetcher(
            {
                "Team A": {"pace": 99.0, "offensive_rating": 111.0},
                "Team B": {"pace": 101.0, "offensive_rating": 112.0},
            }
        )

        analyzer = NBAAnalyzer(
            stats_fetcher=stats,
            simulator=MonteCarloSimulator(SimulationConfig(num_paths=400)),
            detector=EdgeDetector(ot_threshold=10.0, min_ev=0.08),
        )

        edge = analyzer.analyze_game(
            game_id="g2",
            home_team="Team A",
            away_team="Team B",
            total_line=115.0,
            over_odds=-110,
            under_odds=-110,
            injuries=[],
        )

        self.assertIsNone(edge)


if __name__ == "__main__":
    unittest.main()
