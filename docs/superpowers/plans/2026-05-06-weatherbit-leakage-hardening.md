# Weatherbit Leakage Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix daily-pick lookahead risk, move SportsBotv2 weather to Weatherbit, backfill MLB game weather, and publish the complete project without `.env`.

**Architecture:** Keep the XGBoost training path as the source of truth for point-in-time joins. `pick_today.py` will use the same strict ASOF join pattern as `train.py`. `weather.py` will prefer Weatherbit for historical and forecast weather while retaining NWS as an explicit fallback path.

**Tech Stack:** Python, DuckDB, requests, XGBoost, Weatherbit API, unittest.

---

### Task 1: Daily Pick Leakage Guard

**Files:**
- Modify: `xgb/pick_today.py`
- Modify: `tests/test_xgb_production.py`

- [x] **Step 1: Write the failing test**

Add a test that creates a scheduled game on `2026-05-06`, inserts team-hitting rows for `2026-05-05` and `2026-05-06`, and asserts the daily prediction loader returns the `2026-05-05` values.

- [x] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_xgb_production.XgbProductionTests.test_pick_today_prediction_query_uses_prior_stats_only -v`
Expected: FAIL because `pick_today.load_games_for_prediction` does not exist yet.

- [x] **Step 3: Implement the minimal code**

Extract the daily prediction SQL into `load_games_for_prediction(db, date_str)` and replace same-date joins with strict ASOF joins using `g.date > source.date`.

- [x] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_xgb_production.XgbProductionTests.test_pick_today_prediction_query_uses_prior_stats_only -v`
Expected: PASS.

### Task 2: Weatherbit Provider

**Files:**
- Modify: `xgb/weather.py`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `tests/test_xgb_production.py`

- [x] **Step 1: Write failing Weatherbit tests**

Add tests for Weatherbit historical weather normalization and forecast selection. Verify the API key is passed as a query parameter and never required in the URL string.

- [x] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_xgb_production.XgbProductionTests.test_weatherbit_history_normalizes_to_game_weather -v`
Expected: FAIL because Weatherbit helpers do not exist.

- [x] **Step 3: Implement Weatherbit helpers**

Add environment loading, `weatherbit_api_key()`, `fetch_weatherbit_history_weather()`, `fetch_weatherbit_forecast_weather()`, and use them in `fetch_weather_for_game()` before NWS fallback.

- [x] **Step 4: Run Weatherbit tests**

Run: `.venv/bin/python -m unittest tests.test_xgb_production.XgbProductionTests.test_weatherbit_history_normalizes_to_game_weather tests.test_xgb_production.XgbProductionTests.test_weatherbit_forecast_normalizes_to_game_weather -v`
Expected: PASS.

### Task 3: Backfill, Verify, Publish

**Files:**
- Update generated DB: `xgb/mlb.duckdb`
- Upload all tracked project files except `.env`

- [x] **Step 1: Run the focused test suite**

Run: `.venv/bin/python -m unittest tests.test_xgb_production -v`
Expected: PASS.

- [x] **Step 2: Backfill weather**

Run: `.venv/bin/python xgb/weather.py --retry-unavailable --sleep 0`
Actual: DB weather rows updated with `weatherbit:*` sources until Weatherbit returned a long retry window. Remaining `:missing`/`:unavailable` rows can be resumed later with the same command.

- [x] **Step 3: Run preflight**

Run: `.venv/bin/python xgb/preflight.py`
Expected: current DB, latest weather date, model metadata, and pending settlements are reported.

- [ ] **Step 4: Publish**

Stage the complete project in the `sportsmodel.git` clone, confirm `.env` is absent, include `xgb/mlb.duckdb`, commit, and push to `origin/main`.
