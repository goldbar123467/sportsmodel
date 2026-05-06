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

## Phase 3: Audit & Gap Fix ✅ DONE

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

### 2.1 — Pitcher Rating
- [ ] Make FIP constant configurable (default 3.20 for 2026)
- [ ] Raise minimum IP threshold to 30 IP
- [ ] Weight: 65% FIP, 35% ERA (was 60/40)
- [ ] Add recent-form weighting (last 5 starts count double)
- [ ] Show confidence indicator (low/med/high IP)

### 2.2 — Offense Rating
- [ ] Raise minimum PA threshold to 200
- [ ] Add home/road split (teams hit differently on the road)
- [ ] Add last-14-day hot/cold adjustment

### 2.3 — Park Factors
- [ ] Apply park factor to pitcher ERAs (not just final total)
- [ ] Add altitude adjustment for Coors Field
- [ ] Add dome factor (dome = no weather, suppresses variance)

### 2.4 — Weather
- [ ] Rate-limit weather calls (200ms between requests)
- [ ] Skip weather for domed stadiums
- [ ] Add humidity factor (high humidity = ball carries)
- [ ] Add temperature threshold (>90°F = more offense)

---

## Phase 3: Output & Usability

### 3.1 — Structured Output
- [ ] JSON output saved to `data/results/YYYY-MM-DD.json`
- [ ] Console table with matchup, model, line, edge, pick
- [ ] Confidence tiers: ★★★ (strong), ★★ (moderate), ★ (lean)

### 3.2 — Summary Report
- [ ] Daily recap: picks made, wins/losses
- [ ] Season tracking: record, ROI, units won/lost
- [ ] Export to CSV

---

## Phase 4: Hardening

### 4.1 — Error Handling
- [ ] Graceful fallback for each API (continue if one fails)
- [ ] Log errors to file, not just console
- [ ] Validate API responses before using data

### 4.2 — Input Validation
- [ ] Check date format
- [ ] Check API key presence before making calls
- [ ] Warn if odds data is stale (>4 hours old)

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
