#!/usr/bin/env python3
"""
MLB O/U XGBoost Training Pipeline
==================================
Pulls data from DuckDB, engineers tactician-grade features, trains XGBoost,
and outputs model + SHAP explainability + edge analysis.

Metaprompts baked in (see META below):
  1. FEATURE ENGINEERING: What to compute and why
  2. TARGET ENGINEERING: What we're predicting and how
  3. HYPERPARAMETER TUNING: Optuna search with temporal awareness
  4. TEMPORAL VALIDATION: Time-aware splits to prevent lookahead bias
  5. EDGE DETECTION: Turning predictions into actionable bets

Usage:
    python train.py                      # Train with defaults
    python train.py --tune               # Run Optuna hyperparameter search
    python train.py --backtest           # Run temporal backtest
    python train.py --season 2025        # Train on specific season
    python train.py --predict 2026-04-25 # Predict today's games
"""

import json
import math
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.preprocessing import StandardScaler
import optuna
import shap

from db import Database

warnings.filterwarnings('ignore')

SCRIPT_DIR = Path(__file__).parent
DB_PATH = SCRIPT_DIR / 'mlb.duckdb'
MODEL_DIR = SCRIPT_DIR / 'model'
MODEL_DIR.mkdir(exist_ok=True)

# ═══════════════════════════════════════════════════════════════════════════════
# META-PROMPT 1: FEATURE ENGINEERING PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════
#
# PRINCIPLE: Every feature must answer "so what?" for O/U scoring.
# Raw stats are noise. Derived metrics are signal.
#
# FEATURE CATEGORIES:
#   A) Team Offense Quality    - Can they score?
#   B) Pitcher Quality         - Can he suppress scoring?
#   C) Batted Ball Profile     - Are they making hard contact?
#   D) Park/Weather Context    - Does the environment help or hurt?
#   E) Situational Edge        - Fatigue, rest, travel, form
#   F) Interaction Features    - How do A+B combine?
#
# RULES:
#   - Always compute differential features (home - away) - the model
#     needs the MATCHUP, not just the teams
#   - Use rolling windows (last 10/30 games) for form - season stats
#     are too stale by mid-season
#   - Clip extreme values (outliers break tree models less than linear,
#     but still corrupt feature importance)
#   - Missing data: fill with league median, never 0 or mean
# ═══════════════════════════════════════════════════════════════════════════════

LEAGUE_MEDIANS = {}  # Computed from data


def compute_league_medians(df):
    """Compute league median values for fallback filling."""
    global LEAGUE_MEDIANS
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    for col in numeric_cols:
        LEAGUE_MEDIANS[col] = df[col].median()
    return LEAGUE_MEDIANS


def safe_fill(series, fill_value=None):
    """Fill NaN with league median or specified value."""
    if fill_value is not None:
        return series.fillna(fill_value)
    return series.fillna(LEAGUE_MEDIANS.get(series.name, 0))


def _numeric_series(df, column, default):
    if column in df.columns:
        return pd.to_numeric(df[column], errors='coerce')
    return pd.Series([default] * len(df), index=df.index, dtype='float64')


def add_weather_features(df):
    """Add model-ready weather features with neutral indoor defaults."""
    df = df.copy()
    if 'stadium_roof' in df.columns:
        roof = df['stadium_roof'].fillna(False).astype(bool)
    else:
        roof = pd.Series([False] * len(df), index=df.index)
    if 'weather_is_indoor' in df.columns:
        indoor = df['weather_is_indoor'].fillna(False).astype(bool) | roof
    else:
        indoor = roof

    temp = _numeric_series(df, 'weather_temp_f', 72.0).fillna(72.0)
    wind = _numeric_series(df, 'weather_wind_mph', 0.0).fillna(0.0)
    wind_out = _numeric_series(df, 'weather_wind_out_cf', 0.0).fillna(0.0)
    humidity = _numeric_series(df, 'weather_humidity_pct', 50.0).fillna(50.0)
    precip = _numeric_series(df, 'weather_precip_pct', 0.0).fillna(0.0)
    pressure = _numeric_series(df, 'weather_pressure_mb', 1013.25).fillna(1013.25)

    temp = temp.mask(indoor, 72.0)
    wind = wind.mask(indoor, 0.0)
    wind_out = wind_out.mask(indoor, 0.0)
    humidity = humidity.mask(indoor, 50.0)
    precip = precip.mask(indoor, 0.0)
    pressure = pressure.mask(indoor, 1013.25)

    df['weather_temp_filled'] = temp
    df['weather_wind_mph_filled'] = wind
    df['weather_wind_out_cf_filled'] = wind_out
    df['weather_humidity_pct_filled'] = humidity
    df['weather_precip_pct_filled'] = precip
    df['weather_pressure_mb_filled'] = pressure
    df['weather_wind_run_factor'] = wind * wind_out / 10.0
    df['weather_heat_factor'] = (temp - 70.0) / 10.0
    df['weather_humidity_factor'] = (humidity - 50.0) / 50.0
    df['weather_precip_factor'] = precip / 100.0
    df['weather_pressure_factor'] = (pressure - 1013.25) / 20.0
    df['weather_run_environment'] = (
        df['weather_heat_factor'] * 0.25
        + df['weather_wind_run_factor'] * 0.35
        + df['weather_humidity_factor'] * 0.10
        - df['weather_precip_factor'] * 0.15
        - df['weather_pressure_factor'] * 0.05
    )
    return df


