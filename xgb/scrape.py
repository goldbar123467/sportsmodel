#!/usr/bin/env python3
"""
Daily MLB Statcast + Stats scraper.
Pulls advanced metrics for XGBoost model training and prediction.

Sources:
  - MLB Stats API (free): team hitting, pitcher logs, rosters, schedules
  - Statcast via pybaseball: barrel%, xwOBA, exit velo, hard hit%, pitcher xERA, FB spin

Usage:
  python scrape.py                    # Scrape today's data
  python scrape.py --date 2025-04-15  # Scrape a specific date
  python scrape.py --backfill 30      # Scrape last 30 days
"""

import json
import os
import sys
import time
import argparse
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

warnings.filterwarnings('ignore')

import requests
import pandas as pd

from db import Database

# Paths
SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True)

# Load team mappings
CONFIG_DIR = SCRIPT_DIR.parent / 'config'
with open(CONFIG_DIR / 'teams.json') as f:
    TEAMS_DATA = json.load(f)
TEAM_IDS = {v['abbr']: int(k) for k, v in TEAMS_DATA['teams'].items()}
ID_TO_ABBR = {int(k): v['abbr'] for k, v in TEAMS_DATA['teams'].items()}

MLB_API = 'https://statsapi.mlb.com/api/v1'
# Load API key from .env
ENV_PATH = SCRIPT_DIR.parent / '.env'
def load_odds_api_key():
    """Load The Odds API key from env first, then .env."""
    api_key = os.environ.get('ODDS_API_KEY', '')
    if api_key or not ENV_PATH.exists():
        return api_key

    for line in ENV_PATH.read_text().splitlines():
        if line.startswith('ODDS_API_KEY='):
            return line.split('=', 1)[1].strip()
    return ''


ODDS_API_KEY = load_odds_api_key()
ODDS_API_URL = 'https://api.the-odds-api.com/v4/sports/baseball_mlb/odds'
DEFAULT_LOCAL_TZ = 'America/New_York'

HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; MLB-Scraper/1.0)'}


# ─── MLB Stats API helpers ──────────────────────────────────────────────────

def mlb_get(endpoint, params=None):
    """Fetch from MLB Stats API with retry."""
    url = f'{MLB_API}{endpoint}'
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=20)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                wait = int(r.headers.get('Retry-After', 5))
                print(f'  ⏳ Rate limited, waiting {wait}s...')
                time.sleep(wait)
                continue
            print(f'  ⚠️  MLB API {r.status_code}: {endpoint}')
            return {}
        except requests.RequestException as e:
            if attempt < 2:
                time.sleep(1 * (attempt + 1))
            else:
                print(f'  ❌ MLB API failed: {e}')
                return {}
    return {}


def odds_commence_window_utc(date_str, local_tz=DEFAULT_LOCAL_TZ):
    """Return UTC commence bounds for one local baseball date."""
    tz = ZoneInfo(local_tz)
    start_local = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=tz)
    end_local = start_local + timedelta(days=1) - timedelta(seconds=1)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)
    return (
        start_utc.strftime('%Y-%m-%dT%H:%M:%SZ'),
        end_utc.strftime('%Y-%m-%dT%H:%M:%SZ'),
    )


