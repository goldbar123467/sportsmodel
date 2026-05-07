#!/usr/bin/env python3
"""
International Soccer Scraper — Uses Wikipedia + GitHub datasets.
Pulls international match results 2020–present for XGBoost training.

Sources:
  - jfjelstul/worldcup (GitHub) — all World Cup matches
  - Wikipedia — Euros, Copa America, AFCON, Asian Cup, Gold Cup, Nations League
  - FIFA rankings (GitHub)

Usage:
  python scrape.py                  # Scrape all configured competitions
  python scrape.py --stats          # Show database stats only
"""

import json
import os
import sys
import time
import argparse
import io
import re
import warnings
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
warnings.filterwarnings('ignore')

import requests
import pandas as pd
import numpy as np

from db import Database

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
    'Accept': 'text/html',
}

# ─── Wikipedia competition pages ───────────────────────────────────────

# Each competition: (Wikipedia title, start_year, tournament_name)
COMPETITIONS = [
    # ── FIFA World Cup (from jfjelstul dataset) ──
    # Handled separately below

    # ── Continental Championships ──
    ('UEFA_Euro_2020', 2021, 'UEFA Euro'),  # played in 2021
    ('UEFA_Euro_2024', 2024, 'UEFA Euro'),

    ('2021_Copa_America', 2021, 'Copa America'),
    ('2024_Copa_America', 2024, 'Copa America'),

    ('2021_Africa_Cup_of_Nations', 2022, 'Africa Cup of Nations'),  # played Jan 2022
    ('2023_Africa_Cup_of_Nations', 2024, 'Africa Cup of Nations'),  # played Jan 2024

    ('2023_AFC_Asian_Cup', 2024, 'AFC Asian Cup'),  # played Jan 2024

    ('2021_CONCACAF_Gold_Cup', 2021, 'CONCACAF Gold Cup'),
    ('2023_CONCACAF_Gold_Cup', 2023, 'CONCACAF Gold Cup'),

    # ── UEFA Nations League ──
    ('2020–21_UEFA_Nations_League', 2020, 'UEFA Nations League'),
    ('2022–23_UEFA_Nations_League', 2022, 'UEFA Nations League'),
    ('2024–25_UEFA_Nations_League', 2024, 'UEFA Nations League'),

    # ── CONCACAF Nations League ──
    ('2019–20_CONCACAF_Nations_League', 2020, 'CONCACAF Nations League'),
    ('2022–23_CONCACAF_Nations_League', 2022, 'CONCACAF Nations League'),
    ('2023–24_CONCACAF_Nations_League', 2023, 'CONCACAF Nations League'),
    ('2024–25_CONCACAF_Nations_League', 2024, 'CONCACAF Nations League'),

    # ── World Cup Qualifying (sample pages) ──
    # UEFA 2022 qualifiers
    ('2022_FIFA_World_Cup_qualification_(UEFA)', 2021, 'World Cup Qual UEFA'),
    # CONMEBOL 2022 qualifiers
    ('2022_FIFA_World_Cup_qualification_(CONMEBOL)', 2020, 'World Cup Qual CONMEBOL'),
]


def wiki_fetch(title):
    """Fetch Wikipedia page as HTML."""
    url = f'https://en.wikipedia.org/wiki/{title}'
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                return r.text
            print(f'  ⚠️  Wikipedia {r.status_code}: {title}')
            if attempt < 2:
                time.sleep(2)
        except Exception as e:
            print(f'  ❌ Wikipedia error: {e}')
            if attempt < 2:
                time.sleep(2)
    return None


