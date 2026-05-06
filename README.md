# SportsBotv2 MLB O/U

SportsBotv2 is a production-ready MLB over/under modeling workflow built around scraped sportsbook totals, MLB game data, pitcher/team features, DuckDB storage, and an XGBoost residual model. The system is designed to generate daily MLB totals picks while avoiding obvious target and market leakage.

The current MLB model uses market totals as the baseline and trains on the residual between the final game total and the posted total. That keeps the model anchored to the betting market while allowing it to identify edges from schedule, team, pitcher, park, and recent-form features.

## Performance Snapshot

<!-- SPORTSBOTV2_RECORD_START -->

**Overall record:** 53-30-3  
**Win rate:** 63.9%  
**ROI:** +20.0 units / +24.1% at -110 juice

## Full Record By Day

| Date | Record | Win % | Picks |
| --- | --- | ---: | ---: |
| Apr 23 | 1-3 | 25.0% | 4 |
| Apr 24 | 5-3 | 62.5% | 8 |
| Apr 25 | 4-2 | 66.7% | 6 |
| Apr 26 | 6-5 | 54.5% | 11 |
| Apr 27 | 1-1 | 50.0% | 2 |
| Apr 28 | 7-2 | 77.8% | 9 |
| Apr 29 | 4-2-1 | 66.7% | 7 |
| Apr 30 | 5-0-1 | 100.0% | 6 |
| May 1 | 8-1 | 88.9% | 9 |
| May 2 | 7-4-1 | 63.6% | 12 |
| May 3 | 3-4 | 42.9% | 7 |
| May 4 | 2-2 | 50.0% | 4 |
| May 5 | 0-1 | 0.0% | 1 |

## Record By Pick Type

| Pick Type | Record | Win % |
| --- | --- | ---: |
| Over | 27-16 | 62.8% |
| Under | 26-14 | 65.0% |

## Record By Confidence

| Confidence | Record | Win % |
| --- | --- | ---: |
| Low | 16-9 | 64.0% |
| Medium | 28-15-1 | 65.1% |
| High | 9-6-2 | 60.0% |

<!-- SPORTSBOTV2_RECORD_END -->

## What The Model Does

- Scrapes MLB schedules, game results, probable starters, team stats, pitcher data, market totals, and game weather.
- Stores historical and current-season data in DuckDB for repeatable training and auditing.
- Trains an XGBoost model on completed games only.
- Excludes final scores, final totals, market odds columns, and other leakage-prone fields from the feature set.
- Adds `api.weather.gov` observations or hourly forecasts for outdoor games, with neutral indoor values for roofed stadiums.
- Treats games without usable market odds as `NO ODDS` instead of fabricating a fallback betting line.
- Produces daily over/under picks with confidence buckets and edge estimates.

## Repository Layout

```text
xgb/
  scrape.py          # Daily MLB schedule/results and odds ingestion
  weather.py         # api.weather.gov forecast/observation backfill
  sbr_scrape.py      # SBR odds scraper for historical/current lines
  train.py           # XGBoost residual model training
  pick_today.py      # Daily pick generation
  auto_cycle.sh      # Daily scrape/train/pick automation
  install_cron.sh    # Cron installer for daily automation
  mlb.duckdb         # Included DuckDB database
  data/              # Scraped games, picks, rosters, Statcast, and team data
  model/             # Trained XGBoost model and metadata

tests/
  test_xgb_production.py
```

## Setup

Create a virtual environment and install dependencies:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create a local `.env` file from the example:

```bash
cp .env.example .env
```

Add your private API keys to `.env`. The real `.env` file is intentionally ignored by Git and should never be committed.

`api.weather.gov` does not require a weather API key. Set `NWS_USER_AGENT` in `.env` to an identifying string with contact information, which is what the National Weather Service asks API clients to send.

## Daily Workflow

Run a one-day scrape:

```bash
.venv/bin/python xgb/scrape.py --date 2026-05-05 --season 2026
```

Train the model:

```bash
.venv/bin/python xgb/train.py
```

Generate picks for a date:

```bash
.venv/bin/python xgb/pick_today.py 2026-05-05
```

Backfill weather:

```bash
.venv/bin/python xgb/weather.py --retry-missing --max-observation-age-days 14
```

Older outdoor games that are no longer available from `api.weather.gov` are marked as `api.weather.gov:observations:unavailable` instead of being retried indefinitely. Domed or roofed parks are stored as neutral indoor weather.

Run the automated daily cycle:

```bash
cd xgb
./auto_cycle.sh
```

Install the cron job:

```bash
cd xgb
./install_cron.sh
```

## Validation

The production test suite checks the important failure modes for this model:

- API keys are passed safely through request parameters.
- Daily odds windows are based on the Eastern local MLB day.
- Live scores are not treated as final training targets.
- Missing odds are not converted into fake betting lines.
- NWS weather calls send an identifying `User-Agent` and no API key.
- Weather values are persisted in DuckDB and exposed to the model feature matrix.
- XGBoost features exclude score, target, odds, and market leakage columns.

Run the tests with:

```bash
.venv/bin/python -m unittest tests.test_xgb_production -v
```

## Notes

This repository includes the model database and generated artifacts needed to inspect and reproduce the current MLB workflow. Private credentials are not included. Past performance does not guarantee future betting results.
