# MLB O/U XGBoost Training Pipeline — Design Plan

## Current State

- **DuckDB** at `xgb/mlb.duckdb` stores games, team hitting, Statcast batting/pitching, pitcher logs, odds, predictions, and results.
- **Scraper** (`xgb/scrape.py`) collects schedules, probable starters, team hitting, Statcast batting/pitching, pitcher logs, rosters, odds, and weather.
- **Training** (`xgb/train.py`) builds a leakage-aware feature matrix and trains the XGBoost residual model.
- **Daily cycle** (`xgb/daily_bot.py`) settles prior picks, updates the README record, scrapes today's card, retrains, generates picks, and can send the Telegram card.
- **Production record** is tracked in `xgb/data/performance.json`, `xgb/data/picks_YYYY-MM-DD.json`, and the README performance block.

## What `train.py` Does

### 1. Feature Engineering (Alpha Layer)

The raw DB columns are the starting point. We derive **engineered features** that capture the tactician edges the market misses:

#### A. Starting Pitcher Mismatch Score
- `starter_fip_diff` = away_starter_fip - home_starter_fip (positive = home pitcher is better)
- `starter_xera_diff` = away_starter_xera - home_starter_xera
- `starter_k_minus_bb` = (starter_k_pct - starter_bb_pct) for each side, then diff
- `starter_brl_against_diff` = away_starter_brl - home_starter_brl
- `starter_fb_velo_diff` = away_fb_velo - home_fb_velo

#### B. Team Offense Power Index
- `team_ops_diff` = away_ops - home_ops
- `team_xwoba_diff` = away_team_xwoba - home_team_xwoba
- `team_brl_diff` = away_team_brl - home_team_brl
- `team_ev_diff` = away_team_ev - home_team_ev
- `combined_xwoba` = (away_xwoba + home_xwoba) / 2 (total offensive context)
- `combined_brl` = (away_brl + home_brl) / 2

#### C. Bullpen Quality
- `bpen_xera_diff` = away_bpen_xera - home_bpen_xera
- `bpen_brl_diff` = away_bpen_brl - home_bpen_brl

#### D. Park & Environment
- `park_factor` (raw from DB)
- `park_altitude` = stadium_alt
- `park_is_dome` = stadium_roof
- `log_10_park_factor` = log(park_factor) for non-linear effect

#### E. Composite Features
- `projected_away_score` — simple FIP-based estimate: (league_avg_runs / away_starter_fip) * park_factor
- `projected_home_score` — same for home
- `projected_total` = projected_away + projected_home
- `pitcher_contact_quality` = away_starter_brl + home_starter_brl (both starters allow barrels)

### 2. Target Variable

`total_runs` = away_score + home_score (already in DB)

### 3. Model Architecture

**XGBoost Regressor** with:

- **Objective**: `reg:squarederror` (predict total runs)
- **Evaluation metrics**: RMSE, MAE, R²
- **Hyperparameters** (starting point, tuned via CV):
  ```python
  {
      'n_estimators': 500,
      'max_depth': 4-6,          # shallow to avoid overfitting on small data
      'learning_rate': 0.05,
      'subsample': 0.8,
      'colsample_bytree': 0.8,
      'min_child_weight': 5,     # regularization for small dataset
      'reg_alpha': 0.1,          # L1 regularization
      'reg_lambda': 1.0,         # L2 regularization
      'early_stopping_rounds': 50
  }
  ```

### 4. Cross-Validation Strategy

**TimeSeriesSplit** — critical for sports data! Never train on future data to predict past:

```
Split 1: Train [2025-04-22]           → Test [2026-04-17]
Split 2: Train [2025-04-22, 04-17]    → Test [2026-04-18]
Split 3: Train [2025-04-22, 04-17-18] → Test [2026-04-19]
...
```

This mimics real deployment: you only know past data when predicting today.

### 5. Output

- **Trained model**: `xgb/model.json` (XGBoost native format)
- **Feature importances**: printed + saved to `xgb/feature_importance.json`
- **CV results**: RMSE, MAE, R² per fold + overall
- **Prediction analysis**: predicted vs actual scatter, error distribution
- **Calibration check**: are predicted totals well-calibrated?

### 6. Edge Detection

After training, the model outputs a predicted total. Compare to market line:
- `edge = model_prediction - market_line`
- If `abs(edge) >= 0.5`: actionable signal (OVER or UNDER)
- Confidence tiers: >1.0 run edge = high, 0.5-1.0 = medium

