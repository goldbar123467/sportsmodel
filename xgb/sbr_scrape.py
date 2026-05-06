#!/usr/bin/env python3
"""
SBR Odds Scraper — Historical totals from SportsbookReview.
Pulls FanDuel O/U lines from __NEXT_DATA__ embedded in SBR pages.

Usage:
    python sbr_scrape.py                    # Scrape today
    python sbr_scrape.py --date 2024-04-15  # Scrape specific date
    python sbr_scrape.py --backfill 365     # Scrape last N days
    python sbr_scrape.py --season 2024      # Scrape full season
"""

import json
import sys
import time
import argparse
from datetime import datetime, timedelta
from pathlib import Path

import requests
import duckdb

SCRIPT_DIR = Path(__file__).parent
DB_PATH = SCRIPT_DIR / 'mlb.duckdb'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# SBR sportsbook name → our DB bookmaker key
BOOK_MAP = {
    'fanduel': 'fanduel',
    'draftkings': 'draftkings',
    'betmgm': 'betmgm',
    'caesars': 'caesars',
    'bet365': 'bet365',
}

# SBR team full names → our abbreviations
NAME_TO_ABBR = {
    'Arizona Diamondbacks': 'ARI', 'Atlanta Braves': 'ATL', 'Baltimore Orioles': 'BAL',
    'Boston Red Sox': 'BOS', 'Chicago Cubs': 'CHC', 'Chicago White Sox': 'CWS',
    'Cincinnati Reds': 'CIN', 'Cleveland Guardians': 'CLE', 'Colorado Rockies': 'COL',
    'Detroit Tigers': 'DET', 'Houston Astros': 'HOU', 'Kansas City Royals': 'KC',
    'Los Angeles Angels': 'LAA', 'Los Angeles Dodgers': 'LAD', 'Miami Marlins': 'MIA',
    'Milwaukee Brewers': 'MIL', 'Minnesota Twins': 'MIN', 'New York Mets': 'NYM',
    'New York Yankees': 'NYY', 'Athletics': 'ATH', 'Oakland Athletics': 'OAK',
    'Philadelphia Phillies': 'PHI', 'Pittsburgh Pirates': 'PIT', 'San Diego Padres': 'SD',
    'San Francisco Giants': 'SF', 'Seattle Mariners': 'SEA', 'St. Louis Cardinals': 'STL',
    'Tampa Bay Rays': 'TB', 'Texas Rangers': 'TEX', 'Toronto Blue Jays': 'TOR',
    'Washington Nationals': 'WSH',
}


def fetch_sbr_totals(date_str):
    """
    Fetch MLB totals from SBR for a given date.
    Returns list of dicts with game info + odds per sportsbook.
    """
    url = f'https://www.sportsbookreview.com/betting-odds/mlb-baseball/totals/full-game/?date={date_str}'

    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code != 200:
            print(f'  ⚠️  SBR returned {r.status_code} for {date_str}')
            return []
    except requests.RequestException as e:
        print(f'  ❌ SBR request failed: {e}')
        return []

    html = r.text
    idx = html.find('__NEXT_DATA__')
    if idx < 0:
        print(f'  ⚠️  No __NEXT_DATA__ found for {date_str}')
        return []

    try:
        json_start = html.find('{', idx)
        json_end = html.find('</script>', json_start)
        data = json.loads(html[json_start:json_end])
        tables = data['props']['pageProps'].get('oddsTables', [])
        if not tables:
            return []
        games = tables[0]['oddsTableModel']['gameRows']
    except (json.JSONDecodeError, KeyError, IndexError):
        print(f'  ⚠️  Failed to parse SBR data for {date_str}')
        return []

    results = []
    for g in games:
        gv = g.get('gameView') or {}
        away_full = (gv.get('awayTeam') or {}).get('fullName', '')
        home_full = (gv.get('homeTeam') or {}).get('fullName', '')
        away_abbr = NAME_TO_ABBR.get(away_full, '')
        home_abbr = NAME_TO_ABBR.get(home_full, '')

        if not away_abbr or not home_abbr:
            continue

        game_pk = gv.get('gameId')
        away_score = gv.get('awayTeamScore')
        home_score = gv.get('homeTeamScore')
        total_runs = (away_score + home_score) if away_score is not None and home_score is not None else None

        # Extract odds from each sportsbook
        odds_views = g.get('oddsViews') or []
        book_odds = {}
        for ov in odds_views:
            if not ov:
                continue
            book = ov.get('sportsbook', '')
            if book not in BOOK_MAP:
                continue

            cl = ov.get('currentLine') or {}
            total = cl.get('total')
            over = cl.get('overOdds')
            under = cl.get('underOdds')

            if total is not None:
                book_odds[book] = {
                    'total_line': total,
                    'over_odds': over,
                    'under_odds': under,
                }

        if book_odds:
            results.append({
                'date': date_str,
                'game_pk': game_pk,
                'away_team': away_abbr,
                'home_team': home_abbr,
                'away_score': away_score,
                'home_score': home_score,
                'total_runs': total_runs,
                'odds': book_odds,
            })

    return results


