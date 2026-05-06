#!/usr/bin/env python3
"""Generate O/U picks for today using the trained XGBoost model."""

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import xgboost as xgb

sys.path.insert(0, str(Path(__file__).parent))
from db import Database
from train import (
    build_feature_matrix, get_feature_cols, _apply_feature_engineering,
    compute_league_medians, safe_fill, TARGET, MODEL_DIR, predict_model_totals
)

DATA_DIR = Path(__file__).parent / 'data'
DATA_DIR.mkdir(exist_ok=True)

EDGE_THRESHOLD = 0.8

def is_missing_odds(odds):
    if odds is None:
        return True
    try:
        return bool(np.isnan(odds))
    except TypeError:
        return False

def confidence_stars(edge):
    abs_e = abs(edge)
    if abs_e >= 1.5:
        return "★★★"
    elif abs_e >= 0.8:
        return "★★☆"
    elif abs_e >= 0.5:
        return "★☆☆"
    return "☆☆☆"

def build_pick(away, home, pred, odds):
    pred = float(pred)
    if is_missing_odds(odds):
        return {
            'away': away,
            'home': home,
            'pred': round(pred, 2),
            'odds': None,
            'edge': None,
            'conf': "☆☆☆",
            'pick': "NO ODDS",
        }

    odds = float(odds)
    edge = pred - odds
    if abs(edge) >= EDGE_THRESHOLD:
        direction = "OVER" if edge > 0 else "UNDER"
    else:
        direction = "PASS"

    return {
        'away': away,
        'home': home,
        'pred': round(pred, 2),
        'odds': odds,
        'edge': round(edge, 2),
        'conf': confidence_stars(edge),
        'pick': direction,
    }

