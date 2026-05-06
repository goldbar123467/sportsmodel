#!/usr/bin/env python3
"""
DuckDB backend for MLB XGBoost model.
Stores games, stats, Statcast data, odds, and predictions for backtesting.

Usage:
    from db import Database
    db = Database()  # opens/creates xgb/mlb.duckdb
    db.ingest_game(record)
    df = db.query("SELECT * FROM games WHERE total_runs IS NOT NULL")
"""

import json
import math
from pathlib import Path
from datetime import datetime

import duckdb

SCRIPT_DIR = Path(__file__).parent
DB_PATH = SCRIPT_DIR / 'mlb.duckdb'

GAME_WEATHER_COLUMNS = {
    'game_time_utc': 'TIMESTAMP',
    'weather_temp_f': 'DOUBLE',
    'weather_wind_mph': 'DOUBLE',
    'weather_wind_dir_degrees': 'DOUBLE',
    'weather_wind_out_cf': 'DOUBLE',
    'weather_humidity_pct': 'DOUBLE',
    'weather_precip_pct': 'DOUBLE',
    'weather_pressure_mb': 'DOUBLE',
    'weather_is_indoor': 'BOOLEAN',
    'weather_source': 'VARCHAR',
    'weather_station': 'VARCHAR',
    'weather_observed_at': 'TIMESTAMP',
}


