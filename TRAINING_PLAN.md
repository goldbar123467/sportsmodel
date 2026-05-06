# MLB O/U XGBoost Training Pipeline — Design Plan

## Current State

- **DuckDB** at `xgb/mlb.duckdb` with 109 completed games (target: `total_runs`)
- **Scraper** (`scrape.py`) collects: team hitting, Statcast batting/pitching, pitcher logs, pitcher Statcast
- **DB schema** has tables for all of the above plus odds, predictions, results
- **Date range**: 2025-04-22 + 2026-04-17 → 2026-04-23 (need more data for production)

## What `train.py` Will Do

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
│   ├── db.py           # DuckDB backend (existing)
│   ├── scrape.py       # Daily scraper (existing)
│   ├── train.py        # NEW — training pipeline
│   ├── model.json      # Trained model (generated)
│   └── mlb.duckdb      # Database (existing)
├── config/
│   └── teams.json      # Team/stadium data (existing)
└── TRAINING_PLAN.md    # This file
```

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Only 109 games — small sample | Heavy regularization, shallow trees, simple features first |
| Data gap (2025 → 2026) | TimeSeriesSplit respects chronology; note this in results |
| Overfitting | Early stopping, CV, feature importance review |
| Missing features (weather, rest days) | Add V2 features after baseline works |
