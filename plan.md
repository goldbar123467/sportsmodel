# SportsBotv2 — Build Plan

**Date:** 2026-04-23
**Goal:** Rebuild the MLB O/U projection bot from the ground up with clean architecture, secure config, and improved model.

---

## Phase 1: Foundation (Fix the Basics) ✅ DONE

### 1.1 — Project Structure ✅
```
SportsBotv2/
├── .env                  # API keys (gitignored)
├── .env.example          # Template for keys
├── config/
│   ├── defaults.json     # All tuneable constants
│   └── teams.json        # Team abbr, IDs, stadium data, park factors
├── src/
│   ├── api/
│   │   ├── schedule.js   # MLB schedule fetcher
│   │   ├── pitcher.js    # Pitcher stats fetcher
│   │   ├── team.js       # Team stats fetcher
│   │   ├── odds.js       # Odds API fetcher
│   │   └── weather.js    # Weather fetcher
│   ├── model/
│   │   ├── fip.js        # FIP/ERA pitcher rating
│   │   ├── offense.js    # Team offensive multiplier
│   │   ├── park.js       # Park factor adjustments
│   │   ├── weather.js    # Wind & temp adjustments
│   │   └── project.js    # Final projection engine
│   ├── output/
│   │   ├── console.js    # Pretty terminal output
│   │   └── json.js       # JSON file output
│   └── index.js          # Main entry point
├── data/
│   └── results/          # Saved projection JSONs
├── package.json
└── plan.md               # This file
```

### 1.2 — Config System ✅
- [x] Create `.env` with API keys
- [x] Create `.env.example` (no real keys)
- [x] Create `config/defaults.json` with all constants
- [x] Create `config/teams.json` with team/stadium/park data
- [x] Use `dotenv` package to load `.env`

### 1.3 — Modular API Layer ✅
- [x] `src/api/schedule.js` — Fetch MLB schedule for a date
- [x] `src/api/pitcher.js` — Fetch pitcher game logs
- [x] `src/api/team.js` — Fetch team hitting stats
- [x] `src/api/odds.js` — Fetch FanDuel totals lines
- [x] `src/api/weather.js` — Fetch weather with rate limiting
- [x] Add retry logic (3 attempts, exponential backoff)
- [x] Add 200ms delay between weather requests

---

## Phase 2: Model Improvements ✅ DONE

### 2.1 — Pitcher Rating ✅
- [x] Make FIP constant configurable (default 3.20 for 2026)
- [x] Raise minimum IP threshold to 30 IP
- [x] Weight: 65% FIP, 35% ERA
- [x] Add recent-form weighting (last 5 starts count 1.5x)
- [x] Show confidence indicator (low/med/high IP)
- [x] Partial data blending (10-30 IP blends with league avg)
- [x] Home/road splits for pitchers

### 2.2 — Offense Rating ✅
- [x] Raise minimum PA threshold to 200
- [x] Add home/road split (teams hit differently on the road)
- [x] Add last-14-day hot/cold adjustment (clamped ±15%)
- [x] Partial data blending (50-200 PA blends with league avg)

### 2.3 — Park Factors ✅
- [x] Apply park factor to pitcher ERAs (not just final total)
- [x] Add altitude adjustment for Coors Field (conservative 3%/1000ft)
- [x] Dome factor already handled (no weather for domed stadiums)

### 2.4 — Weather ✅
- [x] Rate-limit weather calls (200ms between requests)
- [x] Skip weather for domed stadiums
- [x] Add humidity factor (>70% boosts offense, <40% suppresses)
- [x] Add temperature threshold (>90°F = more offense)

## Phase 3A: Audit & Gap Fix ✅ DONE

### Audit Findings
Read full tactician skill, compared against codebase, identified 3 highest-priority gaps.

### Fix 1: Pitcher Rest Days ✅
- [x] Updated `src/api/pitcher.js` to calculate rest days from game log dates
- [x] Rest days feed into tactician: short rest +0.3, extra rest -0.1, extended -0.2
- [x] Warning shown for pitchers on ≤2 days rest

### Fix 2: Platoon Splits ✅
- [x] Created `src/api/roster.js` — fetches active roster with `hydrate=person` for batSide
- [x] `calcPlatoon()` counts L/R/S batters vs pitcher handedness
- [x] Each net favorable matchup adds +0.04 runs
- [x] Pitcher handedness fetched from `/api/v1/people/{id}` endpoint

### Fix 3: Workload Decay ✅
- [x] Already coded in tactician but not wired into pipeline
- [x] Now passes season IP through to tactician score
- [x] 140+ IP = fatigue signal, 180+ = noticeable, 200+ = diminishing returns

