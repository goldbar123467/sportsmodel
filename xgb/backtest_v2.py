#!/usr/bin/env python3
"""
Backtest v2 — Fixes look-ahead bias using point-in-time pitcher logs.
Analyzes residuals by pitcher fatigue state, edge thresholds, and ROI.
"""

import json
import sys
import glob
import statistics
from pathlib import Path
from collections import defaultdict

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / 'data'
CONFIG_DIR = SCRIPT_DIR.parent / 'config'

# Load config
with open(CONFIG_DIR / 'defaults.json') as f:
    CONFIG = json.load(f)

LG_AVG = CONFIG['leagueAvgRunsPerGame']  # 4.5
LG_AVG_PA = CONFIG['leagueAvgRunsPerPA']  # 0.122
FIP_CONSTANT = CONFIG['fipConstant']  # 3.20
FIP_WEIGHT = CONFIG['fipWeight']  # 0.65
ERA_WEIGHT = CONFIG['eraWeight']  # 0.35
MIN_PITCHER_IP = CONFIG.get('minPitcherIP', 30)
MIN_PITCHER_IP_PARTIAL = CONFIG.get('minPitcherIPPartial', 10)
MIN_TEAM_PA = CONFIG.get('minTeamPA', 200)
MIN_TEAM_PA_PARTIAL = CONFIG.get('minTeamPAPartial', 50)
PARTIAL_BLEND = CONFIG.get('partialDataBlend', 0.5)

# Load park factors
with open(CONFIG_DIR / 'teams.json') as f:
    TEAMS_DATA = json.load(f)
PARK_FACTORS = TEAMS_DATA.get('parkFactors', {})

# Edge thresholds to test
EDGE_THRESHOLDS = [0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0]


def load_pitcher_logs():
    """Load all pitcher game logs indexed by (pitcher_id, season)."""
    logs = {}
    for f in glob.glob(str(DATA_DIR / 'pitcher_*.json')):
        # Extract pitcher_id and season from filename
        parts = Path(f).stem.replace('pitcher_', '').split('_')
        if len(parts) != 2:
            continue
        pid, season = int(parts[0]), parts[1]
        with open(f) as fh:
            starts = json.load(fh)
        if isinstance(starts, list) and starts:
            logs[(pid, season)] = sorted(starts, key=lambda s: s.get('date', ''))
    return logs


def get_pitcher_stats_before(pitcher_logs, pitcher_id, season, game_date):
    """
    Get point-in-time pitcher stats using only starts before game_date.
    Returns: { ip, fip, era, k, bb, hr, games, avg_pitch_count }
    """
    key = (pitcher_id, season)
    starts = pitcher_logs.get(key, [])

    # Filter to starts before this game
    prior = [s for s in starts if s.get('date', '') < game_date]

    if not prior:
        return None

    total_ip = sum(s.get('ip', 0) for s in prior)
    total_er = sum(s.get('er', 0) for s in prior)
    total_h = sum(s.get('h', 0) for s in prior)
    total_bb = sum(s.get('bb', 0) for s in prior)
    total_k = sum(s.get('k', 0) for s in prior)
    total_hr = sum(s.get('hr', 0) for s in prior)
    total_pitches = sum(s.get('pitches', 0) for s in prior)
    games = len(prior)

    if total_ip <= 0:
        return None

    # Calculate FIP
    fip = ((13 * total_hr) + (3 * total_bb) - (2 * total_k)) / total_ip + FIP_CONSTANT

    # Calculate ERA
    era = (total_er * 9) / total_ip

    avg_pitches = total_pitches / games if games > 0 else 0

    # Days rest from last start
    rest_days = None
    if prior:
        last_start_date = prior[-1].get('date', '')
        if last_start_date and game_date:
            from datetime import datetime
            try:
                d1 = datetime.strptime(last_start_date, '%Y-%m-%d')
                d2 = datetime.strptime(game_date, '%Y-%m-%d')
                rest_days = (d2 - d1).days
            except:
                pass

    return {
        'ip': total_ip,
        'fip': fip,
        'era': era,
        'k': total_k,
        'bb': total_bb,
        'hr': total_hr,
        'er': total_er,
        'games': games,
        'avg_pitch_count': avg_pitches,
        'rest_days': rest_days,
        'prior_starts': len(prior),
    }


def calc_fip_from_counts(ip, hr, bb, k):
    """Calculate FIP from counting stats."""
    if ip <= 0:
        return LG_AVG
    return ((13 * hr) + (3 * bb) - (2 * k)) / ip + FIP_CONSTANT