def fetch_odds(date_str, api_key=None, session=requests, local_tz=DEFAULT_LOCAL_TZ):
    """
    Fetch FanDuel totals (O/U) from The Odds API for a given date.
    Free tier: only current/upcoming games. Historical requires paid plan.
    """
    api_key = api_key or ODDS_API_KEY
    if not api_key:
        print('  ⚠️  No ODDS_API_KEY set, skipping odds')
        return []

    commence_from, commence_to = odds_commence_window_utc(date_str, local_tz)
    params = {
        'apiKey': api_key,
        'regions': 'us',
        'bookmakers': 'fanduel',
        'markets': 'totals',
        'oddsFormat': 'american',
        'dateFormat': 'iso',
        'commenceTimeFrom': commence_from,
        'commenceTimeTo': commence_to,
    }

    try:
        r = session.get(ODDS_API_URL, params=params, headers=HEADERS, timeout=20)
        if r.status_code == 401:
            print('  ⚠️  Odds API: invalid/expired key')
            return []
        if r.status_code == 429:
            print('  ⏳ Odds API: rate limited')
            return []
        if r.status_code != 200:
            print(f'  ⚠️  Odds API {r.status_code}')
            return []

        data = r.json()
        odds_list = []
        for game in data:
            away = game.get('away_team', '')
            home = game.get('home_team', '')
            game_pk = game.get('id', '')

            for bookmaker in game.get('bookmakers', []):
                if bookmaker.get('key') != 'fanduel':
                    continue
                for market in bookmaker.get('markets', []):
                    if market.get('key') != 'totals':
                        continue

                    outcomes = market.get('outcomes', [])
                    over_odds = None
                    under_odds = None
                    total_line = None

                    for o in outcomes:
                        if o.get('name') == 'Over':
                            over_odds = o.get('price')
                            total_line = o.get('point')
                        elif o.get('name') == 'Under':
                            under_odds = o.get('price')

                    if total_line is not None:
                        odds_list.append({
                            'date': date_str,
                            'game_pk': game_pk,
                            'away_team': away,
                            'home_team': home,
                            'bookmaker': 'fanduel',
                            'market': 'totals',
                            'total_line': total_line,
                            'over_odds': over_odds,
                            'under_odds': under_odds,
                        })

        remaining = r.headers.get('x-requests-remaining')
        quota = f' (quota remaining: {remaining})' if remaining is not None else ''
        print(f'  💰 Got odds for {len(odds_list)} games{quota}')
        return odds_list

    except requests.RequestException as e:
        print(f'  ❌ Odds API failed: {e}')
        return []


def _team_name_to_abbr_map():
    """Return full-name to abbreviation mapping for external odds feeds."""
    name_to_abbr = {v['name']: k for k, v in TEAMS_DATA['teams'].items()}
    name_to_abbr.update(TEAMS_DATA.get('nameToAbbr', {}))
    return name_to_abbr


def ingest_odds_rows(db, date_str, game_records, odds_data):
    """Match Odds API rows to MLB schedule games and insert FanDuel totals."""
    name_to_abbr = _team_name_to_abbr_map()
    matched = 0

    for o in odds_data:
        away_abbr = name_to_abbr.get(o['away_team'])
        home_abbr = name_to_abbr.get(o['home_team'])
        if not away_abbr or not home_abbr:
            continue

        matching_game = None
        for g in game_records:
            if g['away_team'] == away_abbr and g['home_team'] == home_abbr:
                matching_game = g['game_pk']
                break
        if matching_game is None:
            continue

        db.ingest_odds(
            date_str,
            matching_game,
            away_abbr,
            home_abbr,
            o['bookmaker'],
            o['market'],
            {
                'total_line': o['total_line'],
                'over_odds': o['over_odds'],
                'under_odds': o['under_odds'],
                'raw': o,
            },
        )
        matched += 1

    return matched


def scrape_odds_only(date_str):
    """Fetch today's schedule and Odds API totals, then persist them to DuckDB."""
    print(f'\n🗓️  Scraping Odds API totals for {date_str}...')
    games = fetch_schedule(date_str)
    if not games:
        print('  No games found')
        return {'games': 0, 'odds': 0, 'matched': 0}

    game_records = []
    for g in games:
        record = {
            'date': g['date'],
            'game_pk': g['game_pk'],
            'away_team': g['away_team'],
            'home_team': g['home_team'],
            'away_pitcher': g['away_pitcher_name'],
            'home_pitcher': g['home_pitcher_name'],
            'away_pitcher_id': g['away_pitcher_id'],
            'home_pitcher_id': g['home_pitcher_id'],
            'venue': g['venue'],
            'status': g['status'],
            'away_score': g['away_score'],
            'home_score': g['home_score'],
            'total_runs': g['total_runs'],
            'park_factor': TEAMS_DATA.get('parkFactors', {}).get(g['home_team'], 1.0),
            'stadium': TEAMS_DATA.get('stadiums', {}).get(g['home_team'], {}),
        }
        game_records.append(record)

    odds_data = fetch_odds(date_str)

    db = Database()
    try:
        for record in game_records:
            db.ingest_game(record)
        matched = ingest_odds_rows(db, date_str, game_records, odds_data)
    finally:
        db.close()

    print(f'  💰 Matched {matched}/{len(odds_data)} odds to games')
    return {'games': len(game_records), 'odds': len(odds_data), 'matched': matched}


