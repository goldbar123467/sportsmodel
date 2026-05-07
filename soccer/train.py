#!/usr/bin/env python3
"""
International Soccer XGBoost Training Pipeline.
Trains a model to predict total goals (over/under) for international matches.

Features:
  - Team form (last 5/10 matches: goals, xG, wins)
  - Match stats from historical matches
  - FIFA rankings
  - Tournament context (World Cup > friendly)
  - Rest days, neutral venue

Usage:
  python train.py                    # Train with default params
  python train.py --tune 50          # Optuna hyperparameter tuning (50 trials)
  python train.py --predict 2026-05-01  # Predict matches for a date
"""

import json
import sys
import warnings
from pathlib import Path
from datetime import datetime

warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
import duckdb
import xgboost as xgb
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.preprocessing import LabelEncoder

from db import Database

SCRIPT_DIR = Path(__file__).parent
MODEL_DIR = SCRIPT_DIR / 'model'
MODEL_DIR.mkdir(exist_ok=True)


# ─── Feature Engineering ───────────────────────────────────────────────

def engineer_features(df):
    """Add derived features to the raw training dataframe."""
    df = df.copy()

    # ── Goal difference features ──
    df['diff_xg'] = df['home_xg'].fillna(0) - df['away_xg'].fillna(0)
    df['combined_xg'] = df['home_xg'].fillna(0) + df['away_xg'].fillna(0)
    df['diff_shots'] = df['home_shots'].fillna(0) - df['away_shots'].fillna(0)
    df['combined_shots'] = df['home_shots'].fillna(0) + df['away_shots'].fillna(0)
    df['diff_shots_on'] = df['home_shots_on'].fillna(0) - df['away_shots_on'].fillna(0)
    df['combined_shots_on'] = df['home_shots_on'].fillna(0) + df['away_shots_on'].fillna(0)

    # ── Form differentials ──
    df['form_gf_diff'] = df['home_g5_gf'].fillna(1.3) - df['away_g5_gf'].fillna(1.3)
    df['form_ga_diff'] = df['away_g5_ga'].fillna(1.3) - df['home_g5_ga'].fillna(1.3)
    df['form_xg_diff'] = df['home_g5_xgf'].fillna(1.3) - df['away_g5_xgf'].fillna(1.3)
    df['form_xga_diff'] = df['away_g5_xga'].fillna(1.3) - df['home_g5_xga'].fillna(1.3)

    # ── Combined form (total offensive context) ──
    df['combined_g5_gf'] = df['home_g5_gf'].fillna(1.3) + df['away_g5_gf'].fillna(1.3)
    df['combined_g5_xgf'] = df['home_g5_xgf'].fillna(1.3) + df['away_g5_xgf'].fillna(1.3)
    df['combined_g10_gf'] = df['home_g10_gf'].fillna(1.3) + df['away_g10_gf'].fillna(1.3)
    df['combined_g10_ga'] = df['home_g5_ga'].fillna(1.3) + df['away_g5_ga'].fillna(1.3)

    # ── Win form ──
    df['form_win_diff'] = df['home_g5_wins'].fillna(2) - df['away_g5_wins'].fillna(2)
    df['combined_wins'] = df['home_g5_wins'].fillna(2) + df['away_g5_wins'].fillna(2)

    # ── Ranking features ──
    df['rank_diff'] = (df['away_rank'].fillna(100) - df['home_rank'].fillna(100))
    df['rank_diff'] = df['rank_diff'].clip(-100, 100)  # cap extremes
    df['combined_rank'] = df['home_rank'].fillna(100) + df['away_rank'].fillna(100)
    df['home_is_favorite'] = (df['home_rank'].fillna(100) < df['away_rank'].fillna(100)).astype(int)

    # ── Rest / travel ──
    df['rest_diff'] = df['home_rest'].fillna(5) - df['away_rest'].fillna(5)
    df['min_rest'] = df[['home_rest', 'away_rest']].fillna(5).min(axis=1)
    df['home_short_rest'] = (df['home_rest'].fillna(5) < 4).astype(int)
    df['away_short_rest'] = (df['away_rest'].fillna(5) < 4).astype(int)

    # ── Clean sheet form (defensive strength) ──
    df['combined_clean'] = df['home_g5_clean'].fillna(1) + df['away_g5_clean'].fillna(1)

    # ── Neutral venue ──
    df['is_neutral'] = df.get('neutral', 0).fillna(0).astype(int)

    # ── Tournament importance (encode as ordinal) ──
    tournament_weight = {
        'World Cup': 10,
        'UEFA Euro': 9,
        'Copa America': 8,
        'Africa Cup of Nations': 7,
        'AFC Asian Cup': 7,
        'CONCACAF Gold Cup': 6,
        'UEFA Nations League': 5,
        'World Cup Qual': 5,
        'Friendlies (M)': 2,
    }
    df['tourney_importance'] = df.get('tournament', 'Friendlies (M)').map(
        lambda t: tournament_weight.get(t, 3) if isinstance(t, str) else 3
    ).fillna(3)

    # ── Odds-based features ──
    df['has_odds'] = df['odds_total'].notna().astype(int)

    return df


