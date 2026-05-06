#!/usr/bin/env python3
"""
Backtest the projection model against historical games.
Calculates (projection - actual) residuals to detect over/under bias.

Reimplements the core projection logic from src/model/project.js in Python.
"""

import json
import sys
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
RECENT_FORM_WEIGHT = CONFIG.get('recentFormWeight', 1.5)
MIN_PITCHER_IP = CONFIG.get('minPitcherIP', 30)
MIN_PITCHER_IP_PARTIAL = CONFIG.get('minPitcherIPPartial', 10)
MIN_TEAM_PA = CONFIG.get('minTeamPA', 200)
MIN_TEAM_PA_PARTIAL = CONFIG.get('minTeamPAPartial', 50)
PARTIAL_BLEND = CONFIG.get('partialDataBlend', 0.5)

# Load park factors
with open(CONFIG_DIR / 'teams.json') as f:
    TEAMS_DATA = json.load(f)
PARK_FACTORS = TEAMS_DATA.get('parkFactors', {})


def calc_fip(ip, hr, bb, k):
    """Calculate FIP from counting stats."""
    if ip <= 0:
        return None
    return ((13 * hr) + (3 * bb) - (2 * k)) / ip + FIP_CONSTANT


def calc_era(ip, er):
    """Calculate ERA from counting stats."""
    if ip <= 0:
        return None
    return (er * 9) / ip


def blend_with_lg_avg(stat, ip, min_ip, min_partial, lg_val):
    """Blend a stat with league average when data is thin."""
    if ip >= min_ip:
        return stat
    if ip < min_partial:
        return None
    pct = ((ip - min_partial) / (min_ip - min_partial)) * PARTIAL_BLEND
    return stat * pct + lg_val * (1 - pct)


def rate_pitcher(pitcher_starter, pitcher_log_starts=None):
    """
    Rate a pitcher using the model's FIP/ERA blend.
    pitcher_starter: dict with ip, fip, era, k, bb, hr from the scraped data
    Returns: blended ERA (runs per game allowed)
    """
    if not pitcher_starter:
        return LG_AVG

    ip = pitcher_starter.get('ip', 0)
    if ip < MIN_PITCHER_IP_PARTIAL:
        return LG_AVG

    fip = pitcher_starter.get('fip')
    era = pitcher_starter.get('era')

    if fip is None:
        fip = LG_AVG
    if era is None:
        era = LG_AVG

    # Blend with league avg if thin data
    fip_blended = blend_with_lg_avg(fip, ip, MIN_PITCHER_IP, MIN_PITCHER_IP_PARTIAL, LG_AVG)
    era_blended = blend_with_lg_avg(era, ip, MIN_PITCHER_IP, MIN_PITCHER_IP_PARTIAL, LG_AVG)

    if fip_blended is None:
        fip_blended = LG_AVG
    if era_blended is None:
        era_blended = LG_AVG

    # Weighted blend: FIP + ERA
    blended = fip_blended * FIP_WEIGHT + era_blended * ERA_WEIGHT
    return blended


def rate_offense(team_hitting, is_home=False):
    """
    Rate a team's offense as a multiplier vs league average.
    Returns: multiplier (1.0 = average)
    """
    if not team_hitting:
        return 1.0

    pa = team_hitting.get('pa', 0)
    runs = team_hitting.get('r', 0) or team_hitting.get('runs', 0)

    if pa < MIN_TEAM_PA_PARTIAL:
        return 1.0

    rpa = runs / pa
    # Blend with league avg if thin
    rpa_blended = blend_with_lg_avg(rpa, pa, MIN_TEAM_PA, MIN_TEAM_PA_PARTIAL, LG_AVG_PA)
    if rpa_blended is None:
        rpa_blended = LG_AVG_PA

    multiplier = rpa_blended / LG_AVG_PA
    return multiplier


def project_game(game):
    """
    Run the core projection on a single game.
    Returns: projected total runs
    """
    home_team = game.get('home_team', '')
    park_factor = PARK_FACTORS.get(home_team, 1.0)

    # Rate pitchers (raw, before park adjustment)
    away_pitcher = rate_pitcher(game.get('away_starter'))
    home_pitcher = rate_pitcher(game.get('home_starter'))

    # Park-adjust pitcher ERAs
    away_pitcher_adj = away_pitcher * park_factor
    home_pitcher_adj = home_pitcher * park_factor

    # Rate offenses
    away_offense = rate_offense(game.get('away_hitting'), is_home=False)
    home_offense = rate_offense(game.get('home_hitting'), is_home=True)

    # Project runs
    away_runs = LG_AVG * away_offense * (home_pitcher_adj / LG_AVG)
    home_runs = LG_AVG * home_offense * (away_pitcher_adj / LG_AVG)

    total = away_runs + home_runs
    return round(total, 2)