def fetch_team_hitting_stats(season=2025):
    """Fetch team-level season hitting stats from MLB API."""
    print(f'📊 Fetching team hitting stats ({season})...')
    teams_data = {}

    for abbr, team_id in TEAM_IDS.items():
        data = mlb_get(f'/teams/{team_id}/stats', {
            'stats': 'season',
            'season': season,
            'group': 'hitting',
            'gameType': 'R'
        })
        stats_list = data.get('stats', [])
        splits = stats_list[0].get('splits', []) if stats_list else []
        if splits:
            stat = splits[0].get('stat', {})
            pa = int(stat.get('plateAppearances', 0))
            if pa > 0:
                teams_data[abbr] = {
                    'pa': pa,
                    'avg': float(stat.get('avg', 0)),
                    'obp': float(stat.get('obp', 0)),
                    'slg': float(stat.get('slg', 0)),
                    'ops': float(stat.get('ops', 0)),
                    'hr': int(stat.get('homeRuns', 0)),
                    'r': int(stat.get('runs', 0)),
                    'bb': int(stat.get('baseOnBalls', 0)),
                    'k': int(stat.get('strikeOuts', 0)),
                    'sb': int(stat.get('stolenBases', 0)),
                    'h': int(stat.get('hits', 0)),
                    'ab': int(stat.get('atBats', 0)),
                    'doubles': int(stat.get('doubles', 0)),
                    'triples': int(stat.get('triples', 0)),
                    'rbi': int(stat.get('rbi', 0)),
                    'babip': 0,  # calculated below
                    'iso': 0,    # calculated below
                    'bb_rate': 0,
                    'k_rate': 0,
                    'hr_rate': 0,
                }
                t = teams_data[abbr]
                if t['ab'] > 0:
                    # BABIP = (H - HR) / (AB - K - HR + SF)
                    # Simplified: (H - HR) / (AB - K - HR + SAC_FLIES)
                    # We don't have SF from season stats, approximate
                    t['babip'] = round((t['h'] - t['hr']) / max(t['ab'] - t['k'] - t['hr'] + 20, 1), 3)
                    t['iso'] = round(t['slg'] - t['avg'], 3)
                    t['bb_rate'] = round(t['bb'] / t['pa'], 4)
                    t['k_rate'] = round(t['k'] / t['pa'], 4)
                    t['hr_rate'] = round(t['hr'] / t['pa'], 5)

        time.sleep(0.15)  # Be nice to the API

    print(f'  ✅ Got stats for {len(teams_data)} teams')
    return teams_data


def fetch_pitcher_game_log(pitcher_id, season=2025):
    """Fetch a pitcher's game log for the season."""
    data = mlb_get(f'/people/{pitcher_id}/stats', {
        'stats': 'gameLog',
        'season': season,
        'group': 'pitching'
    })
    stats_list = data.get('stats', [])
    splits = stats_list[0].get('splits', []) if stats_list else []
    starts = []
    for s in splits:
        stat = s.get('stat', {})
        ip = float(stat.get('inningsPitched', 0))
        if ip > 0:
            starts.append({
                'date': s.get('date', ''),
                'ip': ip,
                'er': int(stat.get('earnedRuns', 0)),
                'h': int(stat.get('hits', 0)),
                'bb': int(stat.get('baseOnBalls', 0)),
                'k': int(stat.get('strikeOuts', 0)),
                'hr': int(stat.get('homeRuns', 0)),
                'bf': int(stat.get('battersFaced', 0)),
                'pitches': int(stat.get('numberOfPitches', 0)),
                'is_home': s.get('isHome', False),
                'opponent': s.get('opponent', {}).get('name', ''),
            })
    return starts