def ingest_odds(date_str, results):
    """Ingest SBR odds into DuckDB, matching to existing games by teams."""
    conn = duckdb.connect(str(DB_PATH))

    # Get existing game_pks for this date
    existing = conn.execute(
        "SELECT game_pk, away_team, home_team FROM games WHERE date = ?", [date_str]
    ).fetchall()
    game_map = {(r[1], r[2]): r[0] for r in existing}

    inserted = 0
    for r in results:
        key = (r['away_team'], r['home_team'])
        game_pk = game_map.get(key, r.get('game_pk'))

        if game_pk is None:
            # Try reverse matchup (home/away might be flipped)
            key_rev = (r['home_team'], r['away_team'])
            game_pk = game_map.get(key_rev)

        if game_pk is None:
            continue

        for book, odds in r['odds'].items():
            conn.execute("""
                INSERT OR REPLACE INTO odds
                    (date, game_pk, away_team, home_team, bookmaker, market,
                     total_line, over_odds, under_odds)
                VALUES (?, ?, ?, ?, ?, 'totals', ?, ?, ?)
            """, [date_str, game_pk, r['away_team'], r['home_team'],
                  book, odds['total_line'], odds['over_odds'], odds['under_odds']])
            inserted += 1

    conn.close()
    return inserted


def main():
    parser = argparse.ArgumentParser(description='SBR MLB Totals Scraper')
    parser.add_argument('--date', type=str, help='Date to scrape (YYYY-MM-DD)')
    parser.add_argument('--backfill', type=int, help='Scrape last N days')
    parser.add_argument('--season', type=int, help='Scrape full season (April-October)')
    args = parser.parse_args()

    if args.season:
        # Scrape April through October of the given season
        dates = []
        start = datetime(args.season, 4, 1)
        end = datetime(args.season, 10, 31)
        current = start
        while current <= end:
            dates.append(current.strftime('%Y-%m-%d'))
            current += timedelta(days=1)
        print(f'🗓️  Scraping {args.season} season ({len(dates)} days)')

    elif args.backfill:
        dates = []
        today = datetime.now()
        for i in range(args.backfill, 0, -1):
            d = (today - timedelta(days=i)).strftime('%Y-%m-%d')
            dates.append(d)
        print(f'🗓️  Backfilling {len(dates)} days')

    elif args.date:
        dates = [args.date]

    else:
        dates = [datetime.now().strftime('%Y-%m-%d')]

    total_inserted = 0
    for i, date_str in enumerate(dates):
        print(f'[{i+1}/{len(dates)}] {date_str}...', end=' ', flush=True)
        results = fetch_sbr_totals(date_str)
        if results:
            inserted = ingest_odds(date_str, results)
            total_inserted += inserted
            print(f'{len(results)} games, {inserted} odds rows')
        else:
            print('no data')

        # Be nice to SBR
        time.sleep(1.5)

    print(f'\n✅ Done! Inserted {total_inserted} odds rows')


if __name__ == '__main__':
    main()