def select_features(df):
    """Select and order features for the model."""
    base_features = [
        # Match stats (when available — these are the "Statcast" of soccer)
        'home_xg', 'away_xg', 'diff_xg', 'combined_xg',
        'home_shots', 'away_shots', 'diff_shots', 'combined_shots',
        'home_shots_on', 'away_shots_on', 'diff_shots_on', 'combined_shots_on',
        'home_possession',

        # Form (last 5)
        'home_g5_gf', 'home_g5_ga', 'home_g5_xgf', 'home_g5_xga',
        'away_g5_gf', 'away_g5_ga', 'away_g5_xgf', 'away_g5_xga',
        'home_g5_wins', 'away_g5_wins',
        'home_g5_clean', 'away_g5_clean',

        # Form (last 10)
        'home_g10_gf', 'home_g10_ga', 'away_g10_gf', 'away_g10_ga',

        # Form differentials
        'form_gf_diff', 'form_ga_diff', 'form_xg_diff', 'form_xga_diff',
        'form_win_diff',
        'combined_g5_gf', 'combined_g5_xgf', 'combined_g10_gf', 'combined_g10_ga',
        'combined_wins', 'combined_clean',

        # Rankings
        'home_rank', 'away_rank', 'rank_diff', 'combined_rank', 'home_is_favorite',

        # Rest / travel
        'home_rest', 'away_rest', 'rest_diff', 'min_rest',
        'home_short_rest', 'away_short_rest',

        # Context
        'is_neutral', 'tourney_importance',

        # Odds (market signal)
        'odds_total', 'has_odds',
    ]

    # Only include features that exist in the dataframe
    available = [f for f in base_features if f in df.columns]
    missing = [f for f in base_features if f not in df.columns]
    if missing:
        print(f'  ⚠️  Missing features: {missing}')

    return df[available].fillna(0), available


# ─── Training ──────────────────────────────────────────────────────────

def train_model(X_train, y_train, X_test, y_test, params=None, eval_metric='mae'):
    """Train an XGBoost model and return it + metrics."""
    base_params = {
        'objective': 'reg:squarederror',
        'n_estimators': 400,
        'max_depth': 5,
        'learning_rate': 0.05,
        'min_child_weight': 5,
        'subsample': 0.8,
        'colsample_bytree': 0.8,
        'reg_alpha': 1.0,
        'reg_lambda': 1.0,
        'random_state': 42,
        'verbosity': 0,
    }

    if params:
        base_params.update(params)

    model = xgb.XGBRegressor(**base_params)
    model.fit(
        X_train, y_train,
        eval_set=[(X_test, y_test)],
        verbose=False,
    )

    # Predict
    train_pred = model.predict(X_train)
    test_pred = model.predict(X_test)

    # Metrics
    train_mae = mean_absolute_error(y_train, train_pred)
    test_mae = mean_absolute_error(y_test, test_pred)
    test_rmse = np.sqrt(mean_squared_error(y_test, test_pred))
    test_r2 = r2_score(y_test, test_pred)

    # Naive baseline (always predict mean of training set)
    naive_pred = np.full_like(y_test, y_train.mean())
    naive_mae = mean_absolute_error(y_test, naive_pred)

    metrics = {
        'train_mae': round(train_mae, 4),
        'test_mae': round(test_mae, 4),
        'test_rmse': round(test_rmse, 4),
        'test_r2': round(test_r2, 4),
        'naive_mae': round(naive_mae, 4),
        'better_than_naive': test_mae < naive_mae,
    }

    return model, metrics