def fetch_schedule(date_str):
    """Fetch MLB schedule for a date with probable pitchers."""
    data = mlb_get('/schedule', {
        'sportId': 1,
        'date': date_str,
        'hydrate': 'probablePitcher,venue,team,linescore',
        'gameType': 'R'
    })
    games = []
    for date_entry in data.get('dates', []):
        for g in date_entry.get('games', []):
            status = g.get('status', {}).get('abstractGameState', '')
            if status == 'Final':
                # Get actual score
                away_score = g.get('teams', {}).get('away', {}).get('score')
                home_score = g.get('teams', {}).get('home', {}).get('score')
            else:
                away_score = None
                home_score = None

            away_pitcher = g.get('teams', {}).get('away', {}).get('probablePitcher', {})
            home_pitcher = g.get('teams', {}).get('home', {}).get('probablePitcher', {})

            games.append({
                'game_pk': g.get('gamePk'),
                'date': date_str,
                'away_team': ID_TO_ABBR.get(g.get('teams', {}).get('away', {}).get('team', {}).get('id'), ''),
                'home_team': ID_TO_ABBR.get(g.get('teams', {}).get('home', {}).get('team', {}).get('id'), ''),
                'away_pitcher_id': away_pitcher.get('id'),
                'away_pitcher_name': away_pitcher.get('fullName', 'TBD'),
                'home_pitcher_id': home_pitcher.get('id'),
                'home_pitcher_name': home_pitcher.get('fullName', 'TBD'),
                'venue': g.get('venue', {}).get('name', ''),
                'status': status,
                'away_score': away_score,
                'home_score': home_score,
                'total_runs': (away_score + home_score) if away_score is not None and home_score is not None else None,
            })
    return games


# ─── Statcast helpers (pybaseball) ──────────────────────────────────────────

def fetch_statcast_batter_stats(season=2025):
    """Fetch Statcast batter percentile stats (xwOBA, barrel%, exit velo, etc.)."""
    print(f'📡 Fetching Statcast batter stats ({season})...')
    import pybaseball as pball

    try:
        df = pball.statcast_batter_percentile_ranks(str(season))
        stats = {}
        for _, row in df.iterrows():
            pid = int(row['player_id'])
            stats[pid] = {
                'xwoba': float(row.get('xwoba', 0) or 0),
                'xba': float(row.get('xba', 0) or 0),
                'xslg': float(row.get('xslg', 0) or 0),
                'xiso': float(row.get('xiso', 0) or 0),
                'brl_pct': float(row.get('brl_percent', 0) or 0),
                'exit_velo': float(row.get('exit_velocity', 0) or 0),
                'max_ev': float(row.get('max_ev', 0) or 0),
                'hard_hit_pct': float(row.get('hard_hit_percent', 0) or 0),
                'k_pct': float(row.get('k_percent', 0) or 0),
                'bb_pct': float(row.get('bb_percent', 0) or 0),
                'whiff_pct': float(row.get('whiff_percent', 0) or 0),
                'chase_pct': float(row.get('chase_percent', 0) or 0),
                'sprint_speed': float(row.get('sprint_speed', 0) or 0),
            }
        print(f'  ✅ Got Statcast for {len(stats)} batters')
        return stats
    except Exception as e:
        print(f'  ❌ Statcast batters failed: {e}')
        return {}


def fetch_statcast_pitcher_stats(season=2025):
    """Fetch Statcast pitcher percentile stats (xERA, barrel% against, FB spin, etc.)."""
    print(f'📡 Fetching Statcast pitcher stats ({season})...')
    import pybaseball as pball

    try:
        df = pball.statcast_pitcher_percentile_ranks(str(season))
        stats = {}
        for _, row in df.iterrows():
            pid = int(row['player_id'])
            stats[pid] = {
                'xwoba_against': float(row.get('xwoba', 0) or 0),
                'xba_against': float(row.get('xba', 0) or 0),
                'xslg_against': float(row.get('xslg', 0) or 0),
                'xera': float(row.get('xera', 0) or 0),
                'brl_pct_against': float(row.get('brl_percent', 0) or 0),
                'exit_velo_against': float(row.get('exit_velocity', 0) or 0),
                'hard_hit_against': float(row.get('hard_hit_percent', 0) or 0),
                'k_pct': float(row.get('k_percent', 0) or 0),
                'bb_pct': float(row.get('bb_percent', 0) or 0),
                'whiff_pct': float(row.get('whiff_percent', 0) or 0),
                'fb_velocity': float(row.get('fb_velocity', 0) or 0),
                'fb_spin': float(row.get('fb_spin', 0) or 0),
                'curve_spin': float(row.get('curve_spin', 0) or 0),
            }
        print(f'  ✅ Got Statcast for {len(stats)} pitchers')
        return stats
    except Exception as e:
        print(f'  ❌ Statcast pitchers failed: {e}')
        return {}


