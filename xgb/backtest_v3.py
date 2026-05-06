#!/usr/bin/env python3
"""
Backtest v3 — Full analysis with point-in-time pitcher stats + closing odds.
Outputs: mean residual, fatigue-segmented residuals, edge thresholds, ROI.
"""

import json
import sys
import glob
import statistics
from pathlib import Path
from collections import defaultdict
import duckdb

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / 'data'
CONFIG_DIR = SCRIPT_DIR.parent / 'config'

# Load config
with open(CONFIG_DIR / 'defaults.json') as f:
    CONFIG = json.load(f)

LG_AVG = CONFIG['leagueAvgRunsPerGame']
LG_AVG_PA = CONFIG['leagueAvgRunsPerPA']
FIP_CONSTANT = CONFIG['fipConstant']
FIP_WEIGHT = CONFIG['fipWeight']
ERA_WEIGHT = CONFIG['eraWeight']
MIN_PITCHER_IP = CONFIG.get('minPitcherIP', 30)
MIN_PITCHER_IP_PARTIAL = CONFIG.get('minPitcherIPPartial', 10)
MIN_TEAM_PA = CONFIG.get('minTeamPA', 200)
MIN_TEAM_PA_PARTIAL = CONFIG.get('minTeamPAPartial', 50)
PARTIAL_BLEND = CONFIG.get('partialDataBlend', 0.5)

with open(CONFIG_DIR / 'teams.json') as f:
    TEAMS_DATA = json.load(f)
PARK_FACTORS = TEAMS_DATA.get('parkFactors', {})

EDGE_THRESHOLDS = [0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0]


def load_pitcher_logs():
    """Load all pitcher game logs indexed by (pitcher_id, season)."""
    logs = {}
    for f in glob.glob(str(DATA_DIR / 'pitcher_*.json')):
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
    """Point-in-time pitcher stats using only starts before game_date."""
    key = (pitcher_id, season)
    starts = pitcher_logs.get(key, [])
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

    fip = ((13 * total_hr) + (3 * total_bb) - (2 * total_k)) / total_ip + FIP_CONSTANT
    era = (total_er * 9) / total_ip
    avg_pitches = total_pitches / games if games > 0 else 0

    rest_days = None
    if prior:
        from datetime import datetime
        try:
            d1 = datetime.strptime(prior[-1]['date'], '%Y-%m-%d')
            d2 = datetime.strptime(game_date, '%Y-%m-%d')
            rest_days = (d2 - d1).days
        except:
            pass

    return {
        'ip': total_ip, 'fip': fip, 'era': era,
        'k': total_k, 'bb': total_bb, 'hr': total_hr, 'er': total_er,
        'games': games, 'avg_pitch_count': avg_pitches,
        'rest_days': rest_days,
    }


def blend_with_lg_avg(stat, ip, lg_val):
    if ip >= MIN_PITCHER_IP:
        return stat
    if ip < MIN_PITCHER_IP_PARTIAL:
        return lg_val
    pct = ((ip - MIN_PITCHER_IP_PARTIAL) / (MIN_PITCHER_IP - MIN_PITCHER_IP_PARTIAL)) * PARTIAL_BLEND
    return stat * pct + lg_val * (1 - pct)


def rate_pitcher(pitcher_stats):
    if not pitcher_stats:
        return LG_AVG
    ip = pitcher_stats.get('ip', 0)
    fip = blend_with_lg_avg(pitcher_stats.get('fip', LG_AVG), ip, LG_AVG)
    era = blend_with_lg_avg(pitcher_stats.get('era', LG_AVG), ip, LG_AVG)
    return fip * FIP_WEIGHT + era * ERA_WEIGHT