def blend_with_lg_avg(stat, ip, lg_val):
    """Blend a stat with league average when data is thin."""
    if ip >= MIN_PITCHER_IP:
        return stat
    if ip < MIN_PITCHER_IP_PARTIAL:
        return lg_val  # Use league avg when very thin
    pct = ((ip - MIN_PITCHER_IP_PARTIAL) / (MIN_PITCHER_IP - MIN_PITCHER_IP_PARTIAL)) * PARTIAL_BLEND
    return stat * pct + lg_val * (1 - pct)


def rate_pitcher_pit(pitcher_stats):
    """
    Rate a pitcher using point-in-time FIP/ERA blend.
    Returns: runs per game allowed (blended)
    """
    if not pitcher_stats:
        return LG_AVG, 'default'

    ip = pitcher_stats.get('ip', 0)
    fip = pitcher_stats.get('fip', LG_AVG)
    era = pitcher_stats.get('era', LG_AVG)

    # Blend with league avg if thin data
    fip_blended = blend_with_lg_avg(fip, ip, LG_AVG)
    era_blended = blend_with_lg_avg(era, ip, LG_AVG)

    # Weighted blend: FIP + ERA
    blended = fip_blended * FIP_WEIGHT + era_blended * ERA_WEIGHT

    confidence = 'high' if ip >= MIN_PITCHER_IP else 'medium' if ip >= MIN_PITCHER_IP_PARTIAL else 'low'
    return blended, confidence


def rate_offense_pit(team_hitting):
    """
    Rate offense using season stats (no per-game breakdown available).
    Note: This still has look-ahead bias for team stats, but pitcher stats are clean.
    """
    if not team_hitting:
        return 1.0

    pa = team_hitting.get('pa', 0)
    runs = team_hitting.get('r', 0)

    if pa < MIN_TEAM_PA_PARTIAL:
        return 1.0

    rpa = runs / pa
    rpa_blended = blend_with_lg_avg(rpa, pa, LG_AVG_PA)
    multiplier = rpa_blended / LG_AVG_PA
    return multiplier