def fetch_team_rosters(season=2025):
    """Fetch active rosters for all teams, mapping player IDs to team abbreviations."""
    print(f'📋 Fetching team rosters ({season})...')
    player_to_team = {}

    for abbr, team_id in TEAM_IDS.items():
        data = mlb_get(f'/teams/{team_id}/roster', {
            'rosterType': 'active',
            'season': season,
            'hydrate': 'person'
        })
        for p in data.get('roster', []):
            person = p.get('person', {})
            pid = person.get('id')
            if pid:
                player_to_team[pid] = abbr
        time.sleep(0.1)

    print(f'  ✅ Mapped {len(player_to_team)} players to teams')
    return player_to_team


def aggregate_statcast_to_teams(batter_stats, pitcher_stats, rosters):
    """Aggregate individual Statcast stats to team-level averages."""
    print('🔢 Aggregating Statcast to team level...')

    def safe_avg(values):
        """Average a list, ignoring None/NaN/0 values."""
        valid = []
        for v in values:
            if v is None:
                continue
            try:
                import math
                if math.isnan(float(v)):
                    continue
            except (TypeError, ValueError):
                continue
            if v != 0:
                valid.append(v)
        return round(sum(valid) / len(valid), 3) if valid else 0

    # Batter aggregation: average xwOBA, barrel%, etc. per team
    team_batters = {}
    for pid, team in rosters.items():
        if pid in batter_stats and team not in ('', None):
            if team not in team_batters:
                team_batters[team] = []
            team_batters[team].append(batter_stats[pid])

    team_batting_agg = {}
    for team, players in team_batters.items():
        if not players:
            continue
        n = len(players)
        team_batting_agg[team] = {
            'avg_xwoba': safe_avg([p['xwoba'] for p in players]),
            'avg_brl_pct': safe_avg([p['brl_pct'] for p in players]),
            'avg_exit_velo': safe_avg([p['exit_velo'] for p in players]),
            'avg_hard_hit': safe_avg([p['hard_hit_pct'] for p in players]),
            'avg_sprint': safe_avg([p['sprint_speed'] for p in players]),
            'player_count': n,
        }

    # Pitcher aggregation
    team_pitchers = {}
    for pid, team in rosters.items():
        if pid in pitcher_stats and team not in ('', None):
            if team not in team_pitchers:
                team_pitchers[team] = []
            team_pitchers[team].append(pitcher_stats[pid])

    team_pitching_agg = {}
    for team, pitchers in team_pitchers.items():
        if not pitchers:
            continue
        n = len(pitchers)
        team_pitching_agg[team] = {
            'avg_xera': safe_avg([p['xera'] for p in pitchers]),
            'avg_brl_against': safe_avg([p['brl_pct_against'] for p in pitchers]),
            'avg_ev_against': safe_avg([p['exit_velo_against'] for p in pitchers]),
            'avg_fb_velo': safe_avg([p['fb_velocity'] for p in pitchers]),
            'avg_fb_spin': safe_avg([p['fb_spin'] for p in pitchers]),
            'pitcher_count': n,
        }

    print(f'  ✅ Aggregated Statcast for {len(team_batting_agg)} teams (batting) / {len(team_pitching_agg)} teams (pitching)')
    return team_batting_agg, team_pitching_agg


# ─── Main scraper ────────────────────────────────────────────────────────────