def time_series_cv(df, features, target='total_goals', n_splits=5):
    """Time-series cross-validation for model evaluation."""
    df = df.sort_values('date').reset_index(drop=True)
    X = df[features].fillna(0).values
    y = df[target].values

    tscv = TimeSeriesSplit(n_splits=n_splits)
    fold_results = []

    for fold, (train_idx, test_idx) in enumerate(tscv.split(X)):
        X_train, X_test = X[train_idx], X[test_idx]
        y_train, y_test = y[train_idx], y[test_idx]

        model, metrics = train_model(X_train, y_train, X_test, y_test)
        fold_results.append({
            'fold': fold,
            'train_games': len(train_idx),
            'test_games': len(test_idx),
            **metrics,
        })

    return fold_results


# ─── Hyperparameter Tuning (Optuna) ────────────────────────────────────

def tune_hyperparameters(df, features, target='total_goals', n_trials=50):
    """Use Optuna to find the best hyperparameters."""
    try:
        import optuna
    except ImportError:
        print('Optuna not installed. Run: pip install optuna')
        return None, None

    print(f'\n🔧 Optuna hyperparameter tuning ({n_trials} trials)...')

    # Sort by date and split (80/20 time-based)
    df = df.sort_values('date').reset_index(drop=True)
    split_idx = int(len(df) * 0.8)
    train_df = df.iloc[:split_idx]
    val_df = df.iloc[split_idx:]

    X_train_full = train_df[features].fillna(0)
    y_train_full = train_df['total_goals']
    X_val = val_df[features].fillna(0)
    y_val = val_df['total_goals']

    # Use inner time-series split for CV within tuning
    tscv = TimeSeriesSplit(n_splits=3)

    def objective(trial):
        params = {
            'objective': 'reg:squarederror',
            'n_estimators': trial.suggest_int('n_estimators', 100, 800),
            'max_depth': trial.suggest_int('max_depth', 3, 8),
            'learning_rate': trial.suggest_float('learning_rate', 0.005, 0.2, log=True),
            'min_child_weight': trial.suggest_int('min_child_weight', 1, 20),
            'subsample': trial.suggest_float('subsample', 0.5, 1.0),
            'colsample_bytree': trial.suggest_float('colsample_bytree', 0.5, 1.0),
            'reg_alpha': trial.suggest_float('reg_alpha', 1e-3, 10.0, log=True),
            'reg_lambda': trial.suggest_float('reg_lambda', 1e-3, 10.0, log=True),
            'gamma': trial.suggest_float('gamma', 0, 5.0),
            'random_state': 42,
            'verbosity': 0,
        }

        # CV MAE
        fold_maes = []
        for train_idx, test_idx in tscv.split(X_train_full):
            X_tr, X_te = X_train_full.iloc[train_idx], X_train_full.iloc[test_idx]
            y_tr, y_te = y_train_full.iloc[train_idx], y_train_full.iloc[test_idx]

            model = xgb.XGBRegressor(**params)
            model.fit(X_tr, y_tr, verbose=False)
            pred = model.predict(X_te)
            fold_maes.append(mean_absolute_error(y_te, pred))

        return np.mean(fold_maes)

    study = optuna.create_study(direction='minimize')
    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)

    print(f'\n🏆 Best trial: MAE = {study.best_value:.4f}')
    print(f'   Params: {study.best_params}')

    # Retrain with best params on full train, evaluate on val
    best_params = {k: v for k, v in study.best_params.items()
                   if k not in ('objective', 'random_state', 'verbosity')}

    final_model, final_metrics = train_model(
        X_train_full.values, y_train_full.values,
        X_val.values, y_val.values,
        params=best_params,
    )

    print(f'\n📊 Final model on validation set:')
    print(f'   MAE: {final_metrics["test_mae"]:.4f}')
    print(f'   RMSE: {final_metrics["test_rmse"]:.4f}')
    print(f'   R²: {final_metrics["test_r2"]:.4f}')
    print(f'   Naive MAE: {final_metrics["naive_mae"]:.4f}')
    print(f'   Better than naive: {final_metrics["better_than_naive"]}')

    return study, final_model


