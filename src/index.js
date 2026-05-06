// src/index.js — Main entry point for SportsBotv2
// Run: node src/index.js [YYYY-MM-DD]

import { fetchSchedule } from './api/schedule.js';
import { fetchPitcherStats, fetchPitcherHand } from './api/pitcher.js';
import { fetchTeamStats } from './api/team.js';
import { fetchOdds } from './api/odds.js';
import { fetchAllWeather } from './api/weather.js';
import { fetchRoster, calcPlatoon } from './api/roster.js';
import { project } from './model/project.js';
import { getStadium } from './model/park.js';
import { tacticianScore, daysRestAdjustment, workloadDecay } from './model/tactician.js';
import { pickHomeRuns, printHRPicks } from './model/hrpicks.js';
import { printResults, printSummary } from './output/console.js';
import { printTactician, printCombined } from './output/tactician.js';
import { saveResults } from './output/json.js';
import { savePicks, resolvePicks, printRecord, printRecentPicks } from './tracker.js';
import { loadConfig, loadTeams, fetchJSON } from './api/client.js';

const args = process.argv.slice(2);

// Per-confidence edge thresholds
function getEdgeThreshold(confidence) {
  const config = loadConfig();
  if (confidence === 'high') return config.edgeThresholdHigh || 1.5;
  if (confidence === 'low') return config.edgeThresholdLow || 1.0;
  return config.edgeThresholdMedium || config.edgeThreshold || 0.75;
}

// ── Tracker commands ──
if (args.includes('--record')) {
  printRecord();
  process.exit(0);
}
if (args.includes('--recent')) {
  const n = parseInt(args[args.indexOf('--recent') + 1]) || 10;
  printRecentPicks(n);
  process.exit(0);
}
if (args.includes('--resolve')) {
  const dateArg = args[args.indexOf('--resolve') + 1] || null;
  console.log('🔍 Resolving pending picks...');
  const { resolved, errors } = await resolvePicks(dateArg);
  if (resolved > 0) console.log(`✅ Resolved ${resolved} pick(s)`);
  for (const e of errors) console.log(`  ⚠️  ${e}`);
  printRecord();
  process.exit(0);
}

const date = args[0] || new Date().toISOString().slice(0, 10);
const season = date.slice(0, 4);
const config = loadConfig();
const teams = loadTeams();

console.log(`\n🏟️  SportsBotv2 — MLB O/U Projections for ${date}\n`);

// ── 1. Fetch schedule ──
console.log('1️⃣  Fetching schedule...');
const games = await fetchSchedule(date);
console.log(`   Found ${games.length} games\n`);

if (games.length === 0) {
  console.log('No games scheduled today. Done.');
  process.exit(0);
}

// ── 2. Collect IDs ──
const pitcherIds = new Set();
const teamIds = new Set();
for (const g of games) {
  if (g.away.starter?.id) pitcherIds.add(g.away.starter.id);
  if (g.home.starter?.id) pitcherIds.add(g.home.starter.id);
  teamIds.add(g.away.id);
  teamIds.add(g.home.id);
}

// ── 3. Fetch pitcher stats (with rest days) ──
console.log('2️⃣  Fetching pitcher stats...');
const pitcherLogs = {};
const pitcherHands = {};
for (const pid of pitcherIds) {
  pitcherLogs[pid] = await fetchPitcherStats(pid, season, date);
  pitcherHands[pid] = await fetchPitcherHand(pid);
}
const pFound = Object.values(pitcherLogs).filter(Boolean).length;
console.log(`   Got stats for ${pFound}/${pitcherIds.size} pitchers`);

// Fetch pitcher HR rates for HR picks
console.log('   Fetching pitcher HR rates...');
const pitcherHrRates = {};
for (const pid of pitcherIds) {
  try {
    const url = `https://statsapi.mlb.com/api/v1/stats?stats=season&season=${season}&group=pitching&limit=1&pitcherIds=${pid}`;
    const data = await fetchJSON(url, { label: `pitcher HR ${pid}` });
    const split = data.stats?.[0]?.splits?.[0];
    if (split?.stat) {
      pitcherHrRates[pid] = {
        hr: split.stat.homeRuns || 0,
        bf: split.stat.battersFaced || 0,
        hr_rate: split.stat.battersFaced ? split.stat.homeRuns / split.stat.battersFaced : 0.032,
      };
    }
  } catch { /* skip */ }
}
console.log(`   Got HR rates for ${Object.keys(pitcherHrRates).length} pitchers\n`);