def project_game_pit(game, pitcher_logs):
    """
    Run projection with point-in-time pitcher stats.
    """
    home_team = game.get('home_team', '')
    park_factor = PARK_FACTORS.get(home_team, 1.0)
    date = game.get('date', '')
    season = date[:4] if date else ''

    # Get point-in-time pitcher stats
    away_pid = game.get('away_pitcher_id')
    home_pid = game.get('home_pitcher_id')

    away_stats = get_pitcher_stats_before(pitcher_logs, away_pid, season, date) if away_pid else None
    home_stats = get_pitcher_stats_before(pitcher_logs, home_pid, season, date) if home_pid else None

    # Rate pitchers
    away_pitcher, away_conf = rate_pitcher_pit(away_stats)
    home_pitcher, home_conf = rate_pitcher_pit(home_stats)

    # Park-adjust
    away_pitcher_adj = away_pitcher * park_factor
    home_pitcher_adj = home_pitcher * park_factor

    # Rate offenses (still season-level, but best we have)
    away_offense = rate_offense_pit(game.get('away_hitting'))
    home_offense = rate_offense_pit(game.get('home_hitting'))

    # Project runs
    away_runs = LG_AVG * away_offense * (home_pitcher_adj / LG_AVG)
    home_runs = LG_AVG * home_offense * (away_pitcher_adj / LG_AVG)

    total = away_runs + home_runs

    # Tactician adjustments (using point-in-time data)
    tactician_adj = 0
    fatigue_state = 'unknown'

    if away_stats and home_stats:
        # Days rest
        away_rest = away_stats.get('rest_days')
        home_rest = home_stats.get('rest_days')

        # Pitch count fatigue
        away_pc_adj = 0
        home_pc_adj = 0
        avg_pc = away_stats.get('avg_pitch_count', 95)
        if avg_pc >= 115: away_pc_adj = 0.7
        elif avg_pc >= 105: away_pc_adj = 0.4
        elif avg_pc >= 95: away_pc_adj = 0.2
        elif avg_pc < 85: away_pc_adj = -0.1

        avg_pc = home_stats.get('avg_pitch_count', 95)
        if avg_pc >= 115: home_pc_adj = 0.7
        elif avg_pc >= 105: home_pc_adj = 0.4
        elif avg_pc >= 95: home_pc_adj = 0.2
        elif avg_pc < 85: home_pc_adj = -0.1

        # TTO penalty (approximate from innings)
        away_tto = 0
        home_tto = 0
        if away_stats['games'] > 0:
            avg_ip = away_stats['ip'] / away_stats['games']
            if avg_ip >= 6.5: away_tto = 0.4
            elif avg_ip >= 5.5: away_tto = 0.2
        if home_stats['games'] > 0:
            avg_ip = home_stats['ip'] / home_stats['games']
            if avg_ip >= 6.5: home_tto = 0.4
            elif avg_ip >= 5.5: home_tto = 0.2

        # Workload decay
        away_wd = 0
        home_wd = 0
        if away_stats['ip'] >= 200: away_wd = 0.2
        elif away_stats['ip'] >= 180: away_wd = 0.1
        elif away_stats['ip'] >= 140: away_wd = 0.05
        if home_stats['ip'] >= 200: home_wd = 0.2
        elif home_stats['ip'] >= 180: home_wd = 0.1
        elif home_stats['ip'] >= 140: home_wd = 0.05

        # Rest days adjustment
        away_rest_adj = 0
        home_rest_adj = 0
        if away_rest is not None:
            if away_rest <= 2: away_rest_adj = 0.3
            elif away_rest == 3: away_rest_adj = 0.0
            elif away_rest == 4: away_rest_adj = -0.1
            else: away_rest_adj = -0.2
        if home_rest is not None:
            if home_rest <= 2: home_rest_adj = 0.3
            elif home_rest == 3: home_rest_adj = 0.0
            elif home_rest == 4: home_rest_adj = -0.1
            else: home_rest_adj = -0.2

        # Combine: away pitcher fatigue helps home team score, and vice versa
        away_fatigue = away_pc_adj + away_tto + away_wd + away_rest_adj
        home_fatigue = home_pc_adj + home_tto + home_wd + home_rest_adj
        tactician_adj = home_fatigue - away_fatigue  # Home benefits from away pitcher fatigue

        # Classify fatigue state
        avg_fatigue = (away_fatigue + home_fatigue) / 2
        if avg_fatigue >= 0.5:
            fatigue_state = 'tired'
        elif avg_fatigue <= 0.0:
            fatigue_state = 'rested'
        else:
            fatigue_state = 'normal'

    total += tactician_adj

    return {
        'projected': round(total, 2),
        'away_pitcher': away_pitcher,
        'home_pitcher': home_pitcher,
        'away_confidence': away_conf,
        'home_confidence': home_conf,
        'fatigue_state': fatigue_state,
        'tactician_adj': tactician_adj,
    }


def load_all_games():
    """Load all games with scores."""
    games = []

    # 2024 dataset
    ds24 = DATA_DIR / 'dataset_2024_20260425.json'
    if ds24.exists():
        with open(ds24) as f:
            data = json.load(f)
        for g in data:
            if g.get('total_runs') is not None and g.get('away_pitcher_id') and g.get('home_pitcher_id'):
                games.append(g)

    # 2025 dataset
    ds25 = DATA_DIR / 'dataset_2025_20260424.json'
    if ds25.exists():
        with open(ds25) as f:
            data = json.load(f)
        for g in data:
            if g.get('total_runs') is not None and g.get('away_pitcher_id') and g.get('home_pitcher_id'):
                games.append(g)

    # Individual game files (2026)
    for f in sorted(glob.glob(str(DATA_DIR / 'games_*.json'))):
        with open(f) as fh:
            data = json.load(fh)
        for g in data:
            if g.get('total_runs') is not None and g.get('away_pitcher_id') and g.get('home_pitcher_id'):
                games.append(g)

    return games


def print_stats(label, values):
    if not values:
        print(f"  {label}: no data")
        return
    mean = statistics.mean(values)
    median = statistics.median(values)
    stdev = statistics.stdev(values) if len(values) > 1 else 0
    se = stdev / (len(values) ** 0.5) if len(values) > 1 else 0
    print(f"  {label}: mean={mean:+.3f}, median={median:+.3f}, stdev={stdev:.3f}, se={se:.4f}, n={len(values)}")