# ─── Main ──────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Soccer XGBoost Training')
    parser.add_argument('--tune', type=int, default=0,
                        help='Number of Optuna tuning trials')
    parser.add_argument('--predict', type=str,
                        help='Predict matches for a specific date (YYYY-MM-DD)')
    parser.add_argument('--cv', action='store_true',
                        help='Run time-series cross-validation only')
    args = parser.parse_args()

    # Load data
    db = Database()
    summary = db.get_stats_summary()
    print(f'📊 Database: {summary["tables"]["matches"]} matches, '
          f'{summary["tables"]["match_stats"]} with stats')

    df = db.get_training_data()
    print(f'📋 Loaded {len(df)} matches for training')

    if len(df) < 50:
        print('❌ Not enough data for training (need 50+ matches)')
        db.close()
        return

    # Engineer features
    df = engineer_features(df)
    X, features = select_features(df)
    y = df['total_goals']

    # Filter to rows where we have at least basic features
    valid = X.notna().all(axis=1) & y.notna()
    df_clean = df[valid].copy()
    X_clean = X[valid]
    y_clean = y[valid]

    print(f'✅ {len(df_clean)} valid training samples with {len(features)} features')

    # Time-based split (80/20)
    df_clean = df_clean.sort_values('date').reset_index(drop=True)
    X_clean = X_clean.loc[df_clean.index]
    y_clean = y_clean.loc[df_clean.index]

    split_idx = int(len(df_clean) * 0.8)

    X_train = X_clean.iloc[:split_idx].values
    y_train = y_clean.iloc[:split_idx].values
    X_test = X_clean.iloc[split_idx:].values
    y_test = y_clean.iloc[split_idx:].values

    # Date ranges
    train_dates = df_clean.iloc[:split_idx]['date']
    test_dates = df_clean.iloc[split_idx:]['date']
    print(f'\n📅 Train: {train_dates.min()} → {train_dates.max()} ({len(X_train)} games)')
    print(f'📅 Test:  {test_dates.min()} → {test_dates.max()} ({len(X_test)} games)')

    # Cross-validation
    if args.cv:
        print('\n🔁 Time-Series Cross-Validation:')
        cv_results = time_series_cv(df_clean, features)
        for r in cv_results:
            print(f'  Fold {r["fold"]}: MAE={r["test_mae"]:.4f} | '
                  f'R²={r["test_r2"]:.4f} | '
                  f'Beat naive: {r["better_than_naive"]}')
        avg_mae = np.mean([r['test_mae'] for r in cv_results])
        print(f'  Average CV MAE: {avg_mae:.4f}')

    # Hyperparameter tuning
    if args.tune > 0:
        study, tuned_model = tune_hyperparameters(
            df_clean, features, n_trials=args.tune
        )
        if study:
            # Save best params
            best_params = study.best_params
            model = xgb.XGBRegressor(
                objective='reg:squarederror',
                random_state=42,
                verbosity=0,
                **{k: v for k, v in best_params.items()
                   if k not in ('objective', 'random_state', 'verbosity')}
            )
        else:
            model = None
    else:
        # Train with default params
        model, metrics = train_model(X_train, y_train, X_test, y_test)

        print(f'\n📊 Model Performance:')
        print(f'   Train MAE: {metrics["train_mae"]:.4f}')
        print(f'   Test MAE:  {metrics["test_mae"]:.4f}')
        print(f'   Test RMSE: {metrics["test_rmse"]:.4f}')
        print(f'   Test R²:   {metrics["test_r2"]:.4f}')
        print(f'   Naive MAE: {metrics["naive_mae"]:.4f}')
        print(f'   Better than naive: {"✅ YES" if metrics["better_than_naive"] else "❌ NO"}')

    # Save model
    if model is not None:
        model_path = MODEL_DIR / 'soccer_ou.json'
        model.save_model(str(model_path))
        print(f'\n💾 Saved model to {model_path}')

        # Feature importance
        importance = pd.DataFrame({
            'feature': features,
            'importance': model.feature_importances_,
        }).sort_values('importance', ascending=False)

        print(f'\n🔝 Top 10 Features:')
        for _, row in importance.head(10).iterrows():
            print(f'   {row["feature"]:25s} {row["importance"]:.4f}')

        importance.to_csv(MODEL_DIR / 'soccer_feature_importance.csv', index=False)

        # Save metadata
        metadata = {
            'trained_at': datetime.now().isoformat(),
            'train_games': len(X_train),
            'test_games': len(X_test),
            'train_date_range': f'{train_dates.min()} → {train_dates.max()}',
            'test_date_range': f'{test_dates.min()} → {test_dates.max()}',
            'features': features,
            'params': model.get_params(),
            'metrics': metrics if not args.tune else {
                'test_mae': study.best_value,
                'best_params': study.best_params,
            },
            'top_features': [
                {'feature': row['feature'], 'importance': float(row['importance'])}
                for _, row in importance.head(10).iterrows()
            ],
        }

        with open(MODEL_DIR / 'soccer_model_metadata.json', 'w') as f:
            json.dump(metadata, f, indent=2, default=str)

        print(f'\n📝 Metadata saved to {MODEL_DIR / "soccer_model_metadata.json"}')

    db.close()


if __name__ == '__main__':
    main()