// ── 4. Fetch team stats ──
console.log('3️⃣  Fetching team stats...');
const teamLogs = {};
for (const tid of teamIds) {
  teamLogs[tid] = await fetchTeamStats(tid, season);
}
const tFound = Object.values(teamLogs).filter(Boolean).length;
console.log(`   Got stats for ${tFound}/${teamIds.size} teams\n`);

// ── 5. Fetch rosters for platoon splits ──
console.log('4️⃣  Fetching rosters (platoon splits)...');
const rosters = {};
for (const tid of teamIds) {
  rosters[tid] = await fetchRoster(tid);
}
console.log(`   Got rosters for ${Object.keys(rosters).length} teams\n`);

// ── 6. Fetch odds ──
console.log('5️⃣  Fetching odds...');
const odds = await fetchOdds();
console.log(`   Got lines for ${odds.length} games\n`);

// ── 7. Fetch weather ──
console.log('6️⃣  Fetching weather...');
const outdoorStadiums = {};
for (const abbr of [...new Set(games.map(g => g.home.abbr))]) {
  const s = getStadium(abbr);
  if (s && !s.roof) outdoorStadiums[abbr] = s;
}
const weather = await fetchAllWeather(outdoorStadiums);
const wCount = Object.values(weather).filter(Boolean).length;
console.log(`   Got weather for ${wCount} outdoor stadiums\n`);

// ── 8. Run projections + tactician ──
console.log('7️⃣  Running projections + tactician analysis...\n');

const results = [];

for (const g of games) {
  const awayPitcherLog = g.away.starter?.id ? pitcherLogs[g.away.starter.id] : null;
  const homePitcherLog = g.home.starter?.id ? pitcherLogs[g.home.starter.id] : null;
  const awayTeamLog = teamLogs[g.away.id];
  const homeTeamLog = teamLogs[g.home.id];
  const awayRoster = rosters[g.away.id];
  const homeRoster = rosters[g.home.id];

  // Base projection
  const { projected, confidence, breakdown } = project({
    homeAbbr: g.home.abbr,
    awayPitcherLog,
    homePitcherLog,
    awayTeamLog,
    homeTeamLog,
    weather: weather[g.home.abbr],
  });

  // ── TACTICIAN ANALYSIS (with rest days + platoon) ──
  const stadium = getStadium(g.home.abbr);
  const weatherData = weather[g.home.abbr];

  // Pitcher handedness for platoon
  const awayPitcherHand = g.away.starter?.id ? pitcherHands[g.away.starter.id] : null;
  const homePitcherHand = g.home.starter?.id ? pitcherHands[g.home.starter.id] : null;

  // Platoon splits
  const awayPlatoon = calcPlatoon(homeRoster, awayPitcherHand); // home lineup vs away pitcher
  const homePlatoon = calcPlatoon(awayRoster, homePitcherHand); // away lineup vs home pitcher

  // Rest days (from pitcher game log)
  const awayRestDays = awayPitcherLog?.restDays ?? null;
  const homeRestDays = homePitcherLog?.restDays ?? null;

  // Workload decay
  const awayWorkload = awayPitcherLog ? workloadDecay(awayPitcherLog.total.ip) : 0;
  const homeWorkload = homePitcherLog ? workloadDecay(homePitcherLog.total.ip) : 0;

  // Build tactician inputs
  const tactInput = {
    pitcher: awayPitcherLog ? {
      avgPitchCount: 95,
      seasonIP: awayPitcherLog.total?.ip || 0,
    } : null,
    // Weather physics removed — project.js already handles weather + park/altitude
    // Including it here was double-counting Coors Field and other park effects
    barrelRate: awayTeamLog?.total ? (awayTeamLog.total.hr / awayTeamLog.total.pa) * 2.5 : null,
  };

  const tactician = tacticianScore(tactInput);

  // Add rest days, platoon, and workload decay to tactician
  const awayRestAdj = awayRestDays !== null ? daysRestAdjustment(awayRestDays) : 0;
  const homeRestAdj = homeRestDays !== null ? daysRestAdjustment(homeRestDays) : 0;

  // Apply adjustments: rest days and platoon go to the OPPOSING team's scoring
  // Away team benefits from home pitcher being on short rest
  // Home team benefits from away pitcher being on short rest
  const restPlatoonAdj = (awayRestAdj - homeRestAdj) + (awayPlatoon.adjustment - homePlatoon.adjustment);
  const workloadAdj = awayWorkload - homeWorkload;

  tactician.total += restPlatoonAdj + workloadAdj;
  tactician.breakdown.restDays = restPlatoonAdj;
  tactician.breakdown.workload = workloadAdj;
  tactician.breakdown.platoon = awayPlatoon.adjustment - homePlatoon.adjustment;
  tactician.total = Math.round(tactician.total * 100) / 100;

  // Combined projection
  const combined = projected + (tactician.total || 0);

  // Find matching odds line
  const match = odds.find(o => o.away === g.away.abbr && o.home === g.home.abbr);
  const line = match?.total || null;

  // Collect warnings
  const gameWarnings = [];

  // Calculate edge using COMBINED projection
  let edge = null;
  let pick = 'NO PLAY';
  if (line) {
    edge = combined - line;
    // Skip if either starter is a default/placeholder (no real data)
    const awayDefault = !awayPitcherLog || (awayPitcherLog.total?.ip || 0) < (config.minPitcherIPPartial || 10);
    const homeDefault = !homePitcherLog || (homePitcherLog.total?.ip || 0) < (config.minPitcherIPPartial || 10);
    if (awayDefault || homeDefault) {
      pick = 'NO PLAY';
      if (awayDefault) gameWarnings.push(`${g.away.abbr}: No starter data — pick skipped`);
      if (homeDefault) gameWarnings.push(`${g.home.abbr}: No starter data — pick skipped`);
    } else if (Math.abs(edge) >= getEdgeThreshold(confidence)) {
      pick = edge > 0 ? 'OVER' : 'UNDER';
    } else if (edge != null && Math.abs(edge) >= (config.edgeThreshold || 0.75)) {
      // Edge exists but below confidence threshold — log why skipped
      gameWarnings.push(`${g.away.abbr} @ ${g.home.abbr}: Edge ${edge > 0 ? '+' : ''}${edge.toFixed(2)} below ${confidence} threshold (${getEdgeThreshold(confidence).toFixed(2)})`);
    }
  }
  if (!g.away.starter) gameWarnings.push(`${g.away.abbr}: Away starter TBD`);
  if (!g.home.starter) gameWarnings.push(`${g.home.abbr}: Home starter TBD`);
  if (awayRestDays !== null && awayRestDays <= 2) gameWarnings.push(`${g.away.abbr}: Short rest (${awayRestDays} days)`);
  if (homeRestDays !== null && homeRestDays <= 2) gameWarnings.push(`${g.home.abbr}: Short rest (${homeRestDays} days)`);

  results.push({
    away: g.away,
    home: g.home,
    projected,
    combined,
    line,
    edge,
    pick,
    confidence,
    warnings: gameWarnings,
    breakdown,
    tactician,
    platoon: { away: awayPlatoon, home: homePlatoon },
    restDays: { away: awayRestDays, home: homeRestDays },
  });
}

