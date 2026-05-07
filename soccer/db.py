#!/usr/bin/env python3
"""
DuckDB backend for International Soccer XGBoost model.
Stores matches, team stats, rankings, odds, and predictions for backtesting.

Usage:
    from db import Database
    db = Database()  # opens/creates soccer/soccer.duckdb
    db.ingest_match(record)
    df = db.query("SELECT * FROM matches WHERE total_goals IS NOT NULL")
"""

import json
import math
from pathlib import Path
from datetime import datetime

import duckdb

SCRIPT_DIR = Path(__file__).parent
DB_PATH = SCRIPT_DIR / 'soccer.duckdb'


class Database:
    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        self.conn = duckdb.connect(str(self.db_path))
        self._init_schema()

    def _init_schema(self):
        """Create tables if they don't exist."""
        # ─── Matches ───
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                match_id       VARCHAR PRIMARY KEY,
                date           DATE NOT NULL,
                home_team      VARCHAR NOT NULL,
                away_team      VARCHAR NOT NULL,
                tournament     VARCHAR,
                round          VARCHAR,
                venue          VARCHAR,
                neutral        BOOLEAN DEFAULT FALSE,
                home_score     INTEGER,
                away_score     INTEGER,
                total_goals    INTEGER,
                status         VARCHAR DEFAULT 'Final',
                created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # ─── Match Stats (xG, possession, shots, etc.) ───
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS match_stats (
                match_id       VARCHAR,
                -- Home team
                home_xg        DOUBLE,
                home_npxg      DOUBLE,
                home_possession DOUBLE,
                home_shots     INTEGER,
                home_shots_on  INTEGER,
                home_fouls     INTEGER,
                home_corners   INTEGER,
                home_passes    INTEGER,
                home_pass_pct  DOUBLE,
                home_yellow    INTEGER,
                home_red       INTEGER,
                -- Away team
                away_xg        DOUBLE,
                away_npxg      DOUBLE,
                away_possession DOUBLE,
                away_shots     INTEGER,
                away_shots_on  INTEGER,
                away_fouls     INTEGER,
                away_corners   INTEGER,
                away_passes    INTEGER,
                away_pass_pct  DOUBLE,
                away_yellow    INTEGER,
                away_red       INTEGER,
                PRIMARY KEY (match_id)
            );
        """)

        # ─── Team Rankings (FIFA ranking snapshots) ───
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS rankings (
                date           DATE NOT NULL,
                team           VARCHAR NOT NULL,
                rank           INTEGER,
                points         DOUBLE,
                confederation  VARCHAR,
                PRIMARY KEY (date, team)
            );
        """)

        # ─── Team Form (rolling stats calculated per match) ───
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS team_form (
                match_id       VARCHAR,
                team           VARCHAR,
                is_home        BOOLEAN,
                -- Last 5 matches
                g5_gf          DOUBLE,     -- goals for per game
                g5_ga          DOUBLE,     -- goals against per game
                g5_xgf         DOUBLE,     -- xG for per game
                g5_xga         DOUBLE,     -- xG against per game
                g5_wins        INTEGER,
                g5_draws       INTEGER,
                g5_losses      INTEGER,
                g5_clean       INTEGER,    -- clean sheets
                -- Last 10 matches
                g10_gf         DOUBLE,
                g10_ga         DOUBLE,
                g10_xgf        DOUBLE,
                g10_xga        DOUBLE,
                -- Season/campaign
                campaign_gf    DOUBLE,
                campaign_ga    DOUBLE,
                campaign_xgf   DOUBLE,
                campaign_xga   DOUBLE,
                -- Rest
                rest_days      INTEGER,
                -- Distance traveled (simplified: is it a different continent?)
                long_travel    BOOLEAN DEFAULT FALSE,
                PRIMARY KEY (match_id, team)
            );
        """)

        # ─── Odds ───
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS odds (
                date           DATE NOT NULL,
                match_id       VARCHAR,
                home_team      VARCHAR,
                away_team      VARCHAR,
                bookmaker      VARCHAR,
                market         VARCHAR,
                total_line     DOUBLE,
                over_odds      INTEGER,
                under_odds     INTEGER,
                home_ml        INTEGER,
                away_ml        INTEGER,
                draw_ml        INTEGER,
                raw_json       VARCHAR,
                PRIMARY KEY (date, match_id, bookmaker, market)
            );
        """)

        # ─── Predictions ───
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS predictions (
                date           DATE NOT NULL,
                match_id       VARCHAR,
                home_team      VARCHAR,
                away_team      VARCHAR,
                model_total    DOUBLE,
                odds_total     DOUBLE,
                edge           DOUBLE,
                confidence     VARCHAR,
                home_projected DOUBLE,
                away_projected DOUBLE,
                breakdown_json VARCHAR,
                created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (date, match_id)
            );
        """)

        # ─── Results ───
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS results (
                date           DATE NOT NULL,
                match_id       VARCHAR,
                model_total    DOUBLE,
                odds_total     DOUBLE,
                actual_total   INTEGER,
                model_error    DOUBLE,
                odds_error     DOUBLE,
                model_diff     DOUBLE,
                odds_diff      DOUBLE,
                model_hit      BOOLEAN,
                odds_hit       BOOLEAN,
                over_hit       BOOLEAN,
                PRIMARY KEY (date, match_id)
            );
        """)

        # Indexes
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_matches_date ON matches(date)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_matches_teams ON matches(home_team, away_team)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_match_stats_id ON match_stats(match_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_rankings_date ON rankings(date)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_rankings_team ON rankings(team)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_team_form_id ON team_form(match_id)")

    # ─── Ingest ──────────────────────────────────────────────────────

    def ingest_match(self, record):
        """Ingest a scraped match record into all tables."""
        match_id = record.get('match_id')
        date = record.get('date')
        if not match_id or not date:
            return

        # Matches table
        self.conn.execute("""
            INSERT OR REPLACE INTO matches
                (match_id, date, home_team, away_team, tournament, round,
                 venue, neutral, home_score, away_score, total_goals, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            match_id, date,
            record.get('home_team', ''), record.get('away_team', ''),
            record.get('tournament', ''), record.get('round', ''),
            record.get('venue', ''), record.get('neutral', False),
            record.get('home_score'), record.get('away_score'),
            record.get('total_goals'), record.get('status', 'Final'),
        ])

        # Match stats
        stats = record.get('stats', {})
        if stats:
            self.conn.execute("""
                INSERT OR REPLACE INTO match_stats
                    (match_id, home_xg, home_npxg, home_possession,
                     home_shots, home_shots_on, home_fouls, home_corners,
                     home_passes, home_pass_pct, home_yellow, home_red,
                     away_xg, away_npxg, away_possession,
                     away_shots, away_shots_on, away_fouls, away_corners,
                     away_passes, away_pass_pct, away_yellow, away_red)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                match_id,
                stats.get('home_xg'), stats.get('home_npxg'),
                stats.get('home_possession'),
                stats.get('home_shots'), stats.get('home_shots_on'),
                stats.get('home_fouls'), stats.get('home_corners'),
                stats.get('home_passes'), stats.get('home_pass_pct'),
                stats.get('home_yellow'), stats.get('home_red'),
                stats.get('away_xg'), stats.get('away_npxg'),
                stats.get('away_possession'),
                stats.get('away_shots'), stats.get('away_shots_on'),
                stats.get('away_fouls'), stats.get('away_corners'),
                stats.get('away_passes'), stats.get('away_pass_pct'),
                stats.get('away_yellow'), stats.get('away_red'),
            ])

    def ingest_ranking(self, date, team, rank, points, confederation=None):
        """Store a FIFA ranking snapshot."""
        self.conn.execute("""
            INSERT OR REPLACE INTO rankings (date, team, rank, points, confederation)
            VALUES (?, ?, ?, ?, ?)
        """, [date, team, rank, points, confederation])

    def ingest_form(self, match_id, team, is_home, form_data):
        """Store pre-match form metrics for a team."""
        self.conn.execute("""
            INSERT OR REPLACE INTO team_form
                (match_id, team, is_home,
                 g5_gf, g5_ga, g5_xgf, g5_xga,
                 g5_wins, g5_draws, g5_losses, g5_clean,
                 g10_gf, g10_ga, g10_xgf, g10_xga,
                 campaign_gf, campaign_ga, campaign_xgf, campaign_xga,
                 rest_days, long_travel)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            match_id, team, is_home,
            form_data.get('g5_gf'), form_data.get('g5_ga'),
            form_data.get('g5_xgf'), form_data.get('g5_xga'),
            form_data.get('g5_wins'), form_data.get('g5_draws'), form_data.get('g5_losses'),
            form_data.get('g5_clean'),
            form_data.get('g10_gf'), form_data.get('g10_ga'),
            form_data.get('g10_xgf'), form_data.get('g10_xga'),
            form_data.get('campaign_gf'), form_data.get('campaign_ga'),
            form_data.get('campaign_xgf'), form_data.get('campaign_xga'),
            form_data.get('rest_days'), form_data.get('long_travel', False),
        ])

    def ingest_odds(self, date, match_id, home_team, away_team,
                    bookmaker, market, odds_data):
        """Ingest odds data for a match."""
        self.conn.execute("""
            INSERT OR REPLACE INTO odds
                (date, match_id, home_team, away_team, bookmaker, market,
                 total_line, over_odds, under_odds,
                 home_ml, away_ml, draw_ml, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            date, match_id, home_team, away_team, bookmaker, market,
            odds_data.get('total_line'),
            odds_data.get('over_odds'), odds_data.get('under_odds'),
            odds_data.get('home_ml'), odds_data.get('away_ml'), odds_data.get('draw_ml'),
            json.dumps(odds_data.get('raw', {})),
        ])

    def ingest_prediction(self, date, match_id, home_team, away_team,
                          model_total, odds_total, edge, confidence,
                          home_projected, away_projected, breakdown):
        """Store a model prediction."""
        self.conn.execute("""
            INSERT OR REPLACE INTO predictions
                (date, match_id, home_team, away_team,
                 model_total, odds_total, edge, confidence,
                 home_projected, away_projected, breakdown_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            date, match_id, home_team, away_team,
            model_total, odds_total, edge, confidence,
            home_projected, away_projected,
            json.dumps(breakdown) if breakdown else None,
        ])

    def record_result(self, date, match_id, model_total, odds_total, actual_total):
        """Record match result and calculate model vs odds performance."""
        model_diff = model_total - actual_total if model_total else None
        odds_diff = odds_total - actual_total if odds_total else None
        model_error = abs(model_diff) if model_diff is not None else None
        odds_error = abs(odds_diff) if odds_diff is not None else None
        over_hit = actual_total > odds_total if odds_total else None

        self.conn.execute("""
            INSERT OR REPLACE INTO results
                (date, match_id, model_total, odds_total, actual_total,
                 model_error, odds_error, model_diff, odds_diff,
                 model_hit, odds_hit, over_hit)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            date, match_id, model_total, odds_total, actual_total,
            model_error, odds_error, model_diff, odds_diff,
            model_error is not None and model_error < odds_error if odds_error else None,
            odds_error is not None and odds_error < model_error if model_error else None,
            over_hit,
        ])

    # ─── Query helpers ───────────────────────────────────────────────

    def query(self, sql, params=None):
        """Run a SQL query and return results as a list of dicts."""
        if params:
            result = self.conn.execute(sql, params)
        else:
            result = self.conn.execute(sql)
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()
        return [dict(zip(columns, row)) for row in rows]

    def df(self, sql, params=None):
        """Run a SQL query and return a pandas DataFrame."""
        if params:
            return self.conn.execute(sql, params).df()
        return self.conn.execute(sql).df()

    def get_training_data(self):
        """Get all completed matches with features for model training."""
        return self.df("""
            SELECT
                m.match_id, m.date, m.home_team, m.away_team,
                m.tournament, m.neutral, m.total_goals,

                -- Match stats (these ARE the advanced metrics)
                ms.home_xg, ms.home_npxg, ms.home_possession,
                ms.home_shots, ms.home_shots_on,
                ms.away_xg, ms.away_npxg, ms.away_possession,
                ms.away_shots, ms.away_shots_on,

                -- Home team form
                hf.g5_gf as home_g5_gf, hf.g5_ga as home_g5_ga,
                hf.g5_xgf as home_g5_xgf, hf.g5_xga as home_g5_xga,
                hf.g5_wins as home_g5_wins, hf.g5_losses as home_g5_losses,
                hf.g5_clean as home_g5_clean,
                hf.g10_gf as home_g10_gf, hf.g10_ga as home_g10_ga,
                hf.rest_days as home_rest,

                -- Away team form
                af.g5_gf as away_g5_gf, af.g5_ga as away_g5_ga,
                af.g5_xgf as away_g5_xgf, af.g5_xga as away_g5_xga,
                af.g5_wins as away_g5_wins, af.g5_losses as away_g5_losses,
                af.g5_clean as away_g5_clean,
                af.g10_gf as away_g10_gf, af.g10_ga as away_g10_ga,
                af.rest_days as away_rest,

                -- Home ranking
                hr.rank as home_rank, hr.points as home_rank_pts,
                -- Away ranking
                ar.rank as away_rank, ar.points as away_rank_pts,

                -- Odds
                o.total_line as odds_total

            FROM matches m
            LEFT JOIN match_stats ms ON m.match_id = ms.match_id
            LEFT JOIN team_form hf ON m.match_id = hf.match_id AND hf.team = m.home_team
            LEFT JOIN team_form af ON m.match_id = af.match_id AND af.team = m.away_team
            LEFT JOIN rankings hr ON m.date = hr.date AND m.home_team = hr.team
            LEFT JOIN rankings ar ON m.date = ar.date AND m.away_team = ar.team
            LEFT JOIN odds o ON m.match_id = o.match_id
                AND o.market = 'totals'
                AND o.bookmaker = 'fanduel'
            WHERE m.total_goals IS NOT NULL
            ORDER BY m.date, m.match_id
        """)

    def get_stats_summary(self):
        """Get a summary of what's in the database."""
        tables = {}
        for t in ['matches', 'match_stats', 'rankings', 'team_form',
                   'odds', 'predictions', 'results']:
            count = self.query(f"SELECT COUNT(*) as n FROM {t}")[0]['n']
            tables[t] = count

        date_range = self.query(
            "SELECT MIN(date) as min_date, MAX(date) as max_date FROM matches"
        )[0]

        return {
            'tables': tables,
            'date_range': date_range,
        }

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


if __name__ == '__main__':
    db = Database()
    summary = db.get_stats_summary()
    print(f'\n📊 Soccer Database: {db.db_path}')
    print(f'   Date range: {summary["date_range"]["min_date"]} to {summary["date_range"]["max_date"]}')
    for table, count in summary['tables'].items():
        print(f'   {table}: {count} rows')
    db.close()