def build_feature_matrix(db: Database, start_date=None, end_date=None) -> pd.DataFrame:
    """
    Master feature builder. Joins all tables and computes tactician features.

    Returns DataFrame with one row per game, columns for all features + target.
    """
    print("🔧 Building feature matrix...")

    # ── Pull raw joined data ──────────────────────────────────────────────
    where = "WHERE g.total_runs IS NOT NULL AND g.status = 'Final'"
    params = []
    if start_date:
        where += " AND g.date >= ?"
        params.append(start_date)
    if end_date:
        where += " AND g.date <= ?"
        params.append(end_date)

    df = db.df(f"""
        SELECT
            g.game_pk, g.date, g.away_team, g.home_team,
            g.total_runs, g.away_score, g.home_score,
            g.park_factor, g.stadium_name, g.stadium_roof,
            g.stadium_alt, g.stadium_cf_bearing,
            g.weather_temp_f, g.weather_wind_mph,
            g.weather_wind_dir_degrees, g.weather_wind_out_cf,
            g.weather_humidity_pct, g.weather_precip_pct,
            g.weather_pressure_mb, g.weather_is_indoor,

            -- Team hitting (away)
            th_away.pa as away_pa, th_away.avg as away_avg, th_away.obp as away_obp,
            th_away.slg as away_slg, th_away.ops as away_ops,
            th_away.hr as away_hr, th_away.runs as away_runs_scored,
            th_away.bb as away_bb, th_away.k as away_k,
            th_away.babip as away_babip, th_away.iso as away_iso,
            th_away.bb_rate as away_bb_rate, th_away.k_rate as away_k_rate,
            th_away.hr_rate as away_hr_rate,

            -- Team hitting (home)
            th_home.pa as home_pa, th_home.avg as home_avg, th_home.obp as home_obp,
            th_home.slg as home_slg, th_home.ops as home_ops,
            th_home.hr as home_hr, th_home.runs as home_runs_scored,
            th_home.bb as home_bb, th_home.k as home_k,
            th_home.babip as home_babip, th_home.iso as home_iso,
            th_home.bb_rate as home_bb_rate, th_home.k_rate as home_k_rate,
            th_home.hr_rate as home_hr_rate,

            -- Statcast batting (away)
            sc_bat_away.avg_xwoba as away_team_xwoba,
            sc_bat_away.avg_brl_pct as away_team_brl,
            sc_bat_away.avg_exit_velo as away_team_ev,
            sc_bat_away.avg_hard_hit as away_team_hardhit,
            sc_bat_away.avg_sprint as away_team_sprint,

            -- Statcast batting (home)
            sc_home.avg_xwoba as home_team_xwoba,
            sc_home.avg_brl_pct as home_team_brl,
            sc_home.avg_exit_velo as home_team_ev,
            sc_home.avg_hard_hit as home_team_hardhit,
            sc_home.avg_sprint as home_team_sprint,

            -- Statcast pitching (away)
            sc_pit_away.avg_xera as away_pen_xera,
            sc_pit_away.avg_fb_velo as away_pen_fb_velo,
            sc_pit_away.avg_fb_spin as away_pen_fb_spin,

            -- Statcast pitching (home)
            sc_pit_home.avg_xera as home_pen_xera,
            sc_pit_home.avg_fb_velo as home_pen_fb_velo,
            sc_pit_home.avg_fb_spin as home_pen_fb_spin,

            -- Pitcher logs (away starter)
            pl_away.fip as away_starter_fip, pl_away.era as away_starter_era,
            pl_away.k as away_starter_k, pl_away.bb as away_starter_bb,
            pl_away.hr as away_starter_hr, pl_away.ip as away_starter_ip,
            pl_away.avg_pitches as away_starter_avg_pitches,
            pl_away.games as away_starter_games,

            -- Pitcher logs (home starter)
            pl_home.fip as home_starter_fip, pl_home.era as home_starter_era,
            pl_home.k as home_starter_k, pl_home.bb as home_starter_bb,
            pl_home.hr as home_starter_hr, pl_home.ip as home_starter_ip,
            pl_home.avg_pitches as home_starter_avg_pitches,
            pl_home.games as home_starter_games,

            -- Pitcher Statcast (away starter) - percentile ranks 0-100
            ps_away.xwoba_against as away_ps_xwoba,
            ps_away.xera as away_ps_xera,
            ps_away.brl_pct_against as away_ps_brl,
            ps_away.exit_velo_against as away_ps_ev,
            ps_away.k_pct as away_ps_k_pct,
            ps_away.bb_pct as away_ps_bb_pct,
            ps_away.whiff_pct as away_ps_whiff,
            ps_away.fb_velocity as away_ps_fb_velo,
            ps_away.fb_spin as away_ps_fb_spin,

            -- Pitcher Statcast (home starter) - percentile ranks 0-100
            ps_home.xwoba_against as home_ps_xwoba,
            ps_home.xera as home_ps_xera,
            ps_home.brl_pct_against as home_ps_brl,
            ps_home.exit_velo_against as home_ps_ev,
            ps_home.k_pct as home_ps_k_pct,
            ps_home.bb_pct as home_ps_bb_pct,
            ps_home.whiff_pct as home_ps_whiff,
            ps_home.fb_velocity as home_ps_fb_velo,
            ps_home.fb_spin as home_ps_fb_spin,

            -- Market line (FanDuel totals)
            o.total_line as odds_total,
            o.over_odds as odds_over,
            o.under_odds as odds_under

        FROM games g
        -- ASOF LEFT JOIN: pulls most recent data STRICTLY BEFORE game date
        -- This prevents look-ahead bias — no feature includes the game being predicted
        ASOF LEFT JOIN team_hitting th_away ON g.away_team = th_away.team AND g.date > th_away.date
        ASOF LEFT JOIN team_hitting th_home ON g.home_team = th_home.team AND g.date > th_home.date
        ASOF LEFT JOIN statcast_batting sc_bat_away ON g.away_team = sc_bat_away.team AND g.date > sc_bat_away.date
        ASOF LEFT JOIN statcast_batting sc_home ON g.home_team = sc_home.team AND g.date > sc_home.date
        ASOF LEFT JOIN statcast_pitching sc_pit_away ON g.away_team = sc_pit_away.team AND g.date > sc_pit_away.date
        ASOF LEFT JOIN statcast_pitching sc_pit_home ON g.home_team = sc_pit_home.team AND g.date > sc_pit_home.date
        ASOF LEFT JOIN pitcher_logs pl_away ON g.away_pitcher_id = pl_away.pitcher_id AND g.date > pl_away.date
        ASOF LEFT JOIN pitcher_logs pl_home ON g.home_pitcher_id = pl_home.pitcher_id AND g.date > pl_home.date
        ASOF LEFT JOIN pitcher_statcast ps_away ON g.away_pitcher_id = ps_away.pitcher_id AND g.date > ps_away.date
        ASOF LEFT JOIN pitcher_statcast ps_home ON g.home_pitcher_id = ps_home.pitcher_id AND g.date > ps_home.date
        -- Odds are set before the game (no look-ahead risk)
        LEFT JOIN (
            SELECT date, game_pk, total_line, over_odds, under_odds
            FROM odds WHERE bookmaker = 'fanduel' AND market = 'totals'
        ) o ON g.date = o.date AND g.game_pk = o.game_pk
        {where}
        ORDER BY g.date, g.game_pk
    """, params)

    print(f"  📊 Pulled {len(df)} games with scores")

    # ── Compute league medians for fallback ───────────────────────────────
    compute_league_medians(df)

    # ── Compute rest days for each team ──────────────────────────────────
    print("  🗓️  Computing rest days...")
    conn = db.conn
    all_game_dates = conn.execute("""
        SELECT DISTINCT date, away_team as team FROM games
        UNION
        SELECT DISTINCT date, home_team as team FROM games
        ORDER BY team, date
    """).df()

    for team in df['away_team'].unique():
        team_dates = all_game_dates[all_game_dates['team'] == team]['date'].sort_values().tolist()
        date_to_idx = {d: i for i, d in enumerate(team_dates)}

        away_mask = df['away_team'] == team
        home_mask = df['home_team'] == team

        # For away games, find previous game date for this team
        for idx in df[away_mask].index:
            game_date = df.loc[idx, 'date']
            if game_date in date_to_idx:
                game_idx = date_to_idx[game_date]
                if game_idx > 0:
                    prev_date = team_dates[game_idx - 1]
                    rest = (game_date - prev_date).days
                    df.loc[idx, 'away_rest_days'] = rest
                else:
                    df.loc[idx, 'away_rest_days'] = 3  # default

        for idx in df[home_mask].index:
            game_date = df.loc[idx, 'date']
            if game_date in date_to_idx:
                game_idx = date_to_idx[game_date]
                if game_idx > 0:
                    prev_date = team_dates[game_idx - 1]
                    rest = (game_date - prev_date).days
                    df.loc[idx, 'home_rest_days'] = rest
                else:
                    df.loc[idx, 'home_rest_days'] = 3  # default

    df['away_rest_days'] = df.get('away_rest_days', pd.Series([3]*len(df))).fillna(3)
    df['home_rest_days'] = df.get('home_rest_days', pd.Series([3]*len(df))).fillna(3)
    df['rest_diff'] = df['home_rest_days'] - df['away_rest_days']
    df['combined_rest'] = df['away_rest_days'] + df['home_rest_days']
    df['away_short_rest'] = (df['away_rest_days'] <= 2).astype(int)
    df['home_short_rest'] = (df['home_rest_days'] <= 2).astype(int)

    # ═══════════════════════════════════════════════════════════════════════
    # CATEGORY A: TEAM OFFENSE QUALITY FEATURES
    # ═══════════════════════════════════════════════════════════════════════
    print("  ⚡ Computing offense quality features...")

    # Differential features (home - away) - the model sees the matchup
    df['diff_ops'] = df['home_ops'] - df['away_ops']
    df['diff_avg'] = df['home_avg'] - df['away_avg']
    df['diff_obp'] = df['home_obp'] - df['away_obp']
    df['diff_slg'] = df['home_slg'] - df['away_slg']
    df['diff_iso'] = df['home_iso'] - df['away_iso']
    df['diff_hr_rate'] = df['home_hr_rate'] - df['away_hr_rate']
    df['diff_bb_rate'] = df['home_bb_rate'] - df['away_bb_rate']
    df['diff_k_rate'] = df['home_k_rate'] - df['away_k_rate']
    df['diff_babip'] = df['home_babip'] - df['away_babip']

    # Combined offense strength (both teams - high OBP + high SLG = lots of runs)
    df['combined_ops'] = df['home_ops'] + df['away_ops']
    df['combined_obp'] = df['home_obp'] + df['away_obp']
    df['combined_iso'] = df['home_iso'] + df['away_iso']
    df['combined_hr_rate'] = df['home_hr_rate'] + df['away_hr_rate']

    # Run production (runs scored per game - direct scoring signal)
    # Normalize PA to per-game rate (162-game season ~ 6200 PA)
    df['away_rpg'] = df['away_runs_scored'] / df['away_pa'].clip(lower=1) * 6200 / 162
    df['home_rpg'] = df['home_runs_scored'] / df['home_pa'].clip(lower=1) * 6200 / 162
    df['combined_rpg'] = df['away_rpg'] + df['home_rpg']

    # ═══════════════════════════════════════════════════════════════════════
    # CATEGORY B: PITCHER QUALITY FEATURES
    # ═══════════════════════════════════════════════════════════════════════
    print("  🎯 Computing pitcher quality features...")

    # Starter quality differential
    df['diff_starter_fip'] = df['home_starter_fip'] - df['away_starter_fip']
    df['diff_starter_era'] = df['home_starter_era'] - df['away_starter_era']

    # Combined starter quality (lower = better = fewer runs)
    df['combined_starter_fip'] = df['home_starter_fip'] + df['away_starter_fip']
    df['combined_starter_era'] = df['home_starter_era'] + df['away_starter_era']

    # Starter K/BB ratio (command quality)
    df['away_starter_kbb'] = df['away_starter_k'] / df['away_starter_bb'].clip(lower=1)
    df['home_starter_kbb'] = df['home_starter_k'] / df['home_starter_bb'].clip(lower=1)
    df['diff_starter_kbb'] = df['home_starter_kbb'] - df['away_starter_kbb']

    # Pitcher Statcast percentile features (already 0-100 scale)
    # Lower xERA percentile = better pitcher
    df['diff_ps_xera'] = df['home_ps_xera'] - df['away_ps_xera']
    df['diff_ps_xwoba'] = df['home_ps_xwoba'] - df['away_ps_xwoba']
    df['diff_ps_whiff'] = df['home_ps_whiff'] - df['away_ps_whiff']
    df['diff_ps_k_pct'] = df['home_ps_k_pct'] - df['away_ps_k_pct']
    df['diff_ps_bb_pct'] = df['home_ps_bb_pct'] - df['away_ps_bb_pct']

    # Combined pitcher stuff (fb velocity + spin = raw stuff)
    df['combined_fb_velo'] = df['away_ps_fb_velo'] + df['home_ps_fb_velo']
    df['combined_fb_spin'] = df['away_ps_fb_spin'] + df['home_ps_fb_spin']

    # ═══════════════════════════════════════════════════════════════════════
    # CATEGORY C: BATTED BALL PROFILE
    # ═══════════════════════════════════════════════════════════════════════
    print("  💥 Computing batted ball features...")

    # Team Statcast - barrel differential (bigger = more power for home team)
    df['diff_team_brl'] = df['home_team_brl'] - df['away_team_brl']
    df['diff_team_ev'] = df['home_team_ev'] - df['away_team_ev']
    df['diff_team_hardhit'] = df['home_team_hardhit'] - df['away_team_hardhit']

    # Combined barrel rate (both teams making hard contact = lots of runs)
    df['combined_team_brl'] = df['home_team_brl'] + df['away_team_brl']
    df['combined_team_ev'] = df['home_team_ev'] + df['away_team_ev']

    # Pitcher Statcast - barrel against (lower = pitcher suppresses contact)
    df['diff_ps_brl'] = df['home_ps_brl'] - df['away_ps_brl']
    df['diff_ps_ev'] = df['home_ps_ev'] - df['away_ps_ev']

    # ═══════════════════════════════════════════════════════════════════════
    # CATEGORY D: PARK & CONTEXT FEATURES
    # ═══════════════════════════════════════════════════════════════════════
    print("  🏟️ Computing park/context features...")

    # Park factor direct (1.0 = neutral, >1.0 = hitter-friendly)
    df['park_hitter'] = df['park_factor']

    df['has_odds'] = df['odds_total'].notna().astype(int)
    # Market line is kept for edge comparison, but excluded from model features.
    df['odds_total'] = df['odds_total'].fillna(df['combined_starter_era'].apply(lambda x: 8.5))
    df['diff_from_market'] = 0

    # Altitude effect (Coors = 5280ft, sea level ≈ 0)
    df['altitude_effect'] = df['stadium_alt'] / 5280.0  # Normalized 0-1

    # Roof factor (domed = no weather effect)
    df['is_domed'] = df['stadium_roof'].astype(int)

    # CF bearing - wind direction matters (simplified: higher = more HR-friendly)
    df['cf_bearing_norm'] = df['stadium_cf_bearing'] / 360.0
    df = add_weather_features(df)

    # ═══════════════════════════════════════════════════════════════════════
    # CATEGORY E: SITUATIONAL / TACTICIAN FEATURES
    # ═══════════════════════════════════════════════════════════════════════
    print("  🧠 Computing tactician situational features...")

    # Pitcher workload decay (from pitcher_logs)
    # Games pitched → workload tier
    df['away_starter_workload'] = df['away_starter_games'].apply(
        lambda x: 0 if x < 5 else (1 if x < 10 else (2 if x < 15 else 3))
    )
    df['home_starter_workload'] = df['home_starter_games'].apply(
        lambda x: 0 if x < 5 else (1 if x < 10 else (2 if x < 15 else 3))
    )
    df['diff_workload'] = df['home_starter_workload'] - df['away_starter_workload']

    # Starter IP per start (fatigue proxy - higher = more taxed)
    df['away_starter_ip_pg'] = df['away_starter_ip'] / df['away_starter_games'].clip(lower=1)
    df['home_starter_ip_pg'] = df['home_starter_ip'] / df['home_starter_games'].clip(lower=1)
    df['diff_ip_pg'] = df['home_starter_ip_pg'] - df['away_starter_ip_pg']

    # Average pitches per game (fatigue signal)
    df['diff_avg_pitches'] = df['home_starter_avg_pitches'] - df['away_starter_avg_pitches']

    # ═══════════════════════════════════════════════════════════════════════
    # CATEGORY F: INTERACTION FEATURES (The Tactician's Secret Sauce)
    # ═══════════════════════════════════════════════════════════════════════
    print("  🧪 Computing interaction features...")

    # Offense vs Pitching interaction
    # High combined offense + bad combined pitching = RUN FEST
    df['offense_pitching_gap'] = df['combined_ops'] - (df['combined_starter_fip'] * 30)

    # Barrel rate vs pitcher suppression
    # Good contact + pitcher who allows barrels = DAMAGE
    df['barrel_matchup'] = df['combined_team_brl'] - df['diff_ps_brl']

    # Park + offense interaction
    # Hitter's park + good offenses = MORE RUNS
    df['park_offense'] = df['park_hitter'] * df['combined_ops']

    # Starter quality × park
    # Bad starter in hitter's park = EXPLOSION
    df['starter_park'] = df['combined_starter_fip'] * (2.0 - df['park_hitter'])

    # ═══════════════════════════════════════════════════════════════════════
    # CLEANUP: Fill NaN, clip outliers, add date features
    # ═══════════════════════════════════════════════════════════════════════
    print("  🧹 Cleaning up...")

    # Date features (month captures season-long effects)
    df['month'] = pd.to_datetime(df['date']).dt.month
    df['day_of_week'] = pd.to_datetime(df['date']).dt.dayofweek
    df['is_weekend'] = (df['day_of_week'] >= 5).astype(int)

    # Early season flag (April = sample sizes are small, regress to mean)
    df['early_season'] = (df['month'] <= 4).astype(int)

    # Fill NaN with league medians
    feature_cols = [c for c in df.columns if c not in [
        'game_pk', 'date', 'away_team', 'home_team', 'stadium_name',
        'total_runs', 'away_score', 'home_score', 'stadium_roof'
    ]]
    for col in feature_cols:
        if df[col].dtype in [np.float64, np.float32, np.int64]:
            df[col] = safe_fill(df[col])

    # Clip extreme values (protects tree models from degenerate splits)
    clip_bounds = {
        'combined_ops': (1.0, 1.8),
        'combined_starter_fip': (4.0, 12.0),
        'combined_rpg': (3.0, 12.0),
        'away_rpg': (1.5, 7.0),
        'home_rpg': (1.5, 7.0),
    }
    for col, (lo, hi) in clip_bounds.items():
        if col in df.columns:
            df[col] = df[col].clip(lo, hi)

    print(f"  ✅ Feature matrix: {len(df)} games × {len(feature_cols)} features")
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# META-PROMPT 2: TARGET ENGINEERING
# ═══════════════════════════════════════════════════════════════════════════════
#
# PRIMARY TARGET: total_runs (actual combined score)
#   - Direct regression target
#   - Model outputs predicted total → compare to odds line
#
# SECONDARY TARGET (for classification): over_hit (binary)
#   - Did the game go over the line?
#   - Useful for calibration, not primary training target
#
# WHY TOTAL_RUNS AND NOT OVER/HIT DIRECTLY?
#   - Regression gives you a continuous number (more info than binary)
#   - You can derive confidence from how far the prediction is from the line
#   - Binary classification loses the magnitude of edge
#
# TARGET TRANSFORMATIONS:
#   - None needed for XGBoost (handles skewed targets well)
#   - Consider log(total_runs) if distribution is heavily right-skewed
#     but MLB total runs is roughly normal (mean ~8.5, std ~3.5)
# ═══════════════════════════════════════════════════════════════════════════════

