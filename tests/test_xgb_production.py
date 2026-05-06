import sys
import unittest
import importlib
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
XGB_DIR = ROOT / "xgb"
sys.path.insert(0, str(XGB_DIR))

import scrape  # noqa: E402
import train  # noqa: E402
import pick_today  # noqa: E402
import recordkeeping  # noqa: E402
from db import Database  # noqa: E402


def import_weather_module():
    try:
        return importlib.import_module("weather")
    except ModuleNotFoundError as exc:
        raise AssertionError("xgb/weather.py must provide the api.weather.gov integration") from exc


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


class QueuedSession:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if not self.payloads:
            return FakeResponse({}, status_code=404)
        return FakeResponse(self.payloads.pop(0))


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

        self.assertIn("daily_bot.py", cycle)
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

    def test_settle_picks_builds_idempotent_internal_performance_summary(self):
        existing = recordkeeping.empty_performance(baseline=False)
        picks = [
            {"away": "NYY", "home": "BOS", "pick": "OVER", "odds": 8.5, "pred": 9.7, "edge": 1.2, "conf": "★★☆"},
            {"away": "LAD", "home": "SF", "pick": "UNDER", "odds": 7.0, "pred": 5.9, "edge": -1.1, "conf": "★★☆"},
            {"away": "SEA", "home": "TEX", "pick": "OVER", "odds": 9.0, "pred": 10.8, "edge": 1.8, "conf": "★★★"},
            {"away": "CHC", "home": "MIL", "pick": "PASS", "odds": 8.0, "pred": 8.2, "edge": 0.2, "conf": "☆☆☆"},
        ]
        games = [
            {"game_pk": 1, "away_team": "NYY", "home_team": "BOS", "away_score": 5, "home_score": 4, "total_runs": 9},
            {"game_pk": 2, "away_team": "LAD", "home_team": "SF", "away_score": 2, "home_score": 5, "total_runs": 7},
            {"game_pk": 3, "away_team": "SEA", "home_team": "TEX", "away_score": 2, "home_score": 3, "total_runs": 5},
        ]

        first = recordkeeping.apply_settled_date(existing, "2026-05-06", picks, games)
        second = recordkeeping.apply_settled_date(first, "2026-05-06", picks, games)

        self.assertEqual(len(second["settled_picks"]), 3)
        self.assertEqual(second["overall"]["record"], "1-1-1")
        self.assertEqual(second["overall"]["picks"], 3)
        self.assertEqual(second["by_pick_type"]["OVER"]["record"], "1-1")
        self.assertEqual(second["by_pick_type"]["UNDER"]["record"], "0-0-1")
        self.assertEqual(second["by_confidence"]["Medium"]["record"], "1-0-1")
        self.assertEqual(second["by_confidence"]["High"]["record"], "0-1")

    def test_readme_record_block_is_rendered_from_performance_summary(self):
        performance = recordkeeping.empty_performance(baseline=False)
        performance = recordkeeping.apply_settled_date(
            performance,
            "2026-05-06",
            [{"away": "NYY", "home": "BOS", "pick": "OVER", "odds": 8.5, "pred": 9.7, "edge": 1.2, "conf": "★★☆"}],
            [{"game_pk": 1, "away_team": "NYY", "home_team": "BOS", "away_score": 5, "home_score": 4, "total_runs": 9}],
        )
        readme = "# Title\n\n## Performance Snapshot\nold stats\n\n## What The Model Does\nbody\n"

        updated = recordkeeping.update_readme_text(readme, performance)

        self.assertIn("<!-- SPORTSBOTV2_RECORD_START -->", updated)
        self.assertIn("**Overall record:** 1-0", updated)
        self.assertIn("| May 6 | 1-0 | 100.0% | 1 |", updated)
        self.assertIn("## What The Model Does\nbody", updated)

    def test_cron_defaults_to_11am_eastern_telegram_cycle(self):
        cron = (XGB_DIR / "install_cron.sh").read_text()

        self.assertIn("0 11 * * *", cron)
        self.assertIn("daily MLB Telegram cycle", cron)
        self.assertNotIn("55 9 * * *", cron)

    def test_nws_observation_weather_uses_headers_and_normalizes_units(self):
        weather = import_weather_module()
        session = QueuedSession(
            [
                {
                    "properties": {
                        "observationStations": "https://api.weather.gov/gridpoints/BOX/70,76/stations",
                    }
                },
                {
                    "features": [
                        {"id": "https://api.weather.gov/stations/KBOS"},
                    ]
                },
                {
                    "features": [
                        {
                            "properties": {
                                "timestamp": "2026-05-06T23:54:00+00:00",
                                "temperature": {"value": 16.1, "unitCode": "wmoUnit:degC"},
                                "windSpeed": {"value": 19.0, "unitCode": "wmoUnit:km_h"},
                                "windDirection": {"value": 226.0, "unitCode": "wmoUnit:degree_(angle)"},
                                "relativeHumidity": {"value": 64.0, "unitCode": "wmoUnit:percent"},
                                "barometricPressure": {"value": 101220.0, "unitCode": "wmoUnit:Pa"},
                            }
                        }
                    ]
                },
            ]
        )
        game = {
            "game_pk": 1,
            "date": "2026-05-06",
            "game_time_utc": "2026-05-06T23:35:00Z",
            "home_team": "BOS",
        }
        stadium = {"lat": 42.3467, "lon": -71.0972, "roof": False, "cfBearing": 46}

        result = weather.fetch_weather_for_game(
            game,
            stadium,
            session=session,
            now_utc="2026-05-07T04:00:00Z",
        )

        self.assertEqual(result["weather_source"], "api.weather.gov:observations")
        self.assertEqual(result["weather_station"], "KBOS")
        self.assertAlmostEqual(result["weather_temp_f"], 61.0, places=1)
        self.assertAlmostEqual(result["weather_wind_mph"], 11.8, places=1)
        self.assertAlmostEqual(result["weather_wind_out_cf"], 1.0, places=2)
        self.assertEqual(result["weather_humidity_pct"], 64.0)
        self.assertAlmostEqual(result["weather_pressure_mb"], 1012.2, places=1)
        self.assertFalse(result["weather_is_indoor"])

        self.assertTrue(session.calls[0]["url"].startswith("https://api.weather.gov/points/"))
        self.assertTrue(session.calls[2]["url"].endswith("/stations/KBOS/observations"))
        for call in session.calls:
            self.assertIn("User-Agent", call["headers"])
            self.assertNotIn("apiKey", call.get("params") or {})

    def test_nws_hourly_forecast_weather_is_used_for_future_games(self):
        weather = import_weather_module()
        session = QueuedSession(
            [
                {
                    "properties": {
                        "forecastHourly": "https://api.weather.gov/gridpoints/BOX/70,76/forecast/hourly",
                    }
                },
                {
                    "properties": {
                        "periods": [
                            {
                                "startTime": "2026-05-06T22:00:00+00:00",
                                "temperature": 70,
                                "temperatureUnit": "F",
                                "windSpeed": "8 mph",
                                "windDirection": "W",
                                "relativeHumidity": {"value": 50},
                                "probabilityOfPrecipitation": {"value": 10},
                            },
                            {
                                "startTime": "2026-05-06T23:00:00+00:00",
                                "temperature": 59,
                                "temperatureUnit": "F",
                                "windSpeed": "12 mph",
                                "windDirection": "SW",
                                "relativeHumidity": {"value": 72},
                                "probabilityOfPrecipitation": {"value": 20},
                            },
                        ]
                    }
                },
            ]
        )

        result = weather.fetch_weather_for_game(
            {
                "game_pk": 2,
                "date": "2026-05-06",
                "game_time_utc": "2026-05-06T23:35:00Z",
                "home_team": "BOS",
            },
            {"lat": 42.3467, "lon": -71.0972, "roof": False, "cfBearing": 46},
            session=session,
            now_utc="2026-05-06T15:00:00Z",
        )

        self.assertEqual(result["weather_source"], "api.weather.gov:forecastHourly")
        self.assertEqual(result["weather_temp_f"], 59.0)
        self.assertEqual(result["weather_wind_mph"], 12.0)
        self.assertEqual(result["weather_humidity_pct"], 72.0)
        self.assertEqual(result["weather_precip_pct"], 20.0)

    def test_game_weather_is_persisted_in_duckdb(self):
        db_path = Path("/tmp/sportsbotv2_weather_test.duckdb")
        if db_path.exists():
            db_path.unlink()
        db = Database(db_path)
        try:
            db.ingest_game(
                {
                    "date": "2026-05-06",
                    "game_pk": 1001,
                    "away_team": "NYY",
                    "home_team": "BOS",
                    "venue": "Fenway Park",
                    "status": "Final",
                    "away_score": 4,
                    "home_score": 5,
                    "total_runs": 9,
                    "game_time_utc": "2026-05-06T23:35:00Z",
                    "stadium": {
                        "name": "Fenway Park",
                        "roof": False,
                        "altitude": 10,
                        "cfBearing": 46,
                    },
                    "weather": {
                        "weather_temp_f": 61.0,
                        "weather_wind_mph": 11.8,
                        "weather_wind_dir_degrees": 226.0,
                        "weather_wind_out_cf": 1.0,
                        "weather_humidity_pct": 64.0,
                        "weather_precip_pct": 20.0,
                        "weather_pressure_mb": 1012.2,
                        "weather_is_indoor": False,
                        "weather_source": "api.weather.gov:observations",
                        "weather_station": "KBOS",
                        "weather_observed_at": "2026-05-06T23:54:00+00:00",
                    },
                }
            )
            row = db.query(
                """
                SELECT game_time_utc, weather_temp_f, weather_wind_mph,
                       weather_wind_out_cf, weather_source, weather_station
                FROM games WHERE game_pk = 1001
                """
            )[0]
        finally:
            db.close()
            if db_path.exists():
                db_path.unlink()

        self.assertEqual(row["weather_temp_f"], 61.0)
        self.assertEqual(row["weather_wind_mph"], 11.8)
        self.assertEqual(row["weather_wind_out_cf"], 1.0)
        self.assertEqual(row["weather_source"], "api.weather.gov:observations")
        self.assertEqual(row["weather_station"], "KBOS")

    def test_weather_features_are_available_to_the_model_with_indoor_neutralization(self):
        df = pd.DataFrame(
            {
                "stadium_roof": [False, True],
                "stadium_cf_bearing": [46, 46],
                "weather_temp_f": [82.0, 95.0],
                "weather_wind_mph": [12.0, 20.0],
                "weather_wind_out_cf": [1.0, -1.0],
                "weather_humidity_pct": [70.0, 90.0],
                "weather_precip_pct": [30.0, 60.0],
                "weather_pressure_mb": [1010.0, 990.0],
                "weather_is_indoor": [False, True],
                "total_runs": [9, 8],
            }
        )

        out = train.add_weather_features(df)
        feature_cols = train.get_feature_cols(out)

        self.assertIn("weather_temp_filled", feature_cols)
        self.assertIn("weather_wind_mph_filled", feature_cols)
        self.assertIn("weather_run_environment", feature_cols)
        self.assertEqual(out.loc[1, "weather_temp_filled"], 72.0)
        self.assertEqual(out.loc[1, "weather_wind_mph_filled"], 0.0)
        self.assertEqual(out.loc[1, "weather_wind_run_factor"], 0.0)

    def test_backfill_marks_stale_nws_observations_unavailable_without_api_calling(self):
        weather = import_weather_module()
        db_path = Path("/tmp/sportsbotv2_weather_stale_test.duckdb")
        if db_path.exists():
            db_path.unlink()
        db = Database(db_path)
        try:
            db.ingest_game(
                {
                    "date": "2024-04-25",
                    "game_pk": 2001,
                    "away_team": "NYY",
                    "home_team": "BOS",
                    "game_time_utc": "2024-04-25T23:10:00Z",
                    "venue": "Fenway Park",
                    "status": "Final",
                    "away_score": 4,
                    "home_score": 5,
                    "total_runs": 9,
                    "stadium": {
                        "name": "Fenway Park",
                        "roof": False,
                        "altitude": 10,
                        "cfBearing": 46,
                    },
                }
            )
        finally:
            db.close()

        session = QueuedSession([])
        result = weather.backfill_weather(
            db_path=db_path,
            start_date="2024-04-25",
            end_date="2024-04-25",
            max_observation_age_days=14,
            now_utc="2026-05-06T00:00:00Z",
            sleep_s=0,
            session=session,
        )
        db = Database(db_path)
        try:
            row = db.query("SELECT weather_source FROM games WHERE game_pk = 2001")[0]
        finally:
            db.close()
            if db_path.exists():
                db_path.unlink()

        self.assertEqual(result["unavailable"], 1)
        self.assertEqual(row["weather_source"], "api.weather.gov:observations:unavailable")
        self.assertEqual(session.calls, [])


if __name__ == "__main__":
    unittest.main()