def load_all_games():
    """Load all games with scores from the scraped data."""
    games = []

    # Load 2024 dataset
    ds24 = DATA_DIR / 'dataset_2024_20260425.json'
    if ds24.exists():
        with open(ds24) as f:
            data = json.load(f)
        for g in data:
            if g.get('total_runs') is not None:
                games.append(g)

    # Load 2025 dataset
    ds25 = DATA_DIR / 'dataset_2025_20260424.json'
    if ds25.exists():
        with open(ds25) as f:
            data = json.load(f)
        for g in data:
            if g.get('total_runs') is not None:
                games.append(g)

    # Load individual game files (2026)
    import glob
    for f in sorted(glob.glob(str(DATA_DIR / 'games_*.json'))):
        with open(f) as fh:
            data = json.load(fh)
        for g in data:
            if g.get('total_runs') is not None:
                games.append(g)

    return games


def analyze_residuals(games):
    """Run projections and analyze residual distribution."""
    residuals = []
    errors = []
    results_by_month = defaultdict(list)
    results_by_park = defaultdict(list)

    skipped = 0
    for g in games:
        # Skip games with TBD starters
        if not g.get('away_starter') or not g.get('home_starter'):
            skipped += 1
            continue

        projected = project_game(g)
        actual = g['total_runs']
        residual = projected - actual

        residuals.append(residual)
        errors.append(abs(residual))

        date = g.get('date', '')
        month = date[:7] if date else 'unknown'
        results_by_month[month].append(residual)

        home = g.get('home_team', 'UNK')
        results_by_park[home].append(residual)

    return residuals, errors, results_by_month, results_by_park, skipped


def print_stats(label, values):
    """Print summary statistics for a list of values."""
    if not values:
        print(f"  {label}: no data")
        return
    mean = statistics.mean(values)
    median = statistics.median(values)
    stdev = statistics.stdev(values) if len(values) > 1 else 0
    print(f"  {label}: mean={mean:+.3f}, median={median:+.3f}, stdev={stdev:.3f}, n={len(values)}")


def main():
    print("=" * 60)
    print("  MLB Projection Model — Over/Under Bias Backtest")
    print("=" * 60)
    print()

    games = load_all_games()
    print(f"Loaded {len(games)} games with scores")

    residuals, errors, by_month, by_park, skipped = analyze_residuals(games)
    print(f"Projected {len(residuals)} games (skipped {skipped} with TBD starters)")
    print()

    # Overall distribution
    print("─" * 60)
    print("  OVERALL RESIDUAL DISTRIBUTION")
    print("  (positive = model projects MORE runs than actual = OVER bias)")
    print("─" * 60)
    print_stats("Residual (proj - actual)", residuals)
    print_stats("Absolute error", errors)
    print()

    # Key metrics
    mean_res = statistics.mean(residuals)
    positive_pct = sum(1 for r in residuals if r > 0) / len(residuals) * 100
    print(f"  % of games where model over-predicts: {positive_pct:.1f}%")
    print(f"  % of games where model under-predicts: {100 - positive_pct:.1f}%")
    print()

    # Bias significance check
    if len(residuals) > 30:
        se = statistics.stdev(residuals) / (len(residuals) ** 0.5)
        t_stat = mean_res / se if se > 0 else 0
        print(f"  Standard error: {se:.4f}")
        print(f"  t-statistic: {t_stat:.2f}")
        if abs(t_stat) > 2.58:
            print(f"  ✗ SIGNIFICANT bias at 99% confidence (|t| > 2.58)")
        elif abs(t_stat) > 1.96:
            print(f"  ✗ SIGNIFICANT bias at 95% confidence (|t| > 1.96)")
        elif abs(t_stat) > 1.64:
            print(f"  ⚠ Marginally significant at 90% confidence")
        else:
            print(f"  ✓ No statistically significant bias detected")
    print()

    # Distribution buckets
    print("─" * 60)
    print("  RESIDUAL BUCKETS")
    print("─" * 60)
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

    # By park
    print("─" * 60)
    print("  BIAS BY HOME PARK (top 10 over-biased)")
    print("─" * 60)
    park_biases = []
    for park, resids in by_park.items():
        if len(resids) >= 20:
            park_biases.append((park, statistics.mean(resids), len(resids)))
    park_biases.sort(key=lambda x: x[1], reverse=True)
    for park, mean, n in park_biases[:10]:
        print(f"  {park:>5}: mean={mean:+.3f} (n={n})")
    print()
    print("  Top 10 under-biased:")
    for park, mean, n in park_biases[-10:]:
        print(f"  {park:>5}: mean={mean:+.3f} (n={n})")
    print()

    # By month (seasonality)
    print("─" * 60)
    print("  BIAS BY MONTH")
    print("─" * 60)
    for month in sorted(by_month.keys()):
        resids = by_month[month]
        if len(resids) >= 10:
            mean = statistics.mean(resids)
            print(f"  {month}: mean={mean:+.3f} (n={len(resids)})")

    print()
    print("=" * 60)
    if mean_res > 0.15:
        print(f"  VERDICT: Model has OVER bias of +{mean_res:.3f} runs/game")
        print(f"  → Fix the highest-leverage factor driving this")
    elif mean_res < -0.15:
        print(f"  VERDICT: Model has UNDER bias of {mean_res:.3f} runs/game")
    else:
        print(f"  VERDICT: Model residuals are roughly centered (mean={mean_res:+.3f})")
        print(f"  → No structural over bias detected — the 'bias' is likely a")
        print(f"     structural illusion from reading the code")
    print("=" * 60)


if __name__ == '__main__':
    main()