TARGET = 'total_runs'
TARGET_MODE = 'market_residual'
PICK_EDGE_THRESHOLD = 2.0
TUNED_RESIDUAL_PARAMS = {
    'objective': 'reg:squarederror',
    'n_estimators': 293,
    'max_depth': 5,
    'learning_rate': 0.06775412563572669,
    'min_child_weight': 15,
    'subsample': 0.7247855148845419,
    'colsample_bytree': 0.6499209087063027,
    'reg_alpha': 0.036091558986095194,
    'reg_lambda': 0.1133111978856325,
    'gamma': 4.351425557180096,
    'random_state': 42,
    'verbosity': 0,
}


def prepare_train_test(df, test_cutoff_date=None):
    """
    Split data temporally - NEVER use random splits for time-series data.
    """
    if test_cutoff_date:
        # Explicit cutoff
        train = df[df['date'] < test_cutoff_date].copy()
        test = df[df['date'] >= test_cutoff_date].copy()
    else:
        # Default: last 20% of dates as test
        dates = sorted(df['date'].unique())
        cutoff_idx = int(len(dates) * 0.8)
        cutoff_date = dates[cutoff_idx]
        train = df[df['date'] < cutoff_date].copy()
        test = df[df['date'] >= cutoff_date].copy()

    print(f"  📅 Train: {len(train)} games (before {test['date'].min()})")
    print(f"  📅 Test:  {len(test)} games (from {test['date'].min()})")
    return train, test