def parse_wiki_match_tables(html, tournament, start_year):
    """Extract match results from Wikipedia footballbox divs.
    Wikipedia uses div.footballbox with <time> for date and fevent tables for teams/score.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')
    matches = []

    # Find all footballbox divs
    for fb in soup.find_all('div', class_='footballbox'):
        try:
            # 1. Extract date from <time> element
            time_el = fb.find('time')
            if not time_el:
                continue

            date_str = None
            # Look for YYYY-MM-DD in spans within time
            for span in time_el.find_all('span'):
                m = re.search(r'(\d{4}-\d{2}-\d{2})', span.get_text())
                if m:
                    date_str = m.group(1)
                    break
            if not date_str:
                # Parse from text like "11 June 2021"
                time_text = time_el.get_text()
                m = re.search(r'(\d{4}-\d{2}-\d{2})', time_text)
                if m:
                    date_str = m.group(1)
            if not date_str:
                continue

            # 2. Extract teams and score from the fevent table inside
            fevent = fb.find('table', class_='fevent')
            if not fevent:
                continue

            rows = fevent.find_all('tr')
            if not rows:
                continue

            # First row: [Team1, Score, Team2]
            cells = rows[0].find_all(['th', 'td'])
            if len(cells) < 3:
                continue

            home_team = clean_team_name(cells[0].get_text(' ', strip=True))
            score_text = cells[1].get_text(' ', strip=True)
            away_team = clean_team_name(cells[2].get_text(' ', strip=True))

            # Parse score "X–Y" or "X–Y (a.e.t.)"
            score_match = re.search(r'(\d+)\s*[–\-]\s*(\d+)', score_text)
            if not score_match:
                continue
            home_score = int(score_match.group(1))
            away_score = int(score_match.group(2))

            # 3. Extract venue from the text
            venue = ''
            full_text = fb.get_text(' ', strip=True)
            # Venue is typically after the goal details and before "Attendance"
            # Pattern: "... Stadium Name ... Attendance: ..."
            venue_match = re.search(r'\b([A-Z][\w\s]+(?:Stadium|Arena|Ground|Park|Field|Centre|Center|Stadion))\b', full_text)
            if venue_match:
                venue = venue_match.group(1).strip()

            # 4. Determine round from preceding headings
            round_name = ''
            prev_h = fb.find_previous(['h3', 'h4'])
            if prev_h:
                h_text = prev_h.get_text().strip()
                if any(kw in h_text.lower() for kw in ['group', 'round', 'semi', 'quarter', 'final', 'third']):
                    round_name = h_text

            # 5. Determine neutral venue
            # Most international tournaments are at neutral venues
            neutral = True
            if 'home' in full_text.lower() and 'away' not in full_text.lower():
                neutral = False

            match_id = hashlib.md5(
                f'{tournament}_{home_team}_{away_team}_{date_str}_{home_score}_{away_score}'.encode()
            ).hexdigest()[:12]

            matches.append({
                'match_id': f'wiki_{match_id}',
                'date': date_str,
                'home_team': home_team,
                'away_team': away_team,
                'tournament': tournament,
                'round': round_name,
                'venue': venue,
                'neutral': neutral,
                'home_score': home_score,
                'away_score': away_score,
                'total_goals': home_score + away_score,
                'status': 'Final',
                'stats': {},
            })

        except Exception as e:
            continue

    return matches


def clean_team_name(name):
    """Clean team names to consistent format."""
    name = re.sub(r'\s*\([^)]*\)', '', name)  # Remove parentheticals
    name = re.sub(r'\s*\[.*?\]', '', name)     # Remove brackets
    name = name.strip()
    # Common Wikipedia name mappings
    name_map = {
        'Czech Republic': 'Czechia',
        'Korea Republic': 'South Korea',
        'Korea DPR': 'North Korea',
        'IR Iran': 'Iran',
        'United States': 'USA',
        'Côte d\'Ivoire': 'Ivory Coast',
        'Congo DR': 'DR Congo',
    }
    return name_map.get(name, name)


def scrape_world_cup_data():
    """Scrape World Cup data from the jfjelstul GitHub dataset."""
    print('\n🌍 Fetching World Cup data (jfjelstul dataset)...')
    url = 'https://raw.githubusercontent.com/jfjelstul/worldcup/master/data-csv/matches.csv'
    try:
        import io
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code != 200:
            print(f'  ❌ HTTP {r.status_code}')
            return []

        df = pd.read_csv(io.StringIO(r.text))
        # Filter to 2020+
        df = df[df['match_date'] >= '2020-01-01']

        matches = []
        for _, row in df.iterrows():
            match = {
                'match_id': f'wc_{row["match_id"]}',
                'date': str(row['match_date'])[:10],
                'home_team': clean_team_name(row['home_team_name']),
                'away_team': clean_team_name(row['away_team_name']),
                'tournament': 'World Cup',
                'round': row['stage_name'],
                'venue': row['stadium_name'],
                'neutral': True,
                'home_score': int(row['home_team_score']),
                'away_score': int(row['away_team_score']),
                'total_goals': int(row['home_team_score']) + int(row['away_team_score']),
                'status': 'Final',
                'stats': {},
            }
            matches.append(match)

        print(f'  ✅ {len(matches)} World Cup matches')
        return matches

    except Exception as e:
        print(f'  ❌ Failed: {e}')
        return []


def scrape_fifa_rankings():
    """Try to load FIFA rankings from known sources."""
    print('\n🌍 Fetching FIFA rankings...')
    rankings = []

    # Try GitHub dataset
    try:
        url = 'https://raw.githubusercontent.com/martj42/international-football-results/main/data/fifa_rankings.csv'
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            df = pd.read_csv(io.StringIO(r.text))
            for _, row in df.iterrows():
                try:
                    rankings.append({
                        'date': str(row.get('rank_date', row.get('date', ''))),
                        'team': clean_team_name(str(row.get('country_full', row.get('team', '')))),
                        'rank': int(row.get('rank', 0)) if pd.notna(row.get('rank')) else None,
                        'points': float(row.get('total_points', 0)) if pd.notna(row.get('total_points', row.get('points'))) else None,
                        'confederation': str(row.get('confederation', '')),
                    })
                except (ValueError, TypeError):
                    continue
            print(f'  ✅ Loaded {len(rankings)} rankings')
            return rankings
    except Exception as e:
        print(f'  ⚠️  GitHub rankings failed: {e}')

    # Fallback: scrape current FIFA rankings from Wikipedia
    print('  Trying Wikipedia FIFA rankings...')
    try:
        html = wiki_fetch('FIFA_Men%27s_World_Ranking')
        if html:
            dfs = pd.read_html(io.StringIO(html))
            now = datetime.now().strftime('%Y-%m-%d')
            for df in dfs:
                cols = [str(c).lower() for c in df.columns]
                if 'team' in ' '.join(cols) and 'rank' in ' '.join(cols):
                    for _, row in df.iterrows():
                        try:
                            rank_col = next(i for i, c in enumerate(cols) if 'rank' in c.lower())
                            team_col = next(i for i, c in enumerate(cols) if 'team' in c.lower())
                            rankings.append({
                                'date': now,
                                'team': clean_team_name(str(row.iloc[team_col])),
                                'rank': int(row.iloc[rank_col]) if pd.notna(row.iloc[rank_col]) else None,
                                'points': None,
                                'confederation': '',
                            })
                        except (StopIteration, ValueError, TypeError, IndexError):
                            continue
            print(f'  ✅ Got {len(rankings)} rankings from Wikipedia')
            return rankings
    except Exception as e:
        print(f'  ⚠️  Wikipedia rankings failed: {type(e).__name__}: {str(e)[:200]}')

    print(f'  ⚠️  No rankings available ({len(rankings)} entries)')
    return rankings


def calculate_form(db, match_id, team, match_date):
    """Calculate pre-match form metrics."""
    recent = db.query("""
        SELECT home_team, away_team, home_score, away_score
        FROM matches
        WHERE (home_team = ? OR away_team = ?)
          AND date < ?
          AND total_goals IS NOT NULL
        ORDER BY date DESC
        LIMIT 20
    """, [team, team, match_date])

    if not recent:
        return {}

    def team_stats(ms, t):
        gf, ga = [], []
        for m in ms:
            if m['home_team'] == t:
                gf.append(m['home_score'] or 0)
                ga.append(m['away_score'] or 0)
            else:
                gf.append(m['away_score'] or 0)
                ga.append(m['home_score'] or 0)
        return gf, ga

    gf, ga = team_stats(recent, team)
    n = len(recent)

    def window_stats(gf_list, ga_list, window_size):
        w_gf = gf_list[:window_size]
        w_ga = ga_list[:window_size]
        if not w_gf:
            return {'gf': 0, 'ga': 0, 'wins': 0, 'draws': 0, 'losses': 0, 'clean': 0}
        wins = sum(1 for i in range(len(w_gf)) if w_gf[i] > w_ga[i])
        draws = sum(1 for i in range(len(w_gf)) if w_gf[i] == w_ga[i])
        losses = len(w_gf) - wins - draws
        clean = sum(1 for g in w_ga if g == 0)
        return {
            'gf': round(sum(w_gf) / len(w_gf), 2),
            'ga': round(sum(w_ga) / len(w_ga), 2),
            'wins': wins, 'draws': draws, 'losses': losses, 'clean': clean,
        }

    g5 = window_stats(gf, ga, 5)
    g10 = window_stats(gf, ga, min(10, n))

    rest_days = 7
    if len(recent) > 0:
        last_date = recent[0].get('date', '')
        if last_date:
            try:
                delta = (datetime.strptime(str(match_date)[:10], '%Y-%m-%d') -
                         datetime.strptime(str(last_date)[:10], '%Y-%m-%d'))
                rest_days = delta.days
            except (ValueError, TypeError):
                pass

    return {
        'g5_gf': g5['gf'], 'g5_ga': g5['ga'],
        'g5_xgf': g5['gf'], 'g5_xga': g5['ga'],  # proxy: actual goals
        'g5_wins': g5['wins'], 'g5_draws': g5['draws'],
        'g5_losses': g5['losses'], 'g5_clean': g5['clean'],
        'g10_gf': g10['gf'], 'g10_ga': g10['ga'],
        'g10_xgf': g10['gf'], 'g10_xga': g10['ga'],
        'rest_days': rest_days,
    }


def main():
    parser = argparse.ArgumentParser(description='Soccer Scraper — Wikipedia + GitHub')
    parser.add_argument('--no-form', action='store_true', help='Skip team form calculation')
    parser.add_argument('--stats', action='store_true', help='Show DB stats only')
    args = parser.parse_args()

    db = Database()

    if args.stats:
        summary = db.get_stats_summary()
        print(f'\n📊 Soccer DB: {db.db_path}')
        print(f'   Date range: {summary["date_range"]["min_date"]} → {summary["date_range"]["max_date"]}')
        for t, c in summary['tables'].items():
            print(f'   {t}: {c}')
        db.close()
        return

    all_matches = []

    # 1. World Cup from jfjelstul dataset (reliable)
    wc_matches = scrape_world_cup_data()
    all_matches.extend(wc_matches)

    # 2. Wikipedia competitions
    for wiki_title, start_year, tournament in COMPETITIONS:
        print(f'\n🔍 Scraping {tournament} ({start_year}) from Wikipedia...')
        html = wiki_fetch(wiki_title)
        if not html:
            print(f'  ❌ Failed to fetch')
            continue

        matches = parse_wiki_match_tables(html, tournament, start_year)
        print(f'  ✅ {len(matches)} matches')
        all_matches.extend(matches)
        time.sleep(1.5)  # Be nice to Wikipedia

    # 3. Deduplicate
    seen = set()
    unique = []
    for m in all_matches:
        key = (m['date'], m['home_team'], m['away_team'], m.get('tournament', ''))
        if key not in seen:
            seen.add(key)
            unique.append(m)
    all_matches = unique

    print(f'\n📊 Total unique matches: {len(all_matches)}')
    completed = [m for m in all_matches if m['total_goals'] is not None]
    print(f'   Completed: {len(completed)}')

    # 4. Ingest into database
    print('\n💾 Ingesting into database...')
    ingested = 0
    for match in all_matches:
        if match['total_goals'] is None:
            continue
        try:
            db.ingest_match(match)
            ingested += 1
        except Exception as e:
            print(f'  ⚠️  Ingest error: {e}')
    print(f'  ✅ Ingested {ingested} matches')

    # 5. Calculate form
    if not args.no_form and ingested > 0:
        print('\n📈 Calculating team form...')
        form_count = 0
        for match in all_matches:
            if match['total_goals'] is None:
                continue
            mid = match['match_id']
            mdate = match['date']

            home_form = calculate_form(db, mid, match['home_team'], mdate)
            if home_form:
                db.ingest_form(mid, match['home_team'], True, home_form)
                form_count += 1

            away_form = calculate_form(db, mid, match['away_team'], mdate)
            if away_form:
                db.ingest_form(mid, match['away_team'], False, away_form)
                form_count += 1

        print(f'  ✅ Stored {form_count} form entries')

    # 6. FIFA rankings
    rankings = scrape_fifa_rankings()
    r_count = 0
    for r in rankings:
        if r.get('rank') and r.get('team'):
            try:
                db.ingest_ranking(r['date'], r['team'], r['rank'],
                                  r.get('points'), r.get('confederation'))
                r_count += 1
            except Exception:
                pass
    if r_count:
        print(f'  ✅ Stored {r_count} rankings')

    # Save raw data
    outfile = DATA_DIR / f'soccer_matches_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    with open(outfile, 'w') as f:
        json.dump(all_matches, f, indent=2, default=str)
    print(f'\n📁 Saved to {outfile}')

    # Summary
    summary = db.get_stats_summary()
    print(f'\n📊 Database Summary:')
    print(f'   Date range: {summary["date_range"]["min_date"]} → {summary["date_range"]["max_date"]}')
    for table, count in summary['tables'].items():
        print(f'   {table}: {count} rows')

    db.close()


if __name__ == '__main__':
    main()