### 7. Dependencies

```
xgboost
scikit-learn
duckdb
pandas
numpy
```

## File Structure After Implementation

```
SportsBotv2/
├── xgb/
│   ├── db.py             # DuckDB backend
│   ├── scrape.py         # Daily scraper
│   ├── weather.py        # Weather enrichment/backfill
│   ├── train.py          # Training pipeline
│   ├── pick_today.py     # Daily pick generator
│   ├── daily_bot.py      # Daily settle/train/pick/send cycle
│   ├── recordkeeping.py  # Settlement, ROI, README performance updates
│   ├── model/            # Trained model + metadata
│   ├── data/             # Scraped games, picks, performance, cached API data
│   └── mlb.duckdb        # Database
├── config/
│   └── teams.json      # Team/stadium data (existing)
└── TRAINING_PLAN.md    # This file
```

## Phase 3: Output & Usability ✅ READY

Phase 3 for the XGBoost path is about making the daily model output easy to consume, audit, and compare over time. The main card and recordkeeping are already in place; the remaining work is polish and export.

### 3.1 — Daily Picks Output
- [x] `xgb/pick_today.py YYYY-MM-DD` writes `xgb/data/picks_YYYY-MM-DD.json`
- [x] Daily output includes matchup, pick, line, model prediction, edge, and confidence
- [x] Games without usable odds stay out of the actionable card instead of using fake lines
- [x] `xgb/daily_bot.py` formats a Telegram-safe daily card

### 3.2 — Performance Reporting
- [x] `xgb/recordkeeping.py` settles picks from final scores
- [x] `xgb/data/performance.json` stores overall, daily, confidence, and pick-type performance
- [x] README performance block is updated from settled results
- [x] ROI and units are calculated at -110 juice
- [x] `xgb/recordkeeping.py export-picks --out ...` and `export-daily --out ...` export settled picks and daily performance to CSV

### 3.3 — Phase 3 Implementation Notes
- CSV exports are derived from `xgb/data/performance.json`.
- Commands: `python xgb/recordkeeping.py export-picks --out xgb/data/picks.csv` and `python xgb/recordkeeping.py export-daily --out xgb/data/daily.csv`.
- Do not edit generated historical pick JSON by hand; use recordkeeping utilities as the source of truth.

## Phase 4: Hardening ✅ READY

Phase 4 is about making the XGBoost path boring to operate every day: clear validation, recoverable failures, and no accidental leakage.

### 4.1 — Data And Leakage Guards
- [x] Training uses completed games only
- [x] Feature joins use historical data before the game date where required
- [x] Weather values are filled with neutral indoor defaults for roofed stadiums
- [x] Missing odds are not converted into synthetic market lines
- [x] Production tests cover API key handling, odds windows, final-score guards, weather persistence, and leakage-prone feature exclusions
- [x] `xgb/preflight.py` reports DB date range, latest odds date, latest weather coverage, model artifact age, and pending settlements

### 4.2 — Runtime Hardening
- [x] Daily cycle supports `--skip-scrape`, `--skip-train`, `--settle-only`, and `--no-telegram`
- [x] Telegram send can be dry-run before external delivery
- [x] NWS weather backfill records unavailable historical observations instead of retrying forever
- [x] Add strict `YYYY-MM-DD` validation to `scrape.py`, `pick_today.py`, `weather.py`, `daily_bot.py`, and `recordkeeping.py`
- [x] Add structured log files under `xgb/logs/` for the daily cycle
- [ ] Make daily cycle continue to settlement/reporting when noncritical enrichments fail

### 4.3 — Phase 4 Implementation Notes
- Keep betting decisions blocked when odds or starter context is missing; output should say why the game is skipped.
- Prefer explicit `NO ODDS`, `NO STARTER`, `NO MODEL`, or `STALE ODDS` statuses over silent omission.
- Add tests for every new hardening behavior before relying on it in the daily cron.

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Small or uneven sample | Heavy regularization, shallow trees, residual target, temporal validation |
| Data gaps across seasons | Time-aware joins and validation; report DB coverage in preflight |
| Overfitting | Early stopping, CV, feature importance review |
| Missing odds/starter/weather features | Skip betting picks when required context is absent; use neutral weather only for roofed/unavailable environments |
| Lookahead leakage | ASOF joins, completed-game filters, and production tests for excluded columns |