def scrape_date(date_str, season=2025):
    """Scrape all data for a single date. Returns a list of game dicts."""
    print(f'\n🗓️  Scraping {date_str}...')

    # 1. Get schedule
    games = fetch_schedule(date_str)
    if not games:
        print('  No games found')
        return []

    print(f'  Found {len(games)} games')

    # 2. Get team hitting stats (cached per season)
    cache_file = DATA_DIR / f'team_hitting_{season}.json'
    if cache_file.exists():
        with open(cache_file) as f:
            team_hitting = json.load(f)
        print(f'  📂 Loaded cached team hitting ({len(team_hitting)} teams)')
    else:
        team_hitting = fetch_team_hitting_stats(season)
        with open(cache_file, 'w') as f:
            json.dump(team_hitting, f, indent=2)

    # 3. Get Statcast data (cached per season)
    sc_batter_cache = DATA_DIR / f'statcast_batters_{season}.json'
    sc_pitcher_cache = DATA_DIR / f'statcast_pitchers_{season}.json'
    roster_cache = DATA_DIR / f'rosters_{season}.json'

    if sc_batter_cache.exists():
        with open(sc_batter_cache) as f:
            statcast_batters = {int(k): v for k, v in json.load(f).items()}
        print(f'  📂 Loaded cached Statcast batters ({len(statcast_batters)})')
    else:
        statcast_batters = fetch_statcast_batter_stats(season)
        with open(sc_batter_cache, 'w') as f:
            json.dump(statcast_batters, f)

    if sc_pitcher_cache.exists():
        with open(sc_pitcher_cache) as f:
            statcast_pitchers = {int(k): v for k, v in json.load(f).items()}
        print(f'  📂 Loaded cached Statcast pitchers ({len(statcast_pitchers)})')
    else:
        statcast_pitchers = fetch_statcast_pitcher_stats(season)
        with open(sc_pitcher_cache, 'w') as f:
            json.dump(statcast_pitchers, f)

    if roster_cache.exists():
        with open(roster_cache) as f:
            rosters = json.load(f)
        # Convert string keys back to int
        rosters = {int(k): v for k, v in rosters.items()}
        print(f'  📂 Loaded cached rosters ({len(rosters)} players)')
    else:
        rosters = fetch_team_rosters(season)
        with open(roster_cache, 'w') as f:
            json.dump({str(k): v for k, v in rosters.items()}, f)

    # 4. Aggregate Statcast to team level
    team_sc_bat, team_sc_pit = aggregate_statcast_to_teams(
        statcast_batters, statcast_pitchers, rosters
    )

    # 5. Get pitcher game logs for probable starters
    pitcher_logs = {}
    pitcher_ids_needed = set()
    for g in games:
        if g['away_pitcher_id']:
            pitcher_ids_needed.add(g['away_pitcher_id'])
        if g['home_pitcher_id']:
            pitcher_ids_needed.add(g['home_pitcher_id'])

    print(f'  📈 Fetching {len(pitcher_ids_needed)} pitcher logs...')
    for pid in pitcher_ids_needed:
        log_cache = DATA_DIR / f'pitcher_{pid}_{season}.json'
        if log_cache.exists():
            with open(log_cache) as f:
                pitcher_logs[pid] = json.load(f)
        else:
            starts = fetch_pitcher_game_log(pid, season)
            pitcher_logs[pid] = starts
            with open(log_cache, 'w') as f:
                json.dump(starts, f)
        time.sleep(0.15)

    # 6. Open database
    db = Database()

    # 7. Build game records and ingest
    game_records = []
    for g in games:
        away_log = pitcher_logs.get(g['away_pitcher_id'], [])
        home_log = pitcher_logs.get(g['home_pitcher_id'], [])

        # Calculate pitcher FIP from game log
        def calc_fip(logs):
            if not logs:
                return None
            total_ip = sum(s['ip'] for s in logs)
            total_hr = sum(s['hr'] for s in logs)
            total_bb = sum(s['bb'] for s in logs)
            total_k = sum(s['k'] for s in logs)
            total_er = sum(s['er'] for s in logs)
            if total_ip < 1:
                return None
            fip = ((13 * total_hr) + (3 * total_bb) - (2 * total_k)) / total_ip + 3.20
            era = (total_er * 9) / total_ip
            return {
                'ip': total_ip,
                'fip': round(fip, 2),
                'era': round(era, 2),
                'k': total_k,
                'bb': total_bb,
                'hr': total_hr,
                'games': len(logs),
                'avg_pitches': round(sum(s.get('pitches', 0) for s in logs) / len(logs), 0) if logs else 0,
                'avg_ip': round(total_ip / len(logs), 1) if logs else 0,
            }

        away_pitcher_fip = calc_fip(away_log)
        home_pitcher_fip = calc_fip(home_log)

        # Get Statcast pitcher data
        away_sc = statcast_pitchers.get(g['away_pitcher_id'], {})
        home_sc = statcast_pitchers.get(g['home_pitcher_id'], {})

        # Build record
        record = {
            'date': g['date'],
            'game_pk': g['game_pk'],
            'away_team': g['away_team'],
            'home_team': g['home_team'],
            'away_pitcher': g['away_pitcher_name'],
            'home_pitcher': g['home_pitcher_name'],
            'away_pitcher_id': g['away_pitcher_id'],
            'home_pitcher_id': g['home_pitcher_id'],
            'venue': g['venue'],
            'status': g['status'],
            'away_score': g['away_score'],
            'home_score': g['home_score'],
            'total_runs': g['total_runs'],

            # Team hitting stats
            'away_hitting': team_hitting.get(g['away_team'], {}),
            'home_hitting': team_hitting.get(g['home_team'], {}),

            # Team Statcast aggregates
            'away_statcast_bat': team_sc_bat.get(g['away_team'], {}),
            'home_statcast_bat': team_sc_bat.get(g['home_team'], {}),
            'away_statcast_pit': team_sc_pit.get(g['away_team'], {}),
            'home_statcast_pit': team_sc_pit.get(g['home_team'], {}),

            # Probable pitcher FIP/ERA
            'away_starter': away_pitcher_fip,
            'home_starter': home_pitcher_fip,

            # Individual Statcast pitcher data
            'away_starter_sc': away_sc,
            'home_starter_sc': home_sc,

            # Park info
            'park_factor': TEAMS_DATA.get('parkFactors', {}).get(g['home_team'], 1.0),
            'stadium': TEAMS_DATA.get('stadiums', {}).get(g['home_team'], {}),
        }
        game_records.append(record)

    # 8. Fetch odds for this date
    odds_data = fetch_odds(date_str)

    # 9. Ingest into database
    for record in game_records:
        db.ingest_game(record)

    matched = ingest_odds_rows(db, date_str, game_records, odds_data)
    print(f'  💰 Matched {matched}/{len(odds_data)} odds to games')

    print(f'  ✅ Built {len(game_records)} game records, ingested into DB')
    db.close()
    return game_records


