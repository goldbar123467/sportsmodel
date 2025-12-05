from __future__ import annotations

from typing import Dict, List


class StatsFetcher:
    """Return lightweight team statistics with optional API hooks.

    The fetcher exposes a consistent shape so sport-specific analyzers can plug
    in external clients (NBA API, odds API, etc.) without changing the rest of
    the modeling stack. If no client is provided, baseline heuristics are used
    to keep the demo self-contained.
    """

    def __init__(self, nba_api_client=None, odds_api_client=None):
        self.nba_api_client = nba_api_client
        self.odds_api_client = odds_api_client
        self.team_adjustments: Dict[str, Dict[str, Dict[str, float]]] = {
            "basketball_nba": {
                "Golden State Warriors": {"pace": 101.5, "offensive_rating": 114.0},
                "Memphis Grizzlies": {"pace": 99.5, "defensive_rating": 110.0},
            }
        }

    def fetch_team_stats(self, team: str, sport: str = "basketball_nba") -> Dict[str, float]:
        # NBA API hook
        if sport == "basketball_nba" and self.nba_api_client:
            data = self.nba_api_client.fetch_team_metrics(team)
            if data:
                return {**self._baseline_for(sport), **data}

        # Odds API hook (for market-implied priors such as totals pace)
        if self.odds_api_client:
            hint = self.odds_api_client.fetch_team_context(team, sport)
            if hint:
                return {**self._baseline_for(sport), **hint}

        adjustments = self.team_adjustments.get(sport, {})
        return {**self._baseline_for(sport), **adjustments.get(team, {})}

    def _baseline_for(self, sport: str) -> Dict[str, float]:
        baselines: Dict[str, Dict[str, float]] = {
            "basketball_nba": {"pace": 100.0, "offensive_rating": 112.0, "defensive_rating": 111.0},
            "basketball_cbb_division1": {"pace": 68.0, "offensive_rating": 102.0, "defensive_rating": 101.0},
            "football_nfl": {"pace": 63.0, "offensive_rating": 23.0, "defensive_rating": 23.0},
            "football_cfb_fbs": {"pace": 70.0, "offensive_rating": 29.0, "defensive_rating": 29.0},
            "hockey_nhl": {"pace": 60.0, "offensive_rating": 3.1, "defensive_rating": 3.1},
            "soccer": {"pace": 1.0, "offensive_rating": 1.4, "defensive_rating": 1.2},
        }

        if sport.startswith("soccer_"):
            return baselines["soccer"]
        return baselines.get(sport, baselines["basketball_nba"])


__all__: List[str] = ["StatsFetcher"]