def main():
    print("=" * 70)
    print("  MLB Projection Model — Backtest v2 (Point-in-Time Pitcher Stats)")
    print("=" * 70)
    print()

    # Load data
    print("Loading pitcher logs...")
    pitcher_logs = load_pitcher_logs()
    print(f"  Loaded {len(pitcher_logs)} pitcher-season logs")

    games = load_all_games()
    print(f"  Loaded {len(games)} games with scores and pitcher IDs")
    print()

    # Run projections
    results = []
    skipped = 0
    for g in games:
        proj = project_game_pit(g, pitcher_logs)
        actual = g['total_runs']
        residual = proj['projected'] - actual

        results.append({
            'date': g.get('date', ''),
            'away': g.get('away_team', ''),
            'home': g.get('home_team', ''),
            'projected': proj['projected'],
            'actual': actual,
            'residual': residual,
            'fatigue_state': proj['fatigue_state'],
            'away_confidence': proj['away_confidence'],
            'home_confidence': proj['home_confidence'],
            'tactician_adj': proj['tactician_adj'],
            'park_factor': PARK_FACTORS.get(g.get('home_team', ''), 1.0),
        })

    print(f"Projected {len(results)} games (skipped {skipped} with missing pitchers)")
    print()

    # ── OVERALL RESIDUALS ──
    residuals = [r['residual'] for r in results]
    print("─" * 70)
    print("  OVERALL RESIDUAL DISTRIBUTION")
    print("  (positive = model over-predicts = OVER bias)")
    print("─" * 70)
    print_stats("Residual (proj - actual)", residuals)

    mean_res = statistics.mean(residuals)
    positive_pct = sum(1 for r in residuals if r > 0) / len(residuals) * 100
    print(f"  % over-predicts: {positive_pct:.1f}%  |  % under-predicts: {100 - positive_pct:.1f}%")

    # Significance test
    if len(residuals) > 30:
        se = statistics.stdev(residuals) / (len(residuals) ** 0.5)
        t_stat = mean_res / se if se > 0 else 0
        print(f"  t-statistic: {t_stat:.2f}", end="")
        if abs(t_stat) > 2.58:
            print("  ✗ SIGNIFICANT at 99%")
        elif abs(t_stat) > 1.96:
            print("  ✗ SIGNIFICANT at 95%")
        elif abs(t_stat) > 1.64:
            print("  ⚠ MARGINAL at 90%")
        else:
            print("  ✓ No significant bias")
    print()

    # ── RESIDUAL BY FATIGUE STATE ──
    print("─" * 70)
    print("  RESIDUAL BY PITCHER FATIGUE STATE")
    print("  (direct test of the stacking hypothesis)")
    print("─" * 70)
    fatigue_groups = defaultdict(list)
    for r in results:
        fatigue_groups[r['fatigue_state']].append(r['residual'])

    for state in ['rested', 'normal', 'tired', 'unknown']:
        resids = fatigue_groups.get(state, [])
        if resids:
            mean = statistics.mean(resids)
            med = statistics.median(resids)
            se = statistics.stdev(resids) / (len(resids) ** 0.5) if len(resids) > 1 else 0
            print(f"  {state:>8}: mean={mean:+.3f}, median={med:+.3f}, se={se:.4f}, n={len(resids)}")

    # Check if tired games go under projection more
    tired_resids = fatigue_groups.get('tired', [])
    rested_resids = fatigue_groups.get('rested', [])
    if tired_resids and rested_resids:
        diff = statistics.mean(tired_resids) - statistics.mean(rested_resids)
        print(f"\n  Tired - Rested difference: {diff:+.3f}")
        if diff < -0.3:
            print("  → Tired-pitcher games go MORE under projection → stacking problem confirmed")
        elif diff > 0.3:
            print("  → Tired-pitcher games go MORE over projection → model over-corrects for fatigue")
        else:
            print("  → No meaningful difference → fatigue adjustments are roughly calibrated")
    print()

    # ── RESIDUAL BY CONFIDENCE ──
    print("─" * 70)
    print("  RESIDUAL BY PITCHER DATA CONFIDENCE")
    print("─" * 70)
    for conf in ['high', 'medium', 'low']:
        resids = [r['residual'] for r in results if r['away_confidence'] == conf or r['home_confidence'] == conf]
        if resids:
            mean = statistics.mean(resids)
            print(f"  {conf:>8}: mean={mean:+.3f}, n={len(resids)}")
    print()

    # ── RESIDUAL BUCKETS ──
    print("─" * 70)
    print("  RESIDUAL BUCKETS")
    print("─" * 70)
    buckets = [
        ("UNDER by 4+", -999, -4),
        ("UNDER by 3", -4, -3),
        ("UNDER by 2", -3, -2),
        ("UNDER by 1", -2, -1),
        ("Within ±1", -1, 1),
        ("OVER by 1", 1, 2),
        ("OVER by 2", 2, 3),
        ("OVER by 3", 3, 4),
        ("OVER by 4+", 4, 999),
    ]
    for label, lo, hi in buckets:
        count = sum(1 for r in residuals if lo <= r < hi)
        pct = count / len(residuals) * 100
        bar = "█" * int(pct / 2)
        print(f"  {label:>20}: {count:4d} ({pct:5.1f}%) {bar}")
    print()

    # ── EDGE THRESHOLDS & ROI ──
    print("─" * 70)
    print("  EDGE THRESHOLD ANALYSIS (assuming closing lines = actual totals)")
    print("  Using actual game total as proxy for market line")
    print("─" * 70)
    print(f"  {'Edge':>6}  {'Games':>6}  {'Wins':>5}  {'Losses':>5}  {'Win%':>6}  {'ROI':>8}  {'Profit':>8}")
    print(f"  {'─'*6}  {'─'*6}  {'─'*5}  {'─'*5}  {'─'*6}  {'─'*8}  {'─'*8}")

    for threshold in EDGE_THRESHOLDS:
        # For each game, "edge" = projected - actual (using actual as line proxy)
        plays = []
        for r in results:
            edge = r['projected'] - r['actual']
            if abs(edge) >= threshold:
                pick = 'OVER' if edge > 0 else 'UNDER'
                plays.append({
                    'pick': pick,
                    'edge': edge,
                    'actual': r['actual'],
                })

        if not plays:
            print(f"  {threshold:>6.2f}  {'0':>6}  {'-':>5}  {'-':>5}  {'-':>6}  {'-':>8}  {'-':>8}")
            continue

        wins = sum(1 for p in plays if (p['pick'] == 'OVER' and p['actual'] > p['edge'] + p['actual'] - p['edge']) or
                   (p['pick'] == 'UNDER' and p['actual'] < p['edge'] + p['actual'] - p['edge']))

        # Actually, let's recalculate properly
        # The "line" is the actual total (proxy). Edge = projected - line.
        # If edge > 0, pick OVER. Win if actual > line. But actual == line, so this is circular.
        # We need a different approach - use the residual directly.

        # Better: for each game, if model projects X and actual is Y:
        # If we bet OVER at line Y: win if actual > Y. But actual == Y always. This doesn't work.
        # We need actual closing lines from odds data.

        print(f"  {threshold:>6.2f}  {len(plays):>6}  (need actual closing lines for win/loss)")

    print()
    print("  ⚠️  Cannot calculate win rate/ROI without actual closing odds lines.")
    print("  The dataset has season-level aggregates, not game-level odds.")
    print("  To complete this analysis, need either:")
    print("    1. Historical closing totals from The Odds API (paid plan)")
    print("    2. Or use the odds already in the DuckDB if available")
    print()

    # ── RESIDUAL BY MONTH ──
    print("─" * 70)
    print("  RESIDUAL BY MONTH")
    print("─" * 70)
    by_month = defaultdict(list)
    for r in results:
        month = r['date'][:7] if r['date'] else 'unknown'
        by_month[month].append(r['residual'])
    for month in sorted(by_month.keys()):
        resids = by_month[month]
        if len(resids) >= 10:
            mean = statistics.mean(resids)
            print(f"  {month}: mean={mean:+.3f} (n={len(resids)})")
    print()

    # ── BIAS BY HOME PARK ──
    print("─" * 70)
    print("  BIAS BY HOME PARK (top over / top under)")
    print("─" * 70)
    by_park = defaultdict(list)
    for r in results:
        by_park[r['home']].append(r['residual'])
    park_biases = []
    for park, resids in by_park.items():
        if len(resids) >= 30:
            park_biases.append((park, statistics.mean(resids), len(resids)))
    park_biases.sort(key=lambda x: x[1], reverse=True)
    print("  Top 5 over-biased:")
    for park, mean, n in park_biases[:5]:
        print(f"    {park:>5}: mean={mean:+.3f} (n={n})")
    print("  Top 5 under-biased:")
    for park, mean, n in park_biases[-5:]:
        print(f"    {park:>5}: mean={mean:+.3f} (n={n})")

    print()
    print("=" * 70)
    if mean_res > 0.15:
        print(f"  VERDICT: OVER bias of +{mean_res:.3f} runs/game")
    elif mean_res < -0.15:
        print(f"  VERDICT: UNDER bias of {mean_res:.3f} runs/game")
    else:
        print(f"  VERDICT: Roughly centered (mean={mean_res:+.3f})")
    print("=" * 70)


if __name__ == '__main__':
    main()