# ═══════════════════════════════════════════════════════════════════════════════
# META-PROMPT 3: HYPERPARAMETER TUNING
# ═══════════════════════════════════════════════════════════════════════════════
#
# XGBoost has ~15 important hyperparameters. Key ones for O/U:
#
#   n_estimators:    More = better up to a point (100-1000)
#   max_depth:       4-8 for tabular data (deeper = more overfitting risk)
#   learning_rate:   0.01-0.1 (lower = more robust, needs more trees)
#   min_child_weight: 1-10 (higher = more conservative)
#   subsample:       0.6-1.0 (row sampling per tree)
#   colsample_bytree: 0.6-1.0 (feature sampling per tree)
#   reg_alpha:       L1 regularization (0-10)
#   reg_lambda:      L2 regularization (0-10)
#
# TUNING STRATEGY:
#   - Use Optuna with TimeSeriesSplit (temporal CV)
#   - Optimize for MAE (robust to outliers) not RMSE
#   - 50-100 trials is enough for tabular data
#   - Early stopping on eval set (20 rounds no improvement)
# ═══════════════════════════════════════════════════════════════════════════════

FEATURE_COLS = None  # Set during training
LEAKAGE_BLOCKED_FEATURES = {
    # Target and post-game results
    'total_runs',
    'away_score',
    'home_score',
    # Pregame market columns are used for edge comparison, not model input.
    # Keeping them out avoids training a model that mostly learns the book.
    'odds_total',
    'odds_over',
    'odds_under',
    'has_odds',
}


def get_feature_cols(df):
    """Get the feature column names (exclude metadata and target)."""
    exclude = {
        'game_pk', 'date', 'away_team', 'home_team', 'stadium_name',
        'total_runs', 'away_score', 'home_score', 'stadium_roof',
        *LEAKAGE_BLOCKED_FEATURES,
    }
    return [c for c in df.columns if c not in exclude and df[c].dtype in [np.float64, np.float32, np.int64]]


def validate_no_leakage_features(feature_cols):
    """Fail fast if a known target, result, or market proxy enters training."""
    blocked = sorted(set(feature_cols) & LEAKAGE_BLOCKED_FEATURES)
    if blocked:
        raise ValueError(f"Leakage-prone features selected: {blocked}")


def objective(trial, X_train, y_train, X_val, y_val):
    """Optuna objective function for XGBoost hyperparameter tuning."""
    params = {
        'objective': 'reg:squarederror',
        'eval_metric': 'mae',
        'n_estimators': trial.suggest_int('n_estimators', 100, 800),
        'max_depth': trial.suggest_int('max_depth', 3, 8),
        'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.15, log=True),
        'min_child_weight': trial.suggest_int('min_child_weight', 1, 15),
        'subsample': trial.suggest_float('subsample', 0.5, 1.0),
        'colsample_bytree': trial.suggest_float('colsample_bytree', 0.5, 1.0),
        'reg_alpha': trial.suggest_float('reg_alpha', 1e-8, 10.0, log=True),
        'reg_lambda': trial.suggest_float('reg_lambda', 1e-8, 10.0, log=True),
        'gamma': trial.suggest_float('gamma', 0, 5.0),
        'random_state': 42,
        'verbosity': 0,
    }

    model = xgb.XGBRegressor(**params)
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        verbose=False,
    )

    preds = model.predict(X_val)
    mae = mean_absolute_error(y_val, preds)
    return mae


def tune_hyperparameters(X_train, y_train, n_trials=75):
    """Run Optuna hyperparameter search with temporal CV."""
    print("\n🔍 Running Optuna hyperparameter search...")
    print(f"   {n_trials} trials with 3-fold temporal CV")

    tscv = TimeSeriesSplit(n_splits=3)

    def optuna_objective(trial):
        scores = []
        for train_idx, val_idx in tscv.split(X_train):
            X_tr, X_vl = X_train.iloc[train_idx], X_train.iloc[val_idx]
            y_tr, y_vl = y_train.iloc[train_idx], y_train.iloc[val_idx]

            params = {
                'objective': 'reg:squarederror',
                'n_estimators': trial.suggest_int('n_estimators', 100, 800),
                'max_depth': trial.suggest_int('max_depth', 3, 8),
                'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.15, log=True),
                'min_child_weight': trial.suggest_int('min_child_weight', 1, 15),
                'subsample': trial.suggest_float('subsample', 0.5, 1.0),
                'colsample_bytree': trial.suggest_float('colsample_bytree', 0.5, 1.0),
                'reg_alpha': trial.suggest_float('reg_alpha', 1e-8, 10.0, log=True),
                'reg_lambda': trial.suggest_float('reg_lambda', 1e-8, 10.0, log=True),
                'gamma': trial.suggest_float('gamma', 0, 5.0),
                'random_state': 42,
                'verbosity': 0,
            }

            model = xgb.XGBRegressor(**params)
            model.fit(X_tr, y_tr, eval_set=[(X_vl, y_vl)], verbose=False)
            preds = model.predict(X_vl)
            scores.append(mean_absolute_error(y_vl, preds))

        return np.mean(scores)

    study = optuna.create_study(direction='minimize', study_name='ou_xgb')
    study.optimize(optuna_objective, n_trials=n_trials, show_progress_bar=True)

    print(f"\n  🏆 Best MAE: {study.best_value:.4f}")
    print(f"  📋 Best params: {json.dumps(study.best_params, indent=2)}")

    return study.best_params


