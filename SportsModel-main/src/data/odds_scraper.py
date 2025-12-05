from __future__ import annotations

from typing import List

from .team_registry import supported_sports, teams_for_sport


class OddsScraper:
    """Stub odds scraper with canned examples.

    Real integrations can be added behind this interface without
    affecting the modeling stack.
    """

    def fetch_odds_api(self, sport: str = "basketball_nba", max_games: int | None = None) -> List[dict]:
        teams = teams_for_sport(sport)
        if len(teams) < 2:
            return []

        game_templates = []
        default_totals = {
            "basketball_nba": 215.5,
            "basketball_cbb_division1": 143.5,
            "football_nfl": 44.5,
            "football_cfb_fbs": 55.5,
            "hockey_nhl": 6.0,
            "soccer_epl": 2.5,
            "soccer_laliga": 2.4,
            "soccer_bundesliga": 3.0,
            "soccer_serie_a": 2.4,
            "soccer_ligue1": 2.7,
            "soccer_champions_league": 2.9,
        }

        target_games = max_games or (len(teams) // 2)
        for idx in range(target_games):
            home_team = teams[(idx * 2) % len(teams)]
            away_team = teams[(idx * 2 + 1) % len(teams)]
            total_line = default_totals.get(sport, 50.0)
            game_templates.append(
                {
                    "game_id": f"{sport}_{idx:03d}",
                    "home_team": home_team,
                    "away_team": away_team,
                    "total_line": total_line,
                    "over_odds": -110,
                    "under_odds": -110,
                }
            )

        return game_templates

    @staticmethod
    def list_supported_sports() -> List[str]:
        return supported_sports()


__all__ = ["OddsScraper"]