def main():
    parser = argparse.ArgumentParser(description='MLB Statcast + Stats daily scraper')
    parser.add_argument('--date', type=str, help='Date to scrape (YYYY-MM-DD)')
    parser.add_argument('--backfill', type=int, help='Scrape last N days')
    parser.add_argument('--season', type=int, default=2025, help='Season year')
    parser.add_argument('--odds-only', action='store_true', help='Only fetch schedule + Odds API totals')
    args = parser.parse_args()

    if args.odds_only:
        date_str = args.date or datetime.now().strftime('%Y-%m-%d')
        scrape_odds_only(date_str)

    elif args.backfill:
        # Scrape multiple dates
        all_records = []
        today = datetime.now()
        for i in range(args.backfill, 0, -1):
            d = (today - timedelta(days=i)).strftime('%Y-%m-%d')
            records = scrape_date(d, args.season)
            all_records.extend(records)
            time.sleep(1)  # Be nice between dates

        # Save combined dataset
        outfile = DATA_DIR / f'dataset_{args.season}_{today.strftime("%Y%m%d")}.json'
        with open(outfile, 'w') as f:
            json.dump(all_records, f, indent=2)
        print(f'\n📁 Saved {len(all_records)} game records to {outfile}')

        # Show summary
        with_runs = [r for r in all_records if r.get('total_runs') is not None]
        print(f'  Games with scores: {len(with_runs)}')
        if with_runs:
            avg = sum(r['total_runs'] for r in with_runs) / len(with_runs)
            print(f'  Avg total runs: {avg:.2f}')

    elif args.date:
        records = scrape_date(args.date, args.season)
        outfile = DATA_DIR / f'games_{args.date}.json'
        with open(outfile, 'w') as f:
            json.dump(records, f, indent=2)
        print(f'\n📁 Saved {len(records)} records to {outfile}')

    else:
        # Default: scrape today
        today = datetime.now().strftime('%Y-%m-%d')
        records = scrape_date(today, args.season)
        outfile = DATA_DIR / f'games_{today}.json'
        with open(outfile, 'w') as f:
            json.dump(records, f, indent=2)
        print(f'\n📁 Saved {len(records)} records to {outfile}')


if __name__ == '__main__':
    main()