def project_game(game, pitcher_logs):
    """Core projection with point-in-time pitcher stats."""
    home_team = game.get('home_team', '')
    park = PARK_FACTORS.get(home_team, 1.0)
    date = game.get('date', '')
    season = date[:4] if date else ''

    away_pid = game.get('away_pitcher_id')
    home_pid = game.get('home_pitcher_id')

    away_stats = get_pitcher_stats_before(pitcher_logs, away_pid, season, date) if away_pid else None
    home_stats = get_pitcher_stats_before(pitcher_logs, home_pid, season, date) if home_pid else None

    away_pitcher = rate_pitcher(away_stats)
    home_pitcher = rate_pitcher(home_stats)

    away_pitcher_adj = away_pitcher * park
    home_pitcher_adj = home_pitcher * park

    # Offense multiplier (season-level — best available)
    away_h = game.get('away_hitting', {})
    home_h = game.get('home_hitting', {})

    def get_off_mult(h):
        if not h:
            return 1.0
        pa = h.get('pa', 0)
        r = h.get('r', 0)
        if pa < MIN_TEAM_PA_PARTIAL:
            return 1.0
        rpa = r / pa
        rpa_b = blend_with_lg_avg(rpa, pa, LG_AVG_PA)
        return rpa_b / LG_AVG_PA

    away_off = get_off_mult(away_h)
    home_off = get_off_mult(home_h)

    away_runs = LG_AVG * away_off * (home_pitcher_adj / LG_AVG)
    home_runs = LG_AVG * home_off * (away_pitcher_adj / LG_AVG)
    base = away_runs + home_runs

    # Tactician adjustments
    tact = 0
    fatigue_state = 'unknown'
    if away_stats and home_stats:
        # Pitcher fatigue
        def pc_fatigue(stats):
            avg = stats.get('avg_pitch_count', 95)
            if avg >= 115: return 0.7
            if avg >= 105: return 0.4
            if avg >= 95: return 0.2
            if avg < 85: return -0.1
            return 0

        def tto_penalty(stats):
            if stats['games'] == 0: return 0
            avg_ip = stats['ip'] / stats['games']
            if avg_ip >= 6.5: return 0.4
            if avg_ip >= 5.5: return 0.2
            return 0

        def workload_decay(ip):
            if ip >= 200: return 0.2
            if ip >= 180: return 0.1
            if ip >= 140: return 0.05
            return 0

        def rest_adj(days):
            if days is None: return 0
            if days <= 2: return 0.3
            if days == 3: return 0.0
            if days == 4: return -0.1
            return -0.2

        away_fatigue = (pc_fatigue(away_stats) + tto_penalty(away_stats) +
                        workload_decay(away_stats['ip']) + rest_adj(away_stats.get('rest_days')))
        home_fatigue = (pc_fatigue(home_stats) + tto_penalty(home_stats) +
                        workload_decay(home_stats['ip']) + rest_adj(home_stats.get('rest_days')))

        tact = home_fatigue - away_fatigue

        avg_fat = (away_fatigue + home_fatigue) / 2
        if avg_fat >= 0.5:
            fatigue_state = 'tired'
        elif avg_fat <= 0.0:
            fatigue_state = 'rested'
        else:
            fatigue_state = 'normal'

    return base + tact, fatigue_state


def load_odds_from_duckdb():
    """Load closing totals from DuckDB, indexed by (date_str, away, home)."""
    db = duckdb.connect(str(SCRIPT_DIR / 'mlb.duckdb'), read_only=True)
    rows = db.execute("""
        SELECT CAST(o.date AS VARCHAR) as date_str, o.away_team, o.home_team,
               o.total_line, o.over_odds, o.under_odds
        FROM odds o
        WHERE o.total_line IS NOT NULL
          AND o.bookmaker = 'fanduel' AND o.market = 'totals'
    """).fetchall()
    db.close()

    odds_map = {}
    for row in rows:
        date_str, away, home, line, over_odds, under_odds = row
        odds_map[(date_str, away, home)] = {
            'line': float(line),
            'over_odds': over_odds, 'under_odds': under_odds,
        }
    return odds_map


def load_all_games():
    """Load all games with scores and pitcher IDs."""
    games = []
    for pattern in ['dataset_2024_*.json', 'dataset_2025_*.json', 'games_*.json']:
        for f in sorted(glob.glob(str(DATA_DIR / pattern))):
            with open(f) as fh:
                data = json.load(fh)
            for g in data:
                if (g.get('total_runs') is not None and
                    g.get('away_pitcher_id') and g.get('home_pitcher_id')):
                    games.append(g)
    return games


