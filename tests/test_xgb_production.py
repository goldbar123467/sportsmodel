import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
XGB_DIR = ROOT / "xgb"
sys.path.insert(0, str(XGB_DIR))

import scrape  # noqa: E402
import train  # noqa: E402
import pick_today  # noqa: E402


class FakeResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return FakeResponse(self.payload)


class XgbProductionTests(unittest.TestCase):
    def test_fetch_odds_uses_the_odds_api_params_without_key_in_url(self):
        payload = [
            {
                "id": "odds-event-1",
                "commence_time": "2026-05-06T18:05:00Z",
                "away_team": "New York Yankees",
                "home_team": "Boston Red Sox",
                "bookmakers": [
                    {
                        "key": "fanduel",
                        "markets": [
                            {
                                "key": "totals",
                                "outcomes": [
                                    {"name": "Over", "price": -108, "point": 8.5},
                                    {"name": "Under", "price": -112, "point": 8.5},
                                ],
                            }
                        ],
                    },
                    {"key": "draftkings", "markets": []},
                ],
            }
        ]
        session = FakeSession(payload)

        odds = scrape.fetch_odds("2026-05-06", api_key="test_key", session=session)

        self.assertEqual(len(odds), 1)
        self.assertEqual(odds[0]["bookmaker"], "fanduel")
        self.assertEqual(odds[0]["total_line"], 8.5)
        self.assertEqual(odds[0]["over_odds"], -108)
        self.assertEqual(odds[0]["under_odds"], -112)

        call = session.calls[0]
        self.assertNotIn("test_key", call["url"])
        self.assertEqual(call["params"]["apiKey"], "test_key")
        self.assertEqual(call["params"]["bookmakers"], "fanduel")
        self.assertEqual(call["params"]["markets"], "totals")
        self.assertEqual(call["params"]["commenceTimeFrom"], "2026-05-06T04:00:00Z")
        self.assertEqual(call["params"]["commenceTimeTo"], "2026-05-07T03:59:59Z")

    def test_fetch_odds_uses_eastern_local_day_for_utc_commence_window(self):
        session = FakeSession([])

        scrape.fetch_odds("2026-05-05", api_key="test_key", session=session)

        call = session.calls[0]
        self.assertEqual(call["params"]["commenceTimeFrom"], "2026-05-05T04:00:00Z")
        self.assertEqual(call["params"]["commenceTimeTo"], "2026-05-06T03:59:59Z")

    def test_xgboost_features_exclude_market_and_target_leakage_columns(self):
        df = pd.DataFrame(
            {
                "game_pk": [1],
                "date": [pd.Timestamp("2026-05-06")],
                "away_team": ["NYY"],
                "home_team": ["BOS"],
                "stadium_name": ["Fenway Park"],
                "total_runs": [9],
                "away_score": [4],
                "home_score": [5],
                "odds_total": [8.5],
                "odds_over": [-108],
                "odds_under": [-112],
                "has_odds": [1],
                "combined_ops": [1.46],
                "park_factor": [1.03],
            }
        )

        feature_cols = train.get_feature_cols(df)

        self.assertIn("combined_ops", feature_cols)
        self.assertIn("park_factor", feature_cols)
        for blocked in (
            "total_runs",
            "away_score",
            "home_score",
            "odds_total",
            "odds_over",
            "odds_under",
            "has_odds",
        ):
            self.assertNotIn(blocked, feature_cols)

    def test_pick_builder_does_not_fabricate_missing_odds(self):
        pick = pick_today.build_pick("MIL", "STL", pred=10.0, odds=None)

        self.assertEqual(pick["pick"], "NO ODDS")
        self.assertIsNone(pick["odds"])
        self.assertIsNone(pick["edge"])

    def test_daily_cycle_uses_odds_api_scrape_and_no_sbr_scraper(self):
        cycle = (XGB_DIR / "auto_cycle.sh").read_text()

        self.assertIn("scrape.py", cycle)
        self.assertIn("pick_today.py", cycle)
        self.assertIn("America/New_York", cycle)
        self.assertNotIn("sbr_scrape.py", cycle)

    def test_live_schedule_scores_do_not_become_training_targets(self):
        original_mlb_get = scrape.mlb_get

        def fake_mlb_get(endpoint, params=None):
            self.assertEqual(endpoint, "/schedule")
            return {
                "dates": [
                    {
                        "games": [
                            {
                                "gamePk": 1001,
                                "status": {"abstractGameState": "Live"},
                                "teams": {
                                    "away": {
                                        "score": 6,
                                        "team": {"id": 147},
                                        "probablePitcher": {"id": 1, "fullName": "Away Starter"},
                                    },
                                    "home": {
                                        "score": 4,
                                        "team": {"id": 121},
                                        "probablePitcher": {"id": 2, "fullName": "Home Starter"},
                                    },
                                },
                                "venue": {"name": "Live Park"},
                            }
                        ]
                    }
                ]
            }

        try:
            scrape.mlb_get = fake_mlb_get
            games = scrape.fetch_schedule("2026-05-05")
        finally:
            scrape.mlb_get = original_mlb_get

        self.assertEqual(games[0]["status"], "Live")
        self.assertIsNone(games[0]["away_score"])
        self.assertIsNone(games[0]["home_score"])
        self.assertIsNone(games[0]["total_runs"])


if __name__ == "__main__":
    unittest.main()