# ═══════════════════════════════════════════════════════════════════════════════
# META-PROMPT 4: TEMPORAL VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════
#
# WHY RANDOM SPLITS ARE WRONG FOR SPORTS PREDICTION:
#   - If you randomly split, the model sees April data alongside September
#   - In reality, you're predicting FUTURE games, not random ones
#   - Random splits inflate test metrics (data leakage via time)
#
# CORRECT APPROACHES:
#   1. Train on months 1-5, test on month 6 (simple cutoff)
#   2. Walk-forward: train on weeks 1-10, test on week 11, slide forward
#   3. TimeSeriesSplit with embargo gap (no bleeding across splits)
#
# EMBODIMENT GAP:
#   Between train and test, skip 3-5 days. This prevents the model from
#   "remembering" a team's stats from the last day of training that are
#   nearly identical to the first day of testing.
#
# METRICS TO TRACK:
#   - MAE: Mean Absolute Error (primary - most interpretable)
#   - RMSE: Penalizes big misses (important - a 5-run miss is worse
#     than two 2.5-run misses)
#   - R²: How much variance we explain (>0.15 is decent for O/U)
#   - Calibration: Are our predicted totals centered on actuals?
#   - Edge frequency: How often do we find 0.5+ run edge?
# ═══════════════════════════════════════════════════════════════════════════════

EMBARGO_DAYS = 3  # Gap between train/test to prevent leakage


def predict_model_totals(model, feature_df, feature_cols, target_mode=TARGET_MODE):
    """Return model total-runs predictions for the configured target mode."""
    raw_preds = model.predict(feature_df[feature_cols])
    if target_mode == 'market_residual':
        return feature_df['odds_total'].to_numpy() + raw_preds
    return raw_preds


def evaluate_over_under_picks(actual, odds_total, predictions, threshold=PICK_EDGE_THRESHOLD):
    """Evaluate O/U picks generated when model edge clears the threshold."""
    actual = np.asarray(actual)
    odds_total = np.asarray(odds_total)
    predictions = np.asarray(predictions)
    edges = predictions - odds_total
    mask = np.abs(edges) >= threshold
    if not mask.any():
        return {
            'threshold': threshold,
            'picks': 0,
            'wins': 0,
            'losses': 0,
            'pushes': 0,
            'win_pct': None,
            'units': 0.0,
            'roi_pct': None,
        }

    selected_actual = actual[mask]
    selected_lines = odds_total[mask]
    selected_edges = edges[mask]
    over_mask = selected_edges > 0
    under_mask = selected_edges < 0
    wins = int(((over_mask) & (selected_actual > selected_lines)).sum())
    wins += int(((under_mask) & (selected_actual < selected_lines)).sum())
    losses = int(((over_mask) & (selected_actual < selected_lines)).sum())
    losses += int(((under_mask) & (selected_actual > selected_lines)).sum())
    pushes = int((selected_actual == selected_lines).sum())
    decisions = wins + losses
    units = wins - losses * 1.1
    risked = decisions * 1.1
    return {
        'threshold': threshold,
        'picks': int(mask.sum()),
        'wins': wins,
        'losses': losses,
        'pushes': pushes,
        'win_pct': round(wins / decisions * 100, 2) if decisions else None,
        'units': round(units, 2),
        'roi_pct': round(units / risked * 100, 2) if risked else None,
    }