### Tactician Layer
- [x] Created `skills/baseball-tactician/` with SKILL.md, 3 reference docs, edge-calc.js
- [x] Copied edge-calc.js into `src/model/tactician.js`
- [x] Created `src/output/tactician.js` for display
- [x] Updated `src/index.js` to run tactician for every game automatically
- [x] Combined projection = base + tactician adjustment
- [x] Edge/pick decisions use combined projection
- [x] JSON output includes tactician breakdown

## Phase 3B: Output & Usability ✅ READY

Phase 3B is the user-facing reporting layer. The core JSON, console card, and tracker already exist; the remaining work is to tighten the interfaces and add a portable export path without changing the projection model.

### 3.1 — Structured Output ✅
- [x] JSON output saved to `data/results/YYYY-MM-DD.json` via `src/output/json.js`
- [x] Console table shows matchup, model, line, edge, pick, and confidence via `src/output/console.js`
- [x] Confidence tiers render as stars: high `★★★`, medium `★★☆`, low `★☆☆`
- [x] Tactician breakdown is included in saved JSON and printed output

### 3.2 — Summary Report ✅ READY
- [x] Daily recap prints picks made, overs, unders, and no-plays
- [x] Pick tracker stores dated selections in `data/tracker/picks.json`
- [x] `node src/index.js --resolve [YYYY-MM-DD]` resolves pending picks against final scores
- [x] `node src/index.js --record` prints season record, ROI, profit, confidence splits, and pending count
- [x] `node src/index.js --recent [N]` prints recent resolved picks
- [x] `node src/index.js --export-csv [path]` exports tracker history to CSV

### 3.3 — Phase 3B Implementation Notes
- `--export-csv [path]` is implemented in `src/index.js` and `src/tracker.js`.
- CSV columns: `date,away,home,pick,line,edge,confidence,projected,result,actualTotal,resolvedAt`.
- Keep the existing JSON tracker as source of truth; CSV is a derived export only.
- Do not change pick thresholds or projection math in this phase.

---

## Phase 4: Hardening ✅ READY

### 4.1 — Error Handling
- [x] Shared `fetchJSON()` retries failed requests and honors HTTP 429 `Retry-After`
- [x] Odds API gracefully returns no lines when `ODDS_API_KEY` is missing or the odds request fails
- [x] Weather and HR-pick paths already skip failed optional data
- [x] Pitcher/team/roster/weather stage failures are logged and degraded to missing data/no-play where possible
- [x] Log structured errors to `data/logs/YYYY-MM-DD.log` in addition to console output
- [x] Validate schedule and odds response shapes before using data

### 4.2 — Input Validation
- [x] Check CLI date format as strict `YYYY-MM-DD`
- [x] Check odds API key presence before making odds calls
- [x] Add local validation helpers for date and response checks
- [x] Warn if odds data is stale relative to game start or fetch time

### 4.3 — Phase 4 Implementation Notes
- `src/validate.js` handles date validation, response guards, and stale-odds warnings.
- `src/logger.js` writes structured records to `data/logs/`.
- Treat missing required data as `NO PLAY` with a warning when possible.
- Keep hard failures for invalid local config, malformed dates, and unreadable config files.
- Do not add new external services in this phase.

---

## Phase 5: Cron Automation ✅ DONE

### 5.1 — Daily Cron Job
- [x] Cron job runs at 9:55 AM CDT daily (`55 9 * * *` America/Chicago)
- [x] Isolated agent turn: reads baseball-tactician skill first, then runs bot
- [x] Full output with picks + tactician analysis delivered to chat
- [x] `deleteAfterRun: true` — clean up after each run

### Future Enhancements
- [ ] Bullpen modeling (fetch reliever stats)
- [ ] Head-to-head splits
- [ ] Web UI for viewing picks
- [ ] Season tracking: record, ROI, units won/lost

---

## Key Decisions

| Decision | Choice | Why |
|---|---|---|
| Module format | ES Modules (.mjs) | Modern, async/await native |
| Config loading | dotenv + JSON | Simple, no build step |
| Output format | JSON + console table | Machine-readable + human-friendly |
| FIP constant | 3.20 (configurable) | More accurate for recent seasons |
| Min pitcher IP | 30 | Filter out small-sample noise |
| Edge threshold | 0.5 runs | Same as v1, tune later |

---

## Success Criteria

- [ ] All API keys out of source code
- [ ] Modular — can change one piece without breaking others
- [ ] Model produces reasonable projections (within 0.5 runs of market on average)
- [ ] Output is saved and comparable day-to-day
- [ ] Runs without crashing on API errors
