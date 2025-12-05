import pathlib
import random
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.edge.detector import EdgeDetector
from src.models.monte_carlo import MonteCarloSimulator, SimulationConfig
from src.sports.cbb import CBBAnalyzer
from src.sports.ncaaf import NCAAFAnalyzer
from src.sports.nfl import NFLAnalyzer
from src.sports.nhl import NHLAnalyzer
from src.sports.soccer import SoccerAnalyzer


class StubStatsFetcher:
    def __init__(self, stats_by_sport):
        self.stats_by_sport = stats_by_sport

    def fetch_team_stats(self, team: str, sport: str = "basketball_nba"):
        return self.stats_by_sport[sport][team]


class TestMultiSportSynthetic(unittest.TestCase):
    def test_high_edges_surface_across_sports(self):
        random.seed(101)
        stub_stats = StubStatsFetcher(
            {
                "football_nfl": {
                    "Team A": {"pace": 70.0, "offensive_rating": 35.0},
                    "Team B": {"pace": 69.0, "offensive_rating": 34.0},
                },
                "football_cfb_fbs": {
                    "Team A": {"pace": 78.0, "offensive_rating": 42.0},
                    "Team B": {"pace": 75.0, "offensive_rating": 41.0},
                },
                "hockey_nhl": {
                    "Team A": {"pace": 65.0, "offensive_rating": 4.1},
                    "Team B": {"pace": 66.0, "offensive_rating": 3.8},
                },
                "soccer_epl": {
                    "Team A": {"pace": 1.2, "offensive_rating": 1.9},
                    "Team B": {"pace": 1.1, "offensive_rating": 1.7},
                },
                "basketball_cbb_division1": {
                    "Team A": {"pace": 73.0, "offensive_rating": 190.0},
                    "Team B": {"pace": 74.0, "offensive_rating": 186.0},
                },
            }
        )

        detector = EdgeDetector(ot_threshold=0.05, min_ev=0.01)
        simulator = MonteCarloSimulator(SimulationConfig(num_paths=500))

        analyzers = {
            "football_nfl": NFLAnalyzer(stats_fetcher=stub_stats, simulator=simulator, detector=detector),
            "football_cfb_fbs": NCAAFAnalyzer(
                stats_fetcher=stub_stats, simulator=simulator, detector=detector
            ),
            "hockey_nhl": NHLAnalyzer(stats_fetcher=stub_stats, simulator=simulator, detector=detector),
            "soccer_epl": SoccerAnalyzer(stats_fetcher=stub_stats, simulator=simulator, detector=detector),
            "basketball_cbb_division1": CBBAnalyzer(
                stats_fetcher=stub_stats, simulator=simulator, detector=detector
            ),
        }

        edges = {
            "football_nfl": analyzers["football_nfl"].analyze_game(
                game_id="nfl1",
                home_team="Team A",
                away_team="Team B",
                total_line=42.5,
                over_odds=-110,
                under_odds=-110,
                injuries=[],
            ),
            "football_cfb_fbs": analyzers["football_cfb_fbs"].analyze_game(
                game_id="cfb1",
                home_team="Team A",
                away_team="Team B",
                total_line=55.0,
                over_odds=-110,
                under_odds=-110,
                injuries=[],
            ),
            "hockey_nhl": analyzers["hockey_nhl"].analyze_game(
                game_id="nhl1",
                home_team="Team A",
                away_team="Team B",
                total_line=5.0,
                over_odds=-110,
                under_odds=-110,
                injuries=[],
            ),
            "soccer_epl": analyzers["soccer_epl"].analyze_game(
                game_id="soccer1",
                home_team="Team A",
                away_team="Team B",
                total_line=2.0,
                over_odds=-110,
                under_odds=-110,
                injuries=[],
                sport="soccer_epl",
            ),
            "basketball_cbb_division1": analyzers["basketball_cbb_division1"].analyze_game(
                game_id="cbb1",
                home_team="Team A",
                away_team="Team B",
                total_line=145.0,
                over_odds=-110,
                under_odds=-110,
                injuries=[],
            ),
        }

        for sport, edge in edges.items():
            with self.subTest(sport=sport):
                self.assertIsNotNone(edge)
                self.assertGreater(edge.wasserstein_distance, detector.ot_threshold)
                self.assertGreater(edge.expected_value, detector.min_ev)

    def test_low_edges_filtered_across_sports(self):
        random.seed(202)
        stub_stats = StubStatsFetcher(
            {
                "football_nfl": {
                    "Team A": {"pace": 100.0, "offensive_rating": 28.0},
                    "Team B": {"pace": 100.0, "offensive_rating": 28.0},
                },
                "football_cfb_fbs": {
                    "Team A": {"pace": 100.0, "offensive_rating": 35.0},
                    "Team B": {"pace": 100.0, "offensive_rating": 35.0},
                },
                "hockey_nhl": {
                    "Team A": {"pace": 100.0, "offensive_rating": 3.0},
                    "Team B": {"pace": 100.0, "offensive_rating": 3.0},
                },
                "soccer_epl": {
                    "Team A": {"pace": 100.0, "offensive_rating": 1.2},
                    "Team B": {"pace": 100.0, "offensive_rating": 1.2},
                },
                "basketball_cbb_division1": {
                    "Team A": {"pace": 100.0, "offensive_rating": 140.0},
                    "Team B": {"pace": 100.0, "offensive_rating": 140.0},
                },
            }
        )

        detector = EdgeDetector(ot_threshold=2.0, min_ev=0.2)
        simulator = MonteCarloSimulator(SimulationConfig(num_paths=600))

        analyzers = {
            "football_nfl": NFLAnalyzer(stats_fetcher=stub_stats, simulator=simulator, detector=detector),
            "football_cfb_fbs": NCAAFAnalyzer(
                stats_fetcher=stub_stats, simulator=simulator, detector=detector
            ),
            "hockey_nhl": NHLAnalyzer(stats_fetcher=stub_stats, simulator=simulator, detector=detector),
            "soccer_epl": SoccerAnalyzer(stats_fetcher=stub_stats, simulator=simulator, detector=detector),
            "basketball_cbb_division1": CBBAnalyzer(
                stats_fetcher=stub_stats, simulator=simulator, detector=detector
            ),
        }

        edges = {
            "football_nfl": analyzers["football_nfl"].analyze_game(
                game_id="nfl_even",
                home_team="Team A",
                away_team="Team B",
                total_line=56.0,
                over_odds=-110,
                under_odds=-110,
                injuries=[],
            ),
            "football_cfb_fbs": analyzers["football_cfb_fbs"].analyze_game(
                game_id="cfb_even",
                home_team="Team A",
                away_team="Team B",
                total_line=70.0,
                over_odds=-110,
                under_odds=-110,
                injuries=[],
            ),
            "hockey_nhl": analyzers["hockey_nhl"].analyze_game(
                game_id="nhl_even",
                home_team="Team A",
                away_team="Team B",
                total_line=6.0,
                over_odds=-110,
                under_odds=-110,
                injuries=[],
            ),
            "soccer_epl": analyzers["soccer_epl"].analyze_game(
                game_id="soccer_even",
                home_team="Team A",
                away_team="Team B",
                total_line=2.4,
                over_odds=-110,
                under_odds=-110,
                injuries=[],
                sport="soccer_epl",
            ),
            "basketball_cbb_division1": analyzers["basketball_cbb_division1"].analyze_game(
                game_id="cbb_even",
                home_team="Team A",
                away_team="Team B",
                total_line=140.0,
                over_odds=-110,
                under_odds=-110,
                injuries=[],
            ),
        }

        for sport, edge in edges.items():
            with self.subTest(sport=sport):
                self.assertIsNone(edge)


if __name__ == "__main__":
    unittest.main()