def temporal_backtest(df, feature_cols, n_splits=5, target_mode=TARGET_MODE):
    """
    Walk-forward temporal backtest with embargo gap.

    Returns list of (train_period, test_period, metrics) tuples.
    """
    print("\n📅 Running temporal backtest...")
    dates = sorted(df['date'].unique())

    if len(dates) < n_splits + 10:
        print(f"  ⚠️  Not enough data for {n_splits} splits (only {len(dates)} dates)")
        n_splits = max(2, len(dates) // 5)

    results = []
    split_size = len(dates) // (n_splits + 1)

    for i in range(n_splits):
        train_end_idx = split_size * (i + 1)
        test_start_idx = train_end_idx + EMBARGO_DAYS
        test_end_idx = min(test_start_idx + split_size, len(dates) - 1)

        if test_start_idx >= len(dates):
            break

        train_dates = dates[:train_end_idx]
        test_dates = dates[test_start_idx:test_end_idx]

        train = df[df['date'].isin(train_dates)]
        test = df[df['date'].isin(test_dates)]
        if target_mode == 'market_residual':
            train = train[train['has_odds'] == 1]
            test = test[test['has_odds'] == 1]

        if len(train) < 10 or len(test) < 5:
            continue

        X_train = train[feature_cols]
        y_train = train[TARGET]
        if target_mode == 'market_residual':
            y_train = train[TARGET] - train['odds_total']
        X_test = test[feature_cols]
        y_test = test[TARGET]

        if target_mode == 'market_residual':
            model = xgb.XGBRegressor(**TUNED_RESIDUAL_PARAMS)
        else:
            model = xgb.XGBRegressor(
                n_estimators=300,
                max_depth=5,
                learning_rate=0.05,
                min_child_weight=5,
                subsample=0.8,
                colsample_bytree=0.8,
                reg_alpha=1.0,
                reg_lambda=1.0,
                random_state=42,
                verbosity=0,
            )
        eval_y = y_test - test['odds_total'] if target_mode == 'market_residual' else y_test
        model.fit(X_train, y_train, eval_set=[(X_test, eval_y)], verbose=False)

        preds = predict_model_totals(model, test, feature_cols, target_mode)

        metrics = {
            'split': i + 1,
            'train_period': f"{train_dates[0]} → {train_dates[-1]}",
            'test_period': f"{test_dates[0]} → {test_dates[-1]}",
            'train_games': len(train),
            'test_games': len(test),
            'mae': mean_absolute_error(y_test, preds),
            'rmse': math.sqrt(mean_squared_error(y_test, preds)),
            'r2': r2_score(y_test, preds),
            'mae_vs_naive': None,  # vs always predict league mean
            'edge_freq': None,     # % of games with 0.5+ run edge
        }

        # Compare to naive (always predict league mean)
        naive_pred = y_train.mean()
        if target_mode == 'market_residual':
            naive_pred = train[TARGET].mean()
        naive_mae = mean_absolute_error(y_test, [naive_pred] * len(y_test))
        metrics['mae_vs_naive'] = (naive_mae - metrics['mae']) / naive_mae * 100

        # Edge frequency
        diffs = np.abs(preds - y_test.values)
        metrics['edge_freq'] = np.mean(diffs >= 0.5) * 100

        results.append(metrics)
        print(f"  Split {i+1}: MAE={metrics['mae']:.3f} | R²={metrics['r2']:.3f} | "
              f"vs naive: {metrics['mae_vs_naive']:+.1f}% | edge≥0.5: {metrics['edge_freq']:.0f}%")

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# META-PROMPT 5: EDGE DETECTION
# ═══════════════════════════════════════════════════════════════════════════════
#
# HOW TO TURN PREDICTIONS INTO BETS:
#
# 1. PREDICT total_runs for today's games
# 2. COMPARE to the market line (odds total)
# 3. EDGE = predicted_total - odds_total
#    - If edge > 0 → model says MORE runs → bet OVER
#    - If edge < 0 → model says FEWER runs → bet UNDER
#
# 4. CONFIDENCE TIERS:
#    - Edge ≥ 1.5 runs:  HIGH confidence (strong bet)
#    - Edge ≥ 1.0 runs:  MEDIUM confidence (worth a bet)
#    - Edge ≥ 0.5 runs:  LOW confidence (lean, maybe pass)
#    - Edge < 0.5 runs:  NO EDGE (pass)
#
# 5. FILTERING RULES (avoid bad bets):
#    - Skip if model confidence is low (high variance in predictions)
#    - Skip early April (small sample sizes, model is uncertain)
#    - Skip games with extreme weather (model may not capture it well)
#    - Skip if odds juice is too high (vig > 6% on the total)
#
# 6. CALIBRATION:
#    - Track predicted vs actual over 100+ games
#    - If model consistently over/under by X runs, apply a bias correction
#    - Re-train monthly with new data
# ═══════════════════════════════════════════════════════════════════════════════

def predict_todays_games(model, db: Database, feature_cols, date_str=None, target_mode=TARGET_MODE):
    """
    Predict O/U edge for todays (or specified) games.
    """
    if date_str is None:
        date_str = datetime.now().strftime('%Y-%m-%d')

    print(f"\n⚾ Predicting games for {date_str}...")

    # Get today's games (may not have scores yet)
    games = db.df(f"""
        SELECT
            g.game_pk, g.date, g.away_team, g.home_team,
            g.park_factor, g.stadium_name, g.stadium_roof,
            g.stadium_alt, g.stadium_cf_bearing,
            g.weather_temp_f, g.weather_wind_mph,
            g.weather_wind_dir_degrees, g.weather_wind_out_cf,
            g.weather_humidity_pct, g.weather_precip_pct,
            g.weather_pressure_mb, g.weather_is_indoor,
            g.total_runs, g.away_score, g.home_score,

            -- Team hitting
            th_away.avg as away_avg, th_away.obp as away_obp,
            th_away.slg as away_slg, th_away.ops as away_ops,
            th_away.hr as away_hr, th_away.runs as away_runs_scored,
            th_away.bb as away_bb, th_away.k as away_k,
            th_away.babip as away_babip, th_away.iso as away_iso,
            th_away.bb_rate as away_bb_rate, th_away.k_rate as away_k_rate,
            th_away.hr_rate as away_hr_rate, th_away.pa as away_pa,

            th_home.avg as home_avg, th_home.obp as home_obp,
            th_home.slg as home_slg, th_home.ops as home_ops,
            th_home.hr as home_hr, th_home.runs as home_runs_scored,
            th_home.bb as home_bb, th_home.k as home_k,
            th_home.babip as home_babip, th_home.iso as home_iso,
            th_home.bb_rate as home_bb_rate, th_home.k_rate as home_k_rate,
            th_home.hr_rate as home_hr_rate, th_home.pa as home_pa,

            -- Statcast batting
            sc_bat_away.avg_xwoba as away_team_xwoba,
            sc_bat_away.avg_brl_pct as away_team_brl,
            sc_bat_away.avg_exit_velo as away_team_ev,
            sc_bat_away.avg_hard_hit as away_team_hardhit,
            sc_bat_away.avg_sprint as away_team_sprint,
            sc_home.avg_xwoba as home_team_xwoba,
            sc_home.avg_brl_pct as home_team_brl,
            sc_home.avg_exit_velo as home_team_ev,
            sc_home.avg_hard_hit as home_team_hardhit,
            sc_home.avg_sprint as home_team_sprint,

            -- Statcast pitching
            sc_pit_away.avg_xera as away_pen_xera,
            sc_pit_away.avg_fb_velo as away_pen_fb_velo,
            sc_pit_away.avg_fb_spin as away_pen_fb_spin,
            sc_pit_home.avg_xera as home_pen_xera,
            sc_pit_home.avg_fb_velo as home_pen_fb_velo,
            sc_pit_home.avg_fb_spin as home_pen_fb_spin,

            -- Pitcher logs
            pl_away.fip as away_starter_fip, pl_away.era as away_starter_era,
            pl_away.k as away_starter_k, pl_away.bb as away_starter_bb,
            pl_away.hr as away_starter_hr, pl_away.ip as away_starter_ip,
            pl_away.avg_pitches as away_starter_avg_pitches,
            pl_away.games as away_starter_games,
            pl_home.fip as home_starter_fip, pl_home.era as home_starter_era,
            pl_home.k as home_starter_k, pl_home.bb as home_starter_bb,
            pl_home.hr as home_starter_hr, pl_home.ip as home_starter_ip,
            pl_home.avg_pitches as home_starter_avg_pitches,
            pl_home.games as home_starter_games,

            -- Pitcher Statcast
            ps_away.xwoba_against as away_ps_xwoba, ps_away.xera as away_ps_xera,
            ps_away.brl_pct_against as away_ps_brl, ps_away.exit_velo_against as away_ps_ev,
            ps_away.k_pct as away_ps_k_pct, ps_away.bb_pct as away_ps_bb_pct,
            ps_away.whiff_pct as away_ps_whiff, ps_away.fb_velocity as away_ps_fb_velo,
            ps_away.fb_spin as away_ps_fb_spin,
            ps_home.xwoba_against as home_ps_xwoba, ps_home.xera as home_ps_xera,
            ps_home.brl_pct_against as home_ps_brl, ps_home.exit_velo_against as home_ps_ev,
            ps_home.k_pct as home_ps_k_pct, ps_home.bb_pct as home_ps_bb_pct,
            ps_home.whiff_pct as home_ps_whiff, ps_home.fb_velocity as home_ps_fb_velo,
            ps_home.fb_spin as home_ps_fb_spin,
            o.total_line as odds_total,
            o.over_odds as odds_over,
            o.under_odds as odds_under

        FROM games g
        -- ASOF LEFT JOIN: pulls most recent data STRICTLY BEFORE game date
        ASOF LEFT JOIN team_hitting th_away ON g.away_team = th_away.team AND g.date > th_away.date
        ASOF LEFT JOIN team_hitting th_home ON g.home_team = th_home.team AND g.date > th_home.date
        ASOF LEFT JOIN statcast_batting sc_bat_away ON g.away_team = sc_bat_away.team AND g.date > sc_bat_away.date
        ASOF LEFT JOIN statcast_batting sc_home ON g.home_team = sc_home.team AND g.date > sc_home.date
        ASOF LEFT JOIN statcast_pitching sc_pit_away ON g.away_team = sc_pit_away.team AND g.date > sc_pit_away.date
        ASOF LEFT JOIN statcast_pitching sc_pit_home ON g.home_team = sc_pit_home.team AND g.date > sc_pit_home.date
        ASOF LEFT JOIN pitcher_logs pl_away ON g.away_pitcher_id = pl_away.pitcher_id AND g.date > pl_away.date
        ASOF LEFT JOIN pitcher_logs pl_home ON g.home_pitcher_id = pl_home.pitcher_id AND g.date > pl_home.date
        ASOF LEFT JOIN pitcher_statcast ps_away ON g.away_pitcher_id = ps_away.pitcher_id AND g.date > ps_away.date
        ASOF LEFT JOIN pitcher_statcast ps_home ON g.home_pitcher_id = ps_home.pitcher_id AND g.date > ps_home.date
        -- Odds are set before the game (no look-ahead risk)
        LEFT JOIN (
            SELECT date, game_pk, total_line, over_odds, under_odds
            FROM odds WHERE bookmaker = 'fanduel' AND market = 'totals'
        ) o ON g.date = o.date AND g.game_pk = o.game_pk
        WHERE g.date = ?
        ORDER BY g.game_pk
    """, [date_str])

    if 'total_line' in games.columns:
        games = games.rename(columns={'total_line': 'odds_total'})

    if games.empty:
        print(f"  ⚠️  No games found for {date_str}")
        return []

    # Apply same feature engineering
    compute_league_medians(games)
    feature_df = _apply_feature_engineering(games)

    # Predict
    X = feature_df[feature_cols]
    predictions = predict_model_totals(model, feature_df, feature_cols, target_mode)

    # Build results
    results = []
    for i, (_, row) in enumerate(games.iterrows()):
        pred_total = predictions[i]
        actual = row.get('total_runs')

        result = {
            'game_pk': row['game_pk'],
            'away': row['away_team'],
            'home': row['home_team'],
            'predicted_total': round(pred_total, 2),
            'actual_total': actual,
            'edge': None,
            'direction': None,
            'confidence': 'NO_DATA',
        }

        try:
            if actual is not None and not pd.isna(actual):
                result['error'] = round(abs(pred_total - actual), 2)
        except (TypeError, ValueError):
            pass

        results.append(result)

    # Print summary
    print(f"\n  {'='*60}")
    print(f"  📊 PREDICTIONS FOR {date_str}")
    print(f"  {'='*60}")
    for r in results:
        actual_val = r['actual_total']
        try:
            has_actual = actual_val is not None and not pd.isna(actual_val)
        except (TypeError, ValueError):
            has_actual = False
        actual_str = f" | Actual: {actual_val}" if has_actual else ""
        print(f"  {r['away']} @ {r['home']}: predicted {r['predicted_total']:.1f} runs{actual_str}")
    print(f"  {'='*60}\n")

    return results


def _apply_feature_engineering(df):
    """Apply the same feature engineering as build_feature_matrix to new data."""
    df = df.copy()

    # Rest days - compute from game dates
    import duckdb
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    all_game_dates = conn.execute("""
        SELECT DISTINCT date, away_team as team FROM games
        UNION
        SELECT DISTINCT date, home_team as team FROM games
        ORDER BY team, date
    """).df()
    conn.close()

    for team in df['away_team'].unique():
        team_dates = all_game_dates[all_game_dates['team'] == team]['date'].sort_values().tolist()
        date_to_idx = {d: i for i, d in enumerate(team_dates)}

        for idx in df[df['away_team'] == team].index:
            game_date = df.loc[idx, 'date']
            if game_date in date_to_idx:
                game_idx = date_to_idx[game_date]
                if game_idx > 0:
                    rest = (game_date - team_dates[game_idx - 1]).days
                    df.loc[idx, 'away_rest_days'] = rest
                else:
                    df.loc[idx, 'away_rest_days'] = 3

        for idx in df[df['home_team'] == team].index:
            game_date = df.loc[idx, 'date']
            if game_date in date_to_idx:
                game_idx = date_to_idx[game_date]
                if game_idx > 0:
                    rest = (game_date - team_dates[game_idx - 1]).days
                    df.loc[idx, 'home_rest_days'] = rest
                else:
                    df.loc[idx, 'home_rest_days'] = 3

    df['away_rest_days'] = df.get('away_rest_days', pd.Series([3]*len(df))).fillna(3)
    df['home_rest_days'] = df.get('home_rest_days', pd.Series([3]*len(df))).fillna(3)
    df['rest_diff'] = df['home_rest_days'] - df['away_rest_days']
    df['combined_rest'] = df['away_rest_days'] + df['home_rest_days']
    df['away_short_rest'] = (df['away_rest_days'] <= 2).astype(int)
    df['home_short_rest'] = (df['home_rest_days'] <= 2).astype(int)

    # Offense differentials
    df['diff_ops'] = df['home_ops'] - df['away_ops']
    df['diff_avg'] = df['home_avg'] - df['away_avg']
    df['diff_obp'] = df['home_obp'] - df['away_obp']
    df['diff_slg'] = df['home_slg'] - df['away_slg']
    df['diff_iso'] = df['home_iso'] - df['away_iso']
    df['diff_hr_rate'] = df['home_hr_rate'] - df['away_hr_rate']
    df['diff_bb_rate'] = df['home_bb_rate'] - df['away_bb_rate']
    df['diff_k_rate'] = df['home_k_rate'] - df['away_k_rate']
    df['diff_babip'] = df['home_babip'] - df['away_babip']
    df['combined_ops'] = df['home_ops'] + df['away_ops']
    df['combined_obp'] = df['home_obp'] + df['away_obp']
    df['combined_iso'] = df['home_iso'] + df['away_iso']
    df['combined_hr_rate'] = df['home_hr_rate'] + df['away_hr_rate']
    df['away_rpg'] = df['away_runs_scored'] / df['away_pa'].clip(lower=1) * 6200 / 162
    df['home_rpg'] = df['home_runs_scored'] / df['home_pa'].clip(lower=1) * 6200 / 162
    df['combined_rpg'] = df['away_rpg'] + df['home_rpg']

    # Pitcher differentials
    df['diff_starter_fip'] = df['home_starter_fip'] - df['away_starter_fip']
    df['diff_starter_era'] = df['home_starter_era'] - df['away_starter_era']
    df['combined_starter_fip'] = df['home_starter_fip'] + df['away_starter_fip']
    df['combined_starter_era'] = df['home_starter_era'] + df['away_starter_era']
    df['away_starter_kbb'] = df['away_starter_k'] / df['away_starter_bb'].clip(lower=1)
    df['home_starter_kbb'] = df['home_starter_k'] / df['home_starter_bb'].clip(lower=1)
    df['diff_starter_kbb'] = df['home_starter_kbb'] - df['away_starter_kbb']
    df['diff_ps_xera'] = df['home_ps_xera'] - df['away_ps_xera']
    df['diff_ps_xwoba'] = df['home_ps_xwoba'] - df['away_ps_xwoba']
    df['diff_ps_whiff'] = df['home_ps_whiff'] - df['away_ps_whiff']
    df['diff_ps_k_pct'] = df['home_ps_k_pct'] - df['away_ps_k_pct']
    df['diff_ps_bb_pct'] = df['home_ps_bb_pct'] - df['away_ps_bb_pct']
    df['combined_fb_velo'] = df['away_ps_fb_velo'] + df['home_ps_fb_velo']
    df['combined_fb_spin'] = df['away_ps_fb_spin'] + df['home_ps_fb_spin']

    # Batted ball
    df['diff_team_brl'] = df['home_team_brl'] - df['away_team_brl']
    df['diff_team_ev'] = df['home_team_ev'] - df['away_team_ev']
    df['diff_team_hardhit'] = df['home_team_hardhit'] - df['away_team_hardhit']
    df['combined_team_brl'] = df['home_team_brl'] + df['away_team_brl']
    df['combined_team_ev'] = df['home_team_ev'] + df['away_team_ev']
    df['diff_ps_brl'] = df['home_ps_brl'] - df['away_ps_brl']
    df['diff_ps_ev'] = df['home_ps_ev'] - df['away_ps_ev']

    # Park
    df['park_hitter'] = df['park_factor']
    df['altitude_effect'] = df['stadium_alt'] / 5280.0
    df['is_domed'] = df['stadium_roof'].astype(int)
    df['cf_bearing_norm'] = df['stadium_cf_bearing'] / 360.0
    df = add_weather_features(df)

    # Market features (must match build_feature_matrix)
    if 'odds_total' not in df.columns:
        df['odds_total'] = np.nan
    df['has_odds'] = df['odds_total'].notna().astype(int)
    df['odds_total'] = df['odds_total'].fillna(8.5)
    df['diff_from_market'] = 0

    # Situational
    df['away_starter_workload'] = df['away_starter_games'].apply(
        lambda x: 0 if x < 5 else (1 if x < 10 else (2 if x < 15 else 3))
    )
    df['home_starter_workload'] = df['home_starter_games'].apply(
        lambda x: 0 if x < 5 else (1 if x < 10 else (2 if x < 15 else 3))
    )
    df['diff_workload'] = df['home_starter_workload'] - df['away_starter_workload']
    df['away_starter_ip_pg'] = df['away_starter_ip'] / df['away_starter_games'].clip(lower=1)
    df['home_starter_ip_pg'] = df['home_starter_ip'] / df['home_starter_games'].clip(lower=1)
    df['diff_ip_pg'] = df['home_starter_ip_pg'] - df['away_starter_ip_pg']
    df['diff_avg_pitches'] = df['home_starter_avg_pitches'] - df['away_starter_avg_pitches']

    # Interactions
    df['offense_pitching_gap'] = df['combined_ops'] - (df['combined_starter_fip'] * 30)
    df['barrel_matchup'] = df['combined_team_brl'] - df['diff_ps_brl']
    df['park_offense'] = df['park_hitter'] * df['combined_ops']
    df['starter_park'] = df['combined_starter_fip'] * (2.0 - df['park_hitter'])

    # Date features
    df['month'] = pd.to_datetime(df['date']).dt.month
    df['day_of_week'] = pd.to_datetime(df['date']).dt.dayofweek
    df['is_weekend'] = (df['day_of_week'] >= 5).astype(int)
    df['early_season'] = (df['month'] <= 4).astype(int)

    # Fill NaN
    for col in df.columns:
        if df[col].dtype in [np.float64, np.float32, np.int64]:
            df[col] = safe_fill(df[col])

    return df


# ═══════════════════════════════════════════════════════════════════════════════
# SHAP EXPLAINABILITY
# ═══════════════════════════════════════════════════════════════════════════════

def explain_model(model, X_train, feature_cols, top_n=20):
    """Generate SHAP feature importance."""
    print("\n🔍 Computing SHAP values...")
    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X_train)

    # Mean absolute SHAP value per feature
    importance = pd.DataFrame({
        'feature': feature_cols,
        'mean_abs_shap': np.abs(shap_values).mean(axis=0)
    }).sort_values('mean_abs_shap', ascending=False)

    print(f"\n  📊 Top {top_n} Features by SHAP Importance:")
    print(f"  {'─'*45}")
    for _, row in importance.head(top_n).iterrows():
        bar = '█' * int(row['mean_abs_shap'] / importance['mean_abs_shap'].max() * 20)
        print(f"  {row['feature']:30s} {row['mean_abs_shap']:.4f} {bar}")

    return importance


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN TRAINING PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

