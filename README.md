# SportsBotv2 MLB Totals Model

SportsBotv2 is an MLB over/under modeling workflow for daily totals picks. It combines sportsbook totals, MLB schedule and result data, team offense, pitcher quality, Statcast signals, park context, and game weather in a DuckDB-backed XGBoost pipeline.

The production model is market anchored: FanDuel totals are used as the baseline, and XGBoost learns the residual edge between the market number and the final game total. Market columns are stored for comparison and pick generation, but they are excluded from model features to avoid simply training the model to copy the book.

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

## Current Tuned Model

The current artifact in `xgb/model/ou_xgb.json` was retrained on May 7, 2026 after Weatherbit historical weather backfill. The tuned model uses XGBoost residual regression with a stricter `2.0` run edge threshold for daily picks.

| Evaluation | Value |
| --- | ---: |
| Training games | 3,692 |
| Holdout games | 923 |
| Holdout period | Aug 25, 2025 to May 6, 2026 |
| Model MAE | 3.6076 |
| Market MAE | 3.4475 |
| Tuned pick threshold | 2.0 runs |
| Holdout picks at threshold | 160 |
| Holdout record | 91-64-5 |
| Holdout ROI | +20.6 units / +12.08% |

The all-game MAE remains market dominated, which is expected for totals. The tuned objective is not to beat the market line on every game; it is to identify high-conviction residual edges. At the tuned threshold, the model produces fewer but stronger picks.

Top SHAP features in the tuned model include starter workload and quality, walk-rate differential, contact profile, and weather context. `weather_run_environment` is now a top-five feature after the Weatherbit backfill.

## Data And Weather Coverage

The included DuckDB file is `xgb/mlb.duckdb`.

| Weather source | Games |
| --- | ---: |
| Weatherbit historical hourly | 3,025 |
| Indoor/roof-neutral | 1,348 |
| api.weather.gov observations | 77 |
| api.weather.gov hourly forecast | 7 |
| Remaining historical unavailable | 525 |
| Remaining missing | 62 |

Weatherbit is the preferred provider when `WEATHERBIT_API_KEY` is configured. `api.weather.gov` remains a no-key fallback for observations and forecasts. Older rows can be resumed with:

```bash
.venv/bin/python xgb/weather.py --retry-unavailable --sleep 0
```

If Weatherbit returns a long retry window, the command exits with `rate_limited: true` and can be rerun later.

## Leakage Controls

SportsBotv2 is built around point-in-time training behavior:

- Final scores and final totals are never model features.
- Market totals and odds are used for residual target construction and edge comparison, not as XGBoost inputs.
- Daily prediction joins use strict ASOF logic: source stat rows must be before the game date.
- Live game scores are not ingested as completed training targets.
- Missing odds produce `NO ODDS`; no fallback betting line is fabricated.
- Weather API keys are passed through request parameters or `.env`, never embedded in URLs or committed files.

## Repository Layout

```text
config/                 Stadium and team configuration
src/                    Node/OpenClaw integration utilities
tests/                  Production regression tests
xgb/
  db.py                 DuckDB schema and ingestion helpers
  scrape.py             MLB schedule, stats, pitcher, and odds ingestion
  weather.py            Weatherbit and api.weather.gov weather backfill
  train.py              XGBoost residual model training and evaluation
  pick_today.py         Daily pick generation
  daily_bot.py          Daily scrape/train/pick/send workflow
  recordkeeping.py      Settled-pick tracking and README record updates
  preflight.py          Operational readiness report
  mlb.duckdb            Included model database
  model/                Trained model and metadata
```

## Setup

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Fill `.env` locally:

```text
ODDS_API_KEY=
WEATHERBIT_API_KEY=
NWS_USER_AGENT=SportsBotv2 MLB Weather (your-email@example.com)
OPENCLAW_TELEGRAM_TARGET=
```

The real `.env` is intentionally ignored by Git and must not be committed.

## Common Commands

Run production tests:

```bash
.venv/bin/python -m unittest tests.test_xgb_production -v
```

Run preflight:

```bash
.venv/bin/python xgb/preflight.py --db-path xgb/mlb.duckdb
```

Scrape one MLB date:

```bash
.venv/bin/python xgb/scrape.py --date 2026-05-06 --season 2026
```

Backfill or resume weather:

```bash
.venv/bin/python xgb/weather.py --retry-unavailable --sleep 0
```

Train the tuned XGBoost residual model:

```bash
.venv/bin/python xgb/train.py
```

Generate picks for a date:

```bash
.venv/bin/python xgb/pick_today.py 2026-05-06
```

Run the full daily bot:

```bash
.venv/bin/python xgb/daily_bot.py --date 2026-05-06
```

## Operational Notes

- `xgb/model/model_metadata.json` records the feature list, tuned XGBoost params, leakage policy, held-out model metrics, and held-out betting metrics.
- `xgb/data/performance.json` is the source for the live record block in this README.
- `xgb/install_cron.sh` installs the daily Telegram workflow without removing unrelated cron jobs.
- Private credentials are excluded. Generated database and model artifacts are included so the current state can be audited and reproduced.

Past performance does not guarantee future betting results.