class Database:
    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        self.conn = duckdb.connect(str(self.db_path))
        self._init_schema()

    def _init_schema(self):
        """Create tables if they don't exist."""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS games (
                game_pk        INTEGER PRIMARY KEY,
                date           DATE NOT NULL,
                away_team      VARCHAR NOT NULL,
                home_team      VARCHAR NOT NULL,
                away_pitcher   VARCHAR,
                home_pitcher   VARCHAR,
                away_pitcher_id INTEGER,
                home_pitcher_id INTEGER,
                game_time_utc  TIMESTAMP,
                venue          VARCHAR,
                status         VARCHAR,
                away_score     INTEGER,
                home_score     INTEGER,
                total_runs     INTEGER,
                park_factor    DOUBLE,
                stadium_name   VARCHAR,
                stadium_roof   BOOLEAN,
                stadium_alt    INTEGER,
                stadium_cf_bearing INTEGER,
                weather_temp_f DOUBLE,
                weather_wind_mph DOUBLE,
                weather_wind_dir_degrees DOUBLE,
                weather_wind_out_cf DOUBLE,
                weather_humidity_pct DOUBLE,
                weather_precip_pct DOUBLE,
                weather_pressure_mb DOUBLE,
                weather_is_indoor BOOLEAN,
                weather_source VARCHAR,
                weather_station VARCHAR,
                weather_observed_at TIMESTAMP,
                created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self._ensure_columns('games', GAME_WEATHER_COLUMNS)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS team_hitting (
                date           DATE NOT NULL,
                team           VARCHAR NOT NULL,
                pa             INTEGER,
                avg            DOUBLE,
                obp            DOUBLE,
                slg            DOUBLE,
                ops            DOUBLE,
                hr             INTEGER,
                runs           INTEGER,
                bb             INTEGER,
                k              INTEGER,
                sb             INTEGER,
                hits           INTEGER,
                ab             INTEGER,
                doubles        INTEGER,
                triples        INTEGER,
                rbi            INTEGER,
                babip          DOUBLE,
                iso            DOUBLE,
                bb_rate        DOUBLE,
                k_rate         DOUBLE,
                hr_rate        DOUBLE,
                PRIMARY KEY (date, team)
            );
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS statcast_batting (
                date           DATE NOT NULL,
                team           VARCHAR NOT NULL,
                avg_xwoba      DOUBLE,
                avg_brl_pct    DOUBLE,
                avg_exit_velo  DOUBLE,
                avg_hard_hit   DOUBLE,
                avg_sprint     DOUBLE,
                player_count   INTEGER,
                PRIMARY KEY (date, team)
            );
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS statcast_pitching (
                date           DATE NOT NULL,
                team           VARCHAR NOT NULL,
                avg_xera       DOUBLE,
                avg_brl_against DOUBLE,
                avg_ev_against DOUBLE,
                avg_fb_velo    DOUBLE,
                avg_fb_spin    DOUBLE,
                pitcher_count  INTEGER,
                PRIMARY KEY (date, team)
            );
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS pitcher_logs (
                date           DATE NOT NULL,
                pitcher_id     INTEGER NOT NULL,
                pitcher_name   VARCHAR,
                team           VARCHAR,
                ip             DOUBLE,
                fip            DOUBLE,
                era            DOUBLE,
                k              INTEGER,
                bb             INTEGER,
                hr             INTEGER,
                er             INTEGER,
                hits           INTEGER,
                bf             INTEGER,
                games          INTEGER,
                avg_pitches    DOUBLE,
                avg_ip         DOUBLE,
                PRIMARY KEY (date, pitcher_id)
            );
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS pitcher_statcast (
                date           DATE NOT NULL,
                pitcher_id     INTEGER NOT NULL,
                pitcher_name   VARCHAR,
                team           VARCHAR,
                xwoba_against  DOUBLE,
                xba_against    DOUBLE,
                xslg_against   DOUBLE,
                xera           DOUBLE,
                brl_pct_against DOUBLE,
                exit_velo_against DOUBLE,
                hard_hit_against DOUBLE,
                k_pct          DOUBLE,
                bb_pct         DOUBLE,
                whiff_pct      DOUBLE,
                fb_velocity    DOUBLE,
                fb_spin        DOUBLE,
                curve_spin     DOUBLE,
                PRIMARY KEY (date, pitcher_id)
            );
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS odds (
                date           DATE NOT NULL,
                game_pk        INTEGER,
                away_team      VARCHAR,
                home_team      VARCHAR,
                bookmaker      VARCHAR,
                market         VARCHAR,
                away_line      DOUBLE,
                home_line      DOUBLE,
                total_line     DOUBLE,
                over_odds      INTEGER,
                under_odds     INTEGER,
                away_ml        INTEGER,
                home_ml        INTEGER,
                away_spread    DOUBLE,
                home_spread    DOUBLE,
                away_spread_odds INTEGER,
                home_spread_odds INTEGER,
                raw_json       VARCHAR,
                PRIMARY KEY (date, game_pk, bookmaker, market)
            );
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS predictions (
                date           DATE NOT NULL,
                game_pk        INTEGER,
                away_team      VARCHAR,
                home_team      VARCHAR,
                model_total    DOUBLE,
                odds_total     DOUBLE,
                edge           DOUBLE,
                confidence     VARCHAR,
                away_projected DOUBLE,
                home_projected DOUBLE,
                breakdown_json VARCHAR,
                created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (date, game_pk)
            );
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS results (
                date           DATE NOT NULL,
                game_pk        INTEGER,
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
                PRIMARY KEY (date, game_pk)
            );
        """)

        # Create indexes for common queries
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_games_date ON games(date)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_games_teams ON games(away_team, home_team)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_th_date ON team_hitting(date)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_sc_bat_date ON statcast_batting(date)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_odds_date ON odds(date)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_pred_date ON predictions(date)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_results_date ON results(date)")

    def _ensure_columns(self, table, columns):
        """Add nullable columns for existing DuckDB files."""
        existing = {
            row[1]
            for row in self.conn.execute(f"PRAGMA table_info('{table}')").fetchall()
        }
        for name, sql_type in columns.items():
            if name not in existing:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")

    # ─── Ingest ──────────────────────────────────────────────────────────

    def ingest_game(self, record):
        """Ingest a scraped game record into all tables."""
        date = record.get('date')
        game_pk = record.get('game_pk')
        if not date or not game_pk:
            return

        stadium = record.get('stadium', {})
        weather = record.get('weather', {}) or {}
        game_cols = [
            'game_pk', 'date', 'away_team', 'home_team',
            'away_pitcher', 'home_pitcher',
            'away_pitcher_id', 'home_pitcher_id',
            'game_time_utc', 'venue', 'status',
            'away_score', 'home_score', 'total_runs',
            'park_factor', 'stadium_name', 'stadium_roof',
            'stadium_alt', 'stadium_cf_bearing',
            'weather_temp_f', 'weather_wind_mph',
            'weather_wind_dir_degrees', 'weather_wind_out_cf',
            'weather_humidity_pct', 'weather_precip_pct',
            'weather_pressure_mb', 'weather_is_indoor',
            'weather_source', 'weather_station', 'weather_observed_at',
        ]
        game_values = [
            game_pk, date,
            record.get('away_team', ''), record.get('home_team', ''),
            record.get('away_pitcher', ''), record.get('home_pitcher', ''),
            record.get('away_pitcher_id'), record.get('home_pitcher_id'),
            record.get('game_time_utc'),
            record.get('venue', ''), record.get('status', ''),
            record.get('away_score'), record.get('home_score'),
            record.get('total_runs'),
            record.get('park_factor', 1.0),
            stadium.get('name', ''), stadium.get('roof', False),
            stadium.get('altitude', 0), stadium.get('cfBearing', 0),
            weather.get('weather_temp_f'), weather.get('weather_wind_mph'),
            weather.get('weather_wind_dir_degrees'), weather.get('weather_wind_out_cf'),
            weather.get('weather_humidity_pct'), weather.get('weather_precip_pct'),
            weather.get('weather_pressure_mb'), weather.get('weather_is_indoor'),
            weather.get('weather_source'), weather.get('weather_station'),
            weather.get('weather_observed_at'),
        ]
        placeholders = ', '.join(['?'] * len(game_cols))
        self.conn.execute(
            f"INSERT OR REPLACE INTO games ({', '.join(game_cols)}) VALUES ({placeholders})",
            game_values,
        )

        # Team hitting
        for side in ('away', 'home'):
            team = record.get(f'{side}_team', '')
            h = record.get(f'{side}_hitting', {})
            if h and team:
                self.conn.execute("""
                    INSERT OR REPLACE INTO team_hitting (date, team, pa, avg, obp, slg, ops, hr, runs, bb, k, sb, hits, ab, doubles, triples, rbi, babip, iso, bb_rate, k_rate, hr_rate) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                """, [
                    date, team,
                    h.get('pa'), h.get('avg'), h.get('obp'), h.get('slg'), h.get('ops'),
                    h.get('hr'), h.get('r'), h.get('bb'), h.get('k'), h.get('sb'),
                    h.get('h'), h.get('ab'), h.get('doubles'), h.get('triples'), h.get('rbi'),
                    h.get('babip'), h.get('iso'), h.get('bb_rate'), h.get('k_rate'), h.get('hr_rate'),
                ])

        # Statcast batting
        for side in ('away', 'home'):
            team = record.get(f'{side}_team', '')
            sc = record.get(f'{side}_statcast_bat', {})
            if sc and team:
                self.conn.execute("""
                    INSERT OR REPLACE INTO statcast_batting (date, team, avg_xwoba, avg_brl_pct, avg_exit_velo, avg_hard_hit, avg_sprint, player_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    date, team,
                    sc.get('avg_xwoba'), sc.get('avg_brl_pct'), sc.get('avg_exit_velo'),
                    sc.get('avg_hard_hit'), sc.get('avg_sprint'), sc.get('player_count'),
                ])

        # Statcast pitching
        for side in ('away', 'home'):
            team = record.get(f'{side}_team', '')
            sc = record.get(f'{side}_statcast_pit', {})
            if sc and team:
                self.conn.execute("""
                    INSERT OR REPLACE INTO statcast_pitching (date, team, avg_xera, avg_brl_against, avg_ev_against, avg_fb_velo, avg_fb_spin, pitcher_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    date, team,
                    sc.get('avg_xera'), sc.get('avg_brl_against'), sc.get('avg_ev_against'),
                    sc.get('avg_fb_velo'), sc.get('avg_fb_spin'), sc.get('pitcher_count'),
                ])

        # Pitcher logs (aggregated season stats)
        for side in ('away', 'home'):
            pid = record.get(f'{side}_pitcher_id')
            starter = record.get(f'{side}_starter')
            if pid and starter:
                self.conn.execute("""
                    INSERT OR REPLACE INTO pitcher_logs (date, pitcher_id, pitcher_name, team, ip, fip, era, k, bb, hr, er, hits, bf, games, avg_pitches, avg_ip) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    date, pid,
                    record.get(f'{side}_pitcher', ''),
                    record.get(f'{side}_team', ''),
                    starter.get('ip'), starter.get('fip'), starter.get('era'),
                    starter.get('k'), starter.get('bb'), starter.get('hr'),
                    starter.get('er', 0), starter.get('h', 0), starter.get('bf', 0),
                    starter.get('games'), starter.get('avg_pitches'), starter.get('avg_ip'),
                ])

        # Pitcher Statcast
        for side in ('away', 'home'):
            pid = record.get(f'{side}_pitcher_id')
            sc = record.get(f'{side}_starter_sc', {})
            if pid and sc:
                self.conn.execute("""
                    INSERT OR REPLACE INTO pitcher_statcast (date, pitcher_id, pitcher_name, team, xwoba_against, xba_against, xslg_against, xera, brl_pct_against, exit_velo_against, hard_hit_against, k_pct, bb_pct, whiff_pct, fb_velocity, fb_spin, curve_spin) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    date, pid,
                    record.get(f'{side}_pitcher', ''),
                    record.get(f'{side}_team', ''),
                    sc.get('xwoba_against'), sc.get('xba_against'), sc.get('xslg_against'),
                    sc.get('xera'), sc.get('brl_pct_against'), sc.get('exit_velo_against'),
                    sc.get('hard_hit_against'), sc.get('k_pct'), sc.get('bb_pct'),
                    sc.get('whiff_pct'), sc.get('fb_velocity'), sc.get('fb_spin'),
                    sc.get('curve_spin'),
                ])

    def ingest_odds(self, date, game_pk, away_team, home_team, bookmaker, market, odds_data):
        """Ingest odds data for a game."""
        self.conn.execute("""
            INSERT OR REPLACE INTO odds (date, game_pk, away_team, home_team, bookmaker, market, away_line, home_line, total_line, over_odds, under_odds, away_ml, home_ml, away_spread, home_spread, away_spread_odds, home_spread_odds, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            date, game_pk, away_team, home_team, bookmaker, market,
            odds_data.get('away_line'), odds_data.get('home_line'),
            odds_data.get('total_line'),
            odds_data.get('over_odds'), odds_data.get('under_odds'),
            odds_data.get('away_ml'), odds_data.get('home_ml'),
            odds_data.get('away_spread'), odds_data.get('home_spread'),
            odds_data.get('away_spread_odds'), odds_data.get('home_spread_odds'),
            json.dumps(odds_data.get('raw', {})),
        ])

    def update_game_weather(self, game_pk, weather):
        """Update weather fields for an existing game."""
        cols = [c for c in GAME_WEATHER_COLUMNS if c != 'game_time_utc']
        assignments = ', '.join([f'{c} = ?' for c in cols])
        values = [weather.get(c) for c in cols]
        values.append(game_pk)
        self.conn.execute(
            f"UPDATE games SET {assignments} WHERE game_pk = ?",
            values,
        )

    def ingest_prediction(self, date, game_pk, away_team, home_team,
                          model_total, odds_total, edge, confidence,
                          away_projected, home_projected, breakdown):
        """Store a model prediction."""
        self.conn.execute("""
            INSERT OR REPLACE INTO predictions (date, game_pk, away_team, home_team, model_total, odds_total, edge, confidence, away_projected, home_projected, breakdown_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            date, game_pk, away_team, home_team,
            model_total, odds_total, edge, confidence,
            away_projected, home_projected,
            json.dumps(breakdown) if breakdown else None,
        ])

    def record_result(self, date, game_pk, model_total, odds_total, actual_total):
        """Record game result and calculate model vs odds performance."""
        model_diff = model_total - actual_total if model_total else None
        odds_diff = odds_total - actual_total if odds_total else None
        model_error = abs(model_diff) if model_diff is not None else None
        odds_error = abs(odds_diff) if odds_diff is not None else None
        over_hit = actual_total > odds_total if odds_total else None

        self.conn.execute("""
            INSERT OR REPLACE INTO results (date, game_pk, model_total, odds_total, actual_total, model_error, odds_error, model_diff, odds_diff, model_hit, odds_hit, over_hit) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            date, game_pk, model_total, odds_total, actual_total,
            model_error, odds_error, model_diff, odds_diff,
            model_error is not None and model_error < odds_error if odds_error else None,
            odds_error is not None and odds_error < model_error if model_error else None,
            over_hit,
        ])

    # ─── Query helpers ───────────────────────────────────────────────────

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
        """Get all completed games with features for model training."""
        return self.df("""
            SELECT
                g.date,
                g.game_pk,
                g.away_team,
                g.home_team,
                g.total_runs,
                g.park_factor,
                g.stadium_roof,
                g.stadium_alt,

                -- Away hitting
                th_away.avg as away_avg, th_away.obp as away_obp,
                th_away.slg as away_slg, th_away.ops as away_ops,
                th_away.hr as away_hr, th_away.bb as away_bb,
                th_away.k as away_k, th_away.babip as away_babip,
                th_away.iso as away_iso, th_away.bb_rate as away_bb_rate,
                th_away.k_rate as away_k_rate, th_away.hr_rate as away_hr_rate,

                -- Home hitting
                th_home.avg as home_avg, th_home.obp as home_obp,
                th_home.slg as home_slg, th_home.ops as home_ops,
                th_home.hr as home_hr, th_home.bb as home_bb,
                th_home.k as home_k, th_home.babip as home_babip,
                th_home.iso as home_iso, th_home.bb_rate as home_bb_rate,
                th_home.k_rate as home_k_rate, th_home.hr_rate as home_hr_rate,

                -- Statcast batting
                sc_bat_away.avg_xwoba as away_team_xwoba,
                sc_bat_away.avg_brl_pct as away_team_brl_pct,
                sc_bat_away.avg_exit_velo as away_team_ev,
                sc_bat_away.avg_hard_hit as away_team_hh,
                sc_bat_home.avg_xwoba as home_team_xwoba,
                sc_bat_home.avg_brl_pct as home_team_brl_pct,
                sc_bat_home.avg_exit_velo as home_team_ev,
                sc_bat_home.avg_hard_hit as home_team_hh,

                -- Statcast pitching (team bullpen)
                sc_pit_away.avg_xera as away_bpen_xera,
                sc_pit_away.avg_brl_against as away_bpen_brl,
                sc_pit_home.avg_xera as home_bpen_xera,
                sc_pit_home.avg_brl_against as home_bpen_brl,

                -- Away starter
                pl_away.fip as away_starter_fip,
                pl_away.era as away_starter_era,
                pl_away.ip as away_starter_ip,
                pl_away.k as away_starter_k,
                pl_away.bb as away_starter_bb,
                pl_away.hr as away_starter_hr,
                pl_away.avg_pitches as away_starter_pitches,

                -- Home starter
                pl_home.fip as home_starter_fip,
                pl_home.era as home_starter_era,
                pl_home.ip as home_starter_ip,
                pl_home.k as home_starter_k,
                pl_home.bb as home_starter_bb,
                pl_home.hr as home_starter_hr,
                pl_home.avg_pitches as home_starter_pitches,

                -- Starter Statcast
                ps_away.xera as away_starter_xera,
                ps_away.brl_pct_against as away_starter_brl,
                ps_away.fb_velocity as away_starter_fb_velo,
                ps_away.fb_spin as away_starter_fb_spin,
                ps_away.k_pct as away_starter_k_pct,
                ps_away.bb_pct as away_starter_bb_pct,

                ps_home.xera as home_starter_xera,
                ps_home.brl_pct_against as home_starter_brl,
                ps_home.fb_velocity as home_starter_fb_velo,
                ps_home.fb_spin as home_starter_fb_spin,
                ps_home.k_pct as home_starter_k_pct,
                ps_home.bb_pct as home_starter_bb_pct

            FROM games g
            LEFT JOIN team_hitting th_away ON g.date = th_away.date AND g.away_team = th_away.team
            LEFT JOIN team_hitting th_home ON g.date = th_home.date AND g.home_team = th_home.team
            LEFT JOIN statcast_batting sc_bat_away ON g.date = sc_bat_away.date AND g.away_team = sc_bat_away.team
            LEFT JOIN statcast_batting sc_bat_home ON g.date = sc_bat_home.date AND g.home_team = sc_bat_home.team
            LEFT JOIN statcast_pitching sc_pit_away ON g.date = sc_pit_away.date AND g.away_team = sc_pit_away.team
            LEFT JOIN statcast_pitching sc_pit_home ON g.date = sc_pit_home.date AND g.home_team = sc_pit_home.team
            LEFT JOIN pitcher_logs pl_away ON g.date = pl_away.date AND g.away_pitcher_id = pl_away.pitcher_id
            LEFT JOIN pitcher_logs pl_home ON g.date = pl_home.date AND g.home_pitcher_id = pl_home.pitcher_id
            LEFT JOIN pitcher_statcast ps_away ON g.date = ps_away.date AND g.away_pitcher_id = ps_away.pitcher_id
            LEFT JOIN pitcher_statcast ps_home ON g.date = ps_home.date AND g.home_pitcher_id = ps_home.pitcher_id
            WHERE g.total_runs IS NOT NULL
            ORDER BY g.date, g.game_pk
        """)

    def get_games_for_date(self, date_str):
        """Get all games for a specific date with full features."""
        return self.df("""
            SELECT
                g.*,
                th_away.avg as away_avg, th_away.ops as away_ops,
                th_away.babip as away_babip, th_away.iso as away_iso,
                th_away.hr_rate as away_hr_rate,
                th_home.avg as home_avg, th_home.ops as home_ops,
                th_home.babip as home_babip, th_home.iso as home_iso,
                th_home.hr_rate as home_hr_rate,
                sc_bat_away.avg_xwoba as away_team_xwoba,
                sc_bat_away.avg_brl_pct as away_team_brl,
                sc_bat_home.avg_xwoba as home_team_xwoba,
                sc_bat_home.avg_brl_pct as home_team_brl,
                pl_away.fip as away_starter_fip,
                pl_away.era as away_starter_era,
                pl_home.fip as home_starter_fip,
                pl_home.era as home_starter_era,
                ps_away.xera as away_starter_xera,
                ps_away.fb_velocity as away_starter_fb_velo,
                ps_home.xera as home_starter_xera,
                ps_home.fb_velocity as home_starter_fb_velo
            FROM games g
            LEFT JOIN team_hitting th_away ON g.date = th_away.date AND g.away_team = th_away.team
            LEFT JOIN team_hitting th_home ON g.date = th_home.date AND g.home_team = th_home.team
            LEFT JOIN statcast_batting sc_bat_away ON g.date = sc_bat_away.date AND g.away_team = sc_bat_away.team
            LEFT JOIN statcast_batting sc_bat_home ON g.date = sc_bat_home.date AND g.home_team = sc_bat_home.team
            LEFT JOIN pitcher_logs pl_away ON g.date = pl_away.date AND g.away_pitcher_id = pl_away.pitcher_id
            LEFT JOIN pitcher_logs pl_home ON g.date = pl_home.date AND g.home_pitcher_id = pl_home.pitcher_id
            LEFT JOIN pitcher_statcast ps_away ON g.date = ps_away.date AND g.away_pitcher_id = ps_away.pitcher_id
            LEFT JOIN pitcher_statcast ps_home ON g.date = ps_home.date AND g.home_pitcher_id = ps_home.pitcher_id
            WHERE g.date = ?
            ORDER BY g.game_pk
        """, [date_str])

    def get_backtest_results(self, start_date=None, end_date=None):
        """Get prediction results for backtesting analysis."""
        where = "WHERE 1=1"
        params = []
        if start_date:
            where += " AND r.date >= ?"
            params.append(start_date)
        if end_date:
            where += " AND r.date <= ?"
            params.append(end_date)

        return self.df(f"""
            SELECT
                r.date,
                r.game_pk,
                g.away_team,
                g.home_team,
                r.model_total,
                r.odds_total,
                r.actual_total,
                r.model_error,
                r.odds_error,
                r.model_diff,
                r.odds_diff,
                r.over_hit,
                p.edge,
                p.confidence
            FROM results r
            JOIN games g ON r.game_pk = g.game_pk
            LEFT JOIN predictions p ON r.game_pk = p.game_pk AND r.date = p.date
            {where}
            ORDER BY r.date, r.game_pk
        """, params)

    def get_stats_summary(self):
        """Get a summary of what's in the database."""
        tables = {}
        for t in ['games', 'team_hitting', 'statcast_batting', 'statcast_pitching',
                   'pitcher_logs', 'pitcher_statcast', 'odds', 'predictions', 'results']:
            count = self.query(f"SELECT COUNT(*) as n FROM {t}")[0]['n']
            tables[t] = count

        date_range = self.query("SELECT MIN(date) as min_date, MAX(date) as max_date FROM games")[0]

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