def main():
    date_str = datetime.now().strftime('%Y-%m-%d')
    if len(sys.argv) > 1:
        date_str = sys.argv[1]

    # Load model
    model_path = MODEL_DIR / 'ou_xgb.json'
    model = xgb.XGBRegressor()
    model.load_model(str(model_path))

    # Load feature list from metadata
    meta_path = MODEL_DIR / 'model_metadata.json'
    with open(meta_path) as f:
        meta = json.load(f)
    feature_cols = meta['features']

    # Build full feature matrix to get league medians
    db = Database()
    full_df = build_feature_matrix(db)

    # Get today's games with odds
    games_df = db.df(f"""
        SELECT
            g.game_pk, g.date, g.away_team, g.home_team,
            g.park_factor, g.stadium_name, g.stadium_roof,
            g.stadium_alt, g.stadium_cf_bearing,
            g.weather_temp_f, g.weather_wind_mph,
            g.weather_wind_dir_degrees, g.weather_wind_out_cf,
            g.weather_humidity_pct, g.weather_precip_pct,
            g.weather_pressure_mb, g.weather_is_indoor,
            g.total_runs, g.away_score, g.home_score,

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

            sc_pit_away.avg_xera as away_pen_xera,
            sc_pit_away.avg_fb_velo as away_pen_fb_velo,
            sc_pit_away.avg_fb_spin as away_pen_fb_spin,
            sc_pit_home.avg_xera as home_pen_xera,
            sc_pit_home.avg_fb_velo as home_pen_fb_velo,
            sc_pit_home.avg_fb_spin as home_pen_fb_spin,

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
        LEFT JOIN team_hitting th_away ON g.date = th_away.date AND g.away_team = th_away.team
        LEFT JOIN team_hitting th_home ON g.date = th_home.date AND g.home_team = th_home.team
        LEFT JOIN statcast_batting sc_bat_away ON g.date = sc_bat_away.date AND g.away_team = sc_bat_away.team
        LEFT JOIN statcast_batting sc_home ON g.date = sc_home.date AND g.home_team = sc_home.team
        LEFT JOIN statcast_pitching sc_pit_away ON g.date = sc_pit_away.date AND g.away_team = sc_pit_away.team
        LEFT JOIN statcast_pitching sc_pit_home ON g.date = sc_pit_home.date AND g.home_team = sc_pit_home.team
        LEFT JOIN pitcher_logs pl_away ON g.date = pl_away.date AND g.away_pitcher_id = pl_away.pitcher_id
        LEFT JOIN pitcher_logs pl_home ON g.date = pl_home.date AND g.home_pitcher_id = pl_home.pitcher_id
        LEFT JOIN pitcher_statcast ps_away ON g.date = ps_away.date AND g.away_pitcher_id = ps_away.pitcher_id
        LEFT JOIN pitcher_statcast ps_home ON g.date = ps_home.date AND g.home_pitcher_id = ps_home.pitcher_id
        LEFT JOIN (
            SELECT date, game_pk, total_line, over_odds, under_odds
            FROM odds WHERE bookmaker = 'fanduel' AND market = 'totals'
        ) o ON g.date = o.date AND g.game_pk = o.game_pk
        WHERE g.date = ?
        ORDER BY g.game_pk
    """, [date_str])

    if 'total_line' in games_df.columns:
        games_df = games_df.rename(columns={'total_line': 'odds_total'})

    if games_df.empty:
        print(f"⚠️  No games found for {date_str}")
        db.close()
        return

    # Feature engineering - close db first to avoid DuckDB connection conflict
    db.close()
    compute_league_medians(games_df)
    feature_df = _apply_feature_engineering(games_df)

    # Predict. Residual models output runs over/under the market baseline.
    target_mode = meta.get('target_mode', 'raw_total')
    predictions = predict_model_totals(model, feature_df, feature_cols, target_mode)

    # Generate picks
    picks = []
    for i, (_, row) in enumerate(games_df.iterrows()):
        pred = float(predictions[i])
        odds = row.get('odds_total', None)
        pick = build_pick(row['away_team'], row['home_team'], pred, odds)
        pick['game_pk'] = int(row['game_pk'])
        picks.append(pick)

    # Save picks
    output = {'date': date_str, 'picks': picks}
    outpath = DATA_DIR / f'picks_{date_str}.json'
    with open(outpath, 'w') as f:
        json.dump(output, f, indent=2)

    # Print formatted results
    print(f"\n{'='*65}")
    print(f"  ⚾ O/U PICKS FOR {date_str}")
    print(f"  Model trained on {meta['train_games']} games | Edge threshold: ≥{EDGE_THRESHOLD} runs")
    print(f"{'='*65}\n")

    for p in picks:
        if p['odds'] is None:
            print(f"  {p['away']:>3} @ {p['home']:<3} | {p['pred']:5.1f} |   -- |     -- | {p['conf']} | {p['pick']}")
        else:
            edge_str = f"+{p['edge']:.2f}" if p['edge'] >= 0 else f"{p['edge']:.2f}"
            print(f"  {p['away']:>3} @ {p['home']:<3} | {p['pred']:5.1f} | {p['odds']:4.1f} | {edge_str:>6} | {p['conf']} | {p['pick']}")

    # Count actual picks vs passes
    actual = [p for p in picks if p['pick'] not in ('PASS', 'NO ODDS')]
    passed = [p for p in picks if p['pick'] == 'PASS']
    no_odds = [p for p in picks if p['pick'] == 'NO ODDS']
    print(f"\n  📊 {len(actual)} picks, {len(passed)} passes, {len(no_odds)} without odds out of {len(picks)} games")
    print(f"{'='*65}\n")

    # Print existing record
    log_path = Path(__file__).parent / 'PICKS_LOG.md'
    if log_path.exists():
        print("  📋 EXISTING RECORD:")
        with open(log_path) as f:
            content = f.read()
        # Print last 30 lines
        lines = content.strip().split('\n')
        for line in lines[-30:]:
            print(f"  {line}")
        print()

    db.close()

if __name__ == '__main__':
    main()