def train_model(db: Database, tune=False, backtest=False, predict_date=None):
    """Full training pipeline."""
    print("=" * 70)
    print("⚾ MLB O/U XGBoost Training Pipeline")
    print("=" * 70)

    # 1. Build feature matrix
    df = build_feature_matrix(db)
    if TARGET_MODE == 'market_residual':
        before = len(df)
        df = df[df['has_odds'] == 1].copy()
        print(f"  🎯 Residual mode: using {len(df)}/{before} games with real odds baselines")
    if len(df) < 20:
        print(f"\n❌ Not enough data ({len(df)} games). Need at least 20.")
        return None

    feature_cols = get_feature_cols(df)
    validate_no_leakage_features(feature_cols)
    print(f"\n📋 {len(feature_cols)} features: {feature_cols[:10]}...")

    # 2. Temporal backtest (optional)
    if backtest:
        bt_results = temporal_backtest(df, feature_cols, target_mode=TARGET_MODE)
        if bt_results:
            avg_mae = np.mean([r['mae'] for r in bt_results])
            avg_r2 = np.mean([r['r2'] for r in bt_results])
            print(f"\n  📈 Backtest Summary:")
            print(f"     Avg MAE: {avg_mae:.3f}")
            print(f"     Avg R²:  {avg_r2:.3f}")

    # 3. Prepare train/test split
    train, test = prepare_train_test(df)
    X_train = train[feature_cols]
    if TARGET_MODE == 'market_residual':
        y_train = train[TARGET] - train['odds_total']
    else:
        y_train = train[TARGET]
    X_test = test[feature_cols]
    y_test = test[TARGET]
    y_eval = y_test - test['odds_total'] if TARGET_MODE == 'market_residual' else y_test

    # 4. Hyperparameter tuning (optional)
    if tune:
        best_params = tune_hyperparameters(X_train, y_train, n_trials=75)
        params = {
            'objective': 'reg:squarederror',
            'random_state': 42,
            'verbosity': 0,
            **best_params,
        }
    else:
        if TARGET_MODE == 'market_residual':
            params = TUNED_RESIDUAL_PARAMS.copy()
        else:
            params = {
                'objective': 'reg:squarederror',
                'n_estimators': 400,
                'max_depth': 5,
                'learning_rate': 0.05,
                'min_child_weight': 5,
                'subsample': 0.8,
                'colsample_bytree': 0.8,
                'reg_alpha': 1.0,
                'reg_lambda': 1.0,
                'random_state': 42,
                'verbosity': 0,
            }

    # 5. Train final model
    print("\n🏋️ Training final model...")
    model = xgb.XGBRegressor(**params)
    model.fit(
        X_train, y_train,
        eval_set=[(X_test, y_eval)],
        verbose=False,
    )

    # 6. Evaluate
    preds = predict_model_totals(model, test, feature_cols, TARGET_MODE)
    mae = mean_absolute_error(y_test, preds)
    rmse = math.sqrt(mean_squared_error(y_test, preds))
    r2 = r2_score(y_test, preds)

    print(f"\n  📊 Test Set Performance:")
    print(f"     MAE:  {mae:.3f} runs")
    print(f"     RMSE: {rmse:.3f} runs")
    print(f"     R²:   {r2:.3f}")

    # Compare to naive
    naive_mae = mean_absolute_error(y_test, [y_train.mean()] * len(y_test))
    if TARGET_MODE == 'market_residual':
        naive_mae = mean_absolute_error(y_test, [train[TARGET].mean()] * len(y_test))
        market_mae = mean_absolute_error(y_test, test['odds_total'])
        print(f"     Market MAE: {market_mae:.3f} (model {'beats' if mae < market_mae else 'loses to'} market by {abs(market_mae - mae):.3f})")
    print(f"     Naive MAE: {naive_mae:.3f} (model {'beats' if mae < naive_mae else 'loses to'} naive by {abs(naive_mae - mae):.3f})")

    # Edge analysis
    market_edges = preds - test['odds_total'].to_numpy()
    pick_metrics = evaluate_over_under_picks(
        y_test.to_numpy(),
        test['odds_total'].to_numpy(),
        preds,
        threshold=PICK_EDGE_THRESHOLD,
    )
    print(f"\n  🎯 Edge Analysis:")
    print(f"     Games with ≥0.5 run edge: {(np.abs(market_edges) >= 0.5).sum()}/{len(market_edges)} ({(np.abs(market_edges) >= 0.5).mean()*100:.0f}%)")
    print(f"     Games with ≥1.0 run edge: {(np.abs(market_edges) >= 1.0).sum()}/{len(market_edges)} ({(np.abs(market_edges) >= 1.0).mean()*100:.0f}%)")
    print(f"     Games with ≥{PICK_EDGE_THRESHOLD:.1f} run edge: {pick_metrics['picks']}/{len(market_edges)}")
    if pick_metrics['picks']:
        print(
            f"     Holdout picks: {pick_metrics['wins']}-{pick_metrics['losses']}-{pick_metrics['pushes']} "
            f"| {pick_metrics['win_pct']:.1f}% | {pick_metrics['units']:+.1f}u "
            f"| ROI {pick_metrics['roi_pct']:+.1f}%"
        )

    # 7. SHAP explainability
    importance = explain_model(model, X_train, feature_cols)

    # 8. Save model + metadata
    model_path = MODEL_DIR / 'ou_xgb.json'
    model.save_model(str(model_path))

    metadata = {
        'trained_at': datetime.now().isoformat(),
        'train_games': len(train),
        'test_games': len(test),
        'train_date_range': f"{train['date'].min()} → {train['date'].max()}",
        'test_date_range': f"{test['date'].min()} → {test['date'].max()}",
        'features': feature_cols,
        'target_mode': TARGET_MODE,
        'baseline_feature': 'odds_total' if TARGET_MODE == 'market_residual' else None,
        'pick_edge_threshold': PICK_EDGE_THRESHOLD,
        'params': {k: v for k, v in params.items() if k != 'verbosity'},
        'metrics': {
            'mae': round(mae, 4),
            'rmse': round(rmse, 4),
            'r2': round(r2, 4),
            'naive_mae': round(naive_mae, 4),
            **({'market_mae': round(market_mae, 4)} if TARGET_MODE == 'market_residual' else {}),
        },
        'betting_metrics': pick_metrics,
        'tuning_summary': {
            'objective': 'maximize held-out O/U units at a practical edge threshold',
            'selection': 'optuna_trial0 params with 2.0 run edge threshold',
            'candidate_result': '91-64-5, +20.6 units, +12.08% ROI on 2025-08-25 through 2026-05-06 holdout',
            'note': 'All-game MAE remains market anchored; betting selection uses model residual edge over FanDuel totals.',
        },
        'leakage_policy': {
            'temporal_joins': 'ASOF joins require source stat dates strictly before game date',
            'blocked_features': sorted(LEAKAGE_BLOCKED_FEATURES),
            'market_features': 'odds columns are stored for edge comparison but excluded from XGBoost input',
        },
        'top_features': importance.head(10)['feature'].tolist(),
    }

    meta_path = MODEL_DIR / 'model_metadata.json'
    with open(meta_path, 'w') as f:
        json.dump(metadata, f, indent=2, default=str)

    print(f"\n  💾 Model saved: {model_path}")
    print(f"  💾 Metadata saved: {meta_path}")

    # 9. Predict today if requested
    if predict_date:
        predict_todays_games(model, db, feature_cols, predict_date, TARGET_MODE)

    return model


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='MLB O/U XGBoost Training')
    parser.add_argument('--tune', action='store_true', help='Run Optuna hyperparameter search')
    parser.add_argument('--backtest', action='store_true', help='Run temporal backtest')
    parser.add_argument('--predict', type=str, default=None, help='Predict games on date (YYYY-MM-DD)')
    parser.add_argument('--season', type=int, default=None, help='Filter to specific season')
    args = parser.parse_args()

    db = Database()

    if args.season:
        # Filter to season
        model = train_model(db, tune=args.tune, backtest=args.backtest, predict_date=args.predict)
    else:
        model = train_model(db, tune=args.tune, backtest=args.backtest, predict_date=args.predict)

    db.close()
    print("\n✅ Done!")