# ─── CLI: import existing JSON data ──────────────────────────────────────

def import_json_dataset(db, json_path):
    """Import a scraped JSON dataset into the database."""
    with open(json_path) as f:
        records = json.load(f)

    print(f'Importing {len(records)} records from {json_path.name}...')
    for i, record in enumerate(records):
        db.ingest_game(record)
        if (i + 1) % 10 == 0:
            print(f'  {i + 1}/{len(records)}')

    print(f'✅ Imported {len(records)} games')
    return len(records)


if __name__ == '__main__':
    import sys

    db = Database()

    if len(sys.argv) > 1 and sys.argv[1] == 'import':
        # Import all JSON datasets
        data_dir = SCRIPT_DIR / 'data'
        for json_file in sorted(data_dir.glob('games_*.json')) + sorted(data_dir.glob('dataset_*.json')):
            import_json_dataset(db, json_file)
    elif len(sys.argv) > 1 and sys.argv[1] == 'stats':
        summary = db.get_stats_summary()
        print(f'\n📊 Database: {db.db_path}')
        print(f'   Date range: {summary["date_range"]["min_date"]} to {summary["date_range"]["max_date"]}')
        for table, count in summary['tables'].items():
            print(f'   {table}: {count} rows')
    else:
        print('Usage: python db.py [import|stats]')

    db.close()