def print_stats(label, values):
    if not values:
        return
    mean = statistics.mean(values)
    median = statistics.median(values)
    stdev = statistics.stdev(values) if len(values) > 1 else 0
    se = stdev / (len(values) ** 0.5) if len(values) > 1 else 0
    print(f"  {label}: mean={mean:+.3f}, median={median:+.3f}, stdev={stdev:.3f}, se={se:.4f}, n={len(values)}")


def main():
    print("=" * 70)
    print("  MLB Projection Model — Backtest v3 (PIT stats + closing odds)")
    print("=" * 70)
    print()

    pitcher_logs = load_pitcher_logs()
    print(f"Loaded {len(pitcher_logs)} pitcher-season logs")

    games = load_all_games()
    print(f"Loaded {len(games)} games with scores + pitcher IDs")

    odds_map = load_odds_from_duckdb()
    print(f"Loaded {len(odds_map)} games with closing odds")
    print()

    # Run projections and match to odds
    results = []
    for g in games:
        date = g.get('date', '')
        away = g.get('away_team', '')
        home = g.get('home_team', '')
        odds = odds_map.get((date, away, home))
        if not odds:
            continue

        projected, fatigue_state = project_game(g, pitcher_logs)
        actual = g['total_runs']
        line = odds['line']
        edge = projected - line

        # Determine pick
        if abs(edge) >= 0:
            pick = 'OVER' if edge > 0 else 'UNDER'
        else:
            pick = 'NO_PLAY'

        # Determine result
        if actual > line:
            ou_result = 'OVER'
        elif actual < line:
            ou_result = 'UNDER'
        else:
            ou_result = 'PUSH'

        results.append({
            'date': date,
            'away': away,
            'home': home,
            'projected': projected,
            'actual': actual,
            'line': line,
            'edge': edge,
            'pick': pick,
            'ou_result': ou_result,
            'fatigue_state': fatigue_state,
            'over_odds': odds.get('over_odds', -110),
            'under_odds': odds.get('under_odds', -110),
        })

    print(f"Matched {len(results)} games with projections + closing odds")
    print()

    # ── 1. MEAN RESIDUAL ──
    residuals_model = [r['projected'] - r['actual'] for r in results]
    residuals_line = [r['line'] - r['actual'] for r in results]

    print("─" * 70)
    print("  1. MEAN RESIDUAL (projection - actual)")
    print("─" * 70)
    print_stats("Model projection - actual", residuals_model)
    print_stats("Closing line - actual", residuals_line)
    print()

    mean_model = statistics.mean(residuals_model)
    mean_line = statistics.mean(residuals_line)
    se_model = statistics.stdev(residuals_model) / (len(residuals_model) ** 0.5)
    t_model = mean_model / se_model if se_model > 0 else 0
    print(f"  Model bias: {mean_model:+.3f} (t={t_model:.2f})")
    print(f"  Market bias: {mean_line:+.3f}")
    print(f"  Model vs market: {mean_model - mean_line:+.3f}")
    print()

    # ── 2. RESIDUAL BY FATIGUE STATE ──
    print("─" * 70)
    print("  2. RESIDUAL BY PITCHER FATIGUE STATE")
    print("─" * 70)
    fatigue_groups = defaultdict(list)
    for r in results:
        fatigue_groups[r['fatigue_state']].append(r['projected'] - r['actual'])

    for state in ['rested', 'normal', 'tired', 'unknown']:
        resids = fatigue_groups.get(state, [])
        if resids:
            mean = statistics.mean(resids)
            med = statistics.median(resids)
            se = statistics.stdev(resids) / (len(resids) ** 0.5) if len(resids) > 1 else 0
            print(f"  {state:>8}: mean={mean:+.3f}, median={med:+.3f}, se={se:.4f}, n={len(resids)}")

    # Fatigue stacking test: residual by away pitcher rest days
    print()
    print("  By away pitcher rest days:")
    rest_groups = defaultdict(list)
    for r in results:
        # Get rest days from the game data
        rest_groups['all'].append(r['projected'] - r['actual'])

    # Split by edge direction
    over_games = [r for r in results if r['pick'] == 'OVER']
    under_games = [r for r in results if r['pick'] == 'UNDER']
    print(f"  OVER picks: n={len(over_games)}, mean edge={statistics.mean([r['edge'] for r in over_games]):+.3f}")
    print(f"  UNDER picks: n={len(under_games)}, mean edge={statistics.mean([r['edge'] for r in under_games]):+.3f}")

    # Residual for over picks vs under picks
    if over_games:
        over_resid = statistics.mean([r['projected'] - r['actual'] for r in over_games])
        print(f"  OVER picks residual: {over_resid:+.3f}")
    if under_games:
        under_resid = statistics.mean([r['projected'] - r['actual'] for r in under_games])
        print(f"  UNDER picks residual: {under_resid:+.3f}")
    print()

    # ── 3. EDGE THRESHOLDS + ROI ──
    print("─" * 70)
    print("  3. EDGE THRESHOLD ANALYSIS (ROI at -110)")
    print("─" * 70)
    print(f"  {'Edge':>6}  {'Games':>6}  {'W':>4}  {'L':>4}  {'P':>3}  {'Win%':>6}  {'ROI':>8}")
    print(f"  {'─'*6}  {'─'*6}  {'─'*4}  {'─'*4}  {'─'*3}  {'─'*6}  {'─'*8}")

    for threshold in EDGE_THRESHOLDS:
        plays = []
        for r in results:
            if abs(r['edge']) >= threshold:
                plays.append(r)

        if not plays:
            continue

        wins = 0
        losses = 0
        pushes = 0
        for p in plays:
            pick = p['pick']
            actual = p['actual']
            line = p['line']

            if pick == 'OVER':
                if actual > line: wins += 1
                elif actual < line: losses += 1
                else: pushes += 1
            elif pick == 'UNDER':
                if actual < line: wins += 1
                elif actual > line: losses += 1
                else: pushes += 1

        total = wins + losses
        win_pct = wins / total * 100 if total > 0 else 0

        # ROI at -110 odds (bet 110 to win 100)
        profit = wins * 100 - losses * 110
        cost = total * 110
        roi = profit / cost * 100 if cost > 0 else 0

        print(f"  {threshold:>6.2f}  {len(plays):>6}  {wins:>4}  {losses:>4}  {pushes:>3}  {win_pct:>5.1f}%  {roi:>+7.1f}%")

    print()

    # ── 4. RESIDUAL BUCKETS ──
    print("─" * 70)
    print("  4. RESIDUAL BUCKETS")
    print("─" * 70)
    buckets = [
        ("UNDER by 5+", -999, -5),
        ("UNDER by 4", -5, -4),
        ("UNDER by 3", -4, -3),
        ("UNDER by 2", -3, -2),
        ("UNDER by 1", -2, -1),
        ("Within ±1", -1, 1),
        ("OVER by 1", 1, 2),
        ("OVER by 2", 2, 3),
        ("OVER by 3", 3, 4),
        ("OVER by 4", 4, 5),
        ("OVER by 5+", 5, 999),
    ]
    for label, lo, hi in buckets:
        count = sum(1 for r in residuals_model if lo <= r < hi)
        pct = count / len(residuals_model) * 100
        bar = "█" * int(pct / 2)
        print(f"  {label:>20}: {count:4d} ({pct:5.1f}%) {bar}")
    print()

    # ── 5. BY PARK ──
    print("─" * 70)
    print("  5. BIAS BY HOME PARK")
    print("─" * 70)
    by_park = defaultdict(list)
    for r in results:
        by_park[r['home']].append(r['projected'] - r['actual'])
    park_biases = [(p, statistics.mean(rs), len(rs)) for p, rs in by_park.items() if len(rs) >= 20]
    park_biases.sort(key=lambda x: x[1], reverse=True)
    print("  Most over-projecting:")
    for p, m, n in park_biases[:5]:
        print(f"    {p:>5}: {m:+.3f} (n={n})")
    print("  Most under-projecting:")
    for p, m, n in park_biases[-5:]:
        print(f"    {p:>5}: {m:+.3f} (n={n})")

    print()
    print("=" * 70)
    if mean_model > 0.15:
        print(f"  VERDICT: OVER bias of +{mean_model:.3f}")
    elif mean_model < -0.15:
        print(f"  VERDICT: UNDER bias of {mean_model:.3f}")
    else:
        print(f"  VERDICT: Roughly centered ({mean_model:+.3f})")
    print("=" * 70)


if __name__ == '__main__':
    main()