// ── 9. Output ──
printResults(results);
printSummary(results);

// Tactician breakdown
console.log('🧠 TACTICIAN ANALYSIS:');
console.log('───────────────────────────────────────────────');
for (const r of results) {
  const matchup = `${r.away.abbr} @ ${r.home.abbr}`;
  printTactician(matchup, r.tactician);
  printCombined(r);

  // Show platoon detail
  if (r.platoon.away.adjustment !== 0 || r.platoon.home.adjustment !== 0) {
    const a = r.platoon.away;
    const h = r.platoon.home;
    console.log(`  ⚔️  Platoon: away lineup ${a.favorable}L/${a.unfavorable}R/${a.neutral}S vs home pitcher → ${a.adjustment > 0 ? '+' : ''}${a.adjustment.toFixed(2)}`);
    console.log(`           home lineup ${h.favorable}L/${h.unfavorable}R/${h.neutral}S vs away pitcher → ${h.adjustment > 0 ? '+' : ''}${h.adjustment.toFixed(2)}`);
  }

  // Show rest days
  if (r.restDays.away !== null || r.restDays.home !== null) {
    console.log(`  📅 Rest: ${r.away.abbr} starter on ${r.restDays.away ?? '?'} days rest | ${r.home.abbr} starter on ${r.restDays.home ?? '?'} days rest`);
  }
}
console.log('');

saveResults(date, results);
savePicks(date, results);

// Quick tracker recap
const picksMade = results.filter(r => r.pick && r.pick !== 'NO PLAY');
if (picksMade.length > 0) {
  const { getRecord } = await import('./tracker.js');
  const rec = getRecord();
  if (rec.total > 0) {
    console.log(`📊 TRACKER: ${rec.wins}W - ${rec.losses}L  (${rec.winPct}% | ROI: ${rec.roi}%)`);
  }
}

console.log('✅ Done!\n');

// ── 10. HR Parlay Picks ──
console.log('💣 Generating HR parlay picks...');
pickHomeRuns({
  games,
  rosters,
  pitcherStats: pitcherHrRates,
  pitcherHands,
}).then(picks => {
  printHRPicks(picks);
}).catch(err => {
  console.log(`  ⚠️  HR picks failed: ${err.message}`);
});
