// src/tracker.js — Bet tracker: save picks, resolve results, show record

import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { fetchJSON } from './api/client.js';
import { readFileSync as readJSON } from 'fs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const ROOT = join(__dirname, '..');
const TRACKER_DIR = join(ROOT, 'data', 'tracker');
const PICKS_FILE = join(TRACKER_DIR, 'picks.json');
const TEAMS_FILE = join(ROOT, 'config', 'teams.json');

// Load team ID → abbreviation map
let _teamMap = null;
function getTeamAbbr(teamId) {
  if (!_teamMap) {
    const teams = JSON.parse(readJSON(TEAMS_FILE, 'utf8'));
    _teamMap = {};
    for (const [id, info] of Object.entries(teams.teams)) {
      _teamMap[id] = info.abbr;
    }
  }
  return _teamMap[String(teamId)];
}

// ── Load / Save ──────────────────────────────────────────────────────────

function loadPicks() {
  if (!existsSync(PICKS_FILE)) return [];
  try {
    return JSON.parse(readFileSync(PICKS_FILE, 'utf8'));
  } catch {
    return [];
  }
}

function writePicksFile(picks) {
  if (!existsSync(TRACKER_DIR)) {
    mkdirSync(TRACKER_DIR, { recursive: true });
  }
  writeFileSync(PICKS_FILE, JSON.stringify(picks, null, 2));
}

function csvValue(value) {
  if (value == null) return '';
  const text = String(value);
  if (!/[",\n]/.test(text)) return text;
  return `"${text.replaceAll('"', '""')}"`;
}

export function exportPicksCsv(outputPath = join(TRACKER_DIR, 'picks.csv')) {
  const picks = loadPicks();
  const columns = [
    'date',
    'away',
    'home',
    'pick',
    'line',
    'edge',
    'confidence',
    'projected',
    'result',
    'actualTotal',
    'resolvedAt',
  ];
  const lines = [
    columns.join(','),
    ...picks.map(p => columns.map(col => csvValue(p[col])).join(',')),
  ];
  const path = outputPath || join(TRACKER_DIR, 'picks.csv');
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `${lines.join('\n')}\n`);
  console.log(`📤 Exported ${picks.length} tracker row(s) to ${path}`);
  return path;
}

// ── Save today's picks ──────────────────────────────────────────────────

/**
 * Save picks from a run to the tracker.
 * Skips duplicates (same date + matchup).
 * @param {string} date - YYYY-MM-DD
 * @param {Array} results - game results from index.js
 */
export function savePicks(date, results) {
  const picks = loadPicks();
  const existingKeys = new Set(picks.map(p => `${p.date}_${p.away}_${p.home}`));

  let added = 0;
  for (const r of results) {
    // Only track games with an actual pick (not NO PLAY)
    if (!r.pick || r.pick === 'NO PLAY') continue;

    const key = `${date}_${r.away.abbr}_${r.home.abbr}`;
    if (existingKeys.has(key)) continue;

    picks.push({
      date,
      away: r.away.abbr,
      home: r.home.abbr,
      pick: r.pick,
      line: r.line,
      edge: r.edge,
      confidence: r.confidence,
      projected: r.combined || r.projected,
      result: null,        // null = pending
      actualTotal: null,
      resolvedAt: null,
    });
    existingKeys.add(key);
    added++;
  }

  writePicksFile(picks);
  if (added > 0) {
    console.log(`📝 Tracker: saved ${added} new pick(s)`);
  }
  return added;
}

// ── Resolve pending picks ────────────────────────────────────────────────

/**
 * Resolve pending picks by checking final scores.
 * @param {string} [targetDate] - resolve only this date, or all pending if omitted
 * @returns {Promise<{resolved: number, errors: string[]}>}
 */
export async function resolvePicks(targetDate = null) {
  const picks = loadPicks();
  const pending = picks.filter(p =>
    p.result === null && (targetDate ? p.date === targetDate : true)
  );

  if (pending.length === 0) {
    console.log('✅ No pending picks to resolve');
    return { resolved: 0, errors: [] };
  }

  // Group by date to minimize API calls
  const byDate = {};
  for (const p of pending) {
    if (!byDate[p.date]) byDate[p.date] = [];
    byDate[p.date].push(p);
  }

  let resolved = 0;
  const errors = [];

  for (const [date, datePicks] of Object.entries(byDate)) {
    try {
      const url = `https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=${date}`;
      const data = await fetchJSON(url, { label: `scores ${date}` });

      // Build map of final scores using team IDs → abbreviations
      const scores = {};
      for (const dateEntry of data.dates || []) {
        for (const g of dateEntry.games || []) {
          const status = g.status?.detailedState;
          if (status !== 'Final') continue;

          const awayId = g.teams?.away?.team?.id;
          const homeId = g.teams?.home?.team?.id;
          const awayAbbr = getTeamAbbr(awayId);
          const homeAbbr = getTeamAbbr(homeId);
          const total = (g.teams?.away?.score ?? 0) + (g.teams?.home?.score ?? 0);
          if (awayAbbr && homeAbbr) {
            scores[`${awayAbbr}_${homeAbbr}`] = total;
          }
        }
      }

      // Resolve picks for this date
      for (const pick of datePicks) {
        const total = scores[`${pick.away}_${pick.home}`];
        if (total == null) {
          errors.push(`${pick.date}: ${pick.away} @ ${pick.home} — game not final yet`);
          continue;
        }

        pick.actualTotal = total;

        if (total === pick.line) {
          pick.result = 'PUSH';
        } else if (pick.pick === 'OVER' && total > pick.line) {
          pick.result = 'WIN';
        } else if (pick.pick === 'UNDER' && total < pick.line) {
          pick.result = 'WIN';
        } else {
          pick.result = 'LOSS';
        }

        pick.resolvedAt = new Date().toISOString();
        resolved++;
      }
    } catch (err) {
      errors.push(`${date}: failed to fetch scores — ${err.message}`);
    }
  }

  writePicksFile(picks);
  return { resolved, errors };
}

// ── Get record ───────────────────────────────────────────────────────────

/**
 * Calculate the record from resolved picks.
 * @param {Object} [opts] - filter options
 * @returns {Object} record summary
 */
export function getRecord(opts = {}) {
  const picks = loadPicks();
  const { startDate, endDate, confidence, team } = opts;

  const filtered = picks.filter(p => {
    if (p.result === null) return false; // only resolved
    if (startDate && p.date < startDate) return false;
    if (endDate && p.date > endDate) return false;
    if (confidence && p.confidence !== confidence) return false;
    if (team && p.away !== team && p.home !== team) return false;
    return true;
  });

  const wins = filtered.filter(p => p.result === 'WIN').length;
  const losses = filtered.filter(p => p.result === 'LOSS').length;
  const pushes = filtered.filter(p => p.result === 'PUSH').length;
  const total = wins + losses + pushes;

  // By confidence
  const byConf = {};
  for (const conf of ['high', 'medium', 'low']) {
    const subset = filtered.filter(p => p.confidence === conf);
    byConf[conf] = {
      wins: subset.filter(p => p.result === 'WIN').length,
      losses: subset.filter(p => p.result === 'LOSS').length,
      pushes: subset.filter(p => p.result === 'PUSH').length,
    };
  }

  // By pick type
  const overs = filtered.filter(p => p.pick === 'OVER');
  const unders = filtered.filter(p => p.pick === 'UNDER');

  // ROI at -110 odds (bet $110 to win $100)
  const unitsBet = total * 1.1;  // each unit bet is 1.1
  const unitsWon = wins * 1.0;   // each win pays 1.0
  const unitsLost = losses * 1.1; // each loss costs 1.1
  const profit = unitsWon - unitsLost;
  const roi = unitsBet > 0 ? (profit / unitsBet) * 100 : 0;

  return {
    wins,
    losses,
    pushes,
    total,
    winPct: total > 0 ? ((wins / (wins + losses)) * 100).toFixed(1) : '0.0',
    profit: profit.toFixed(2),
    roi: roi.toFixed(1),
    byConfidence: byConf,
    over: {
      wins: overs.filter(p => p.result === 'WIN').length,
      losses: overs.filter(p => p.result === 'LOSS').length,
    },
    under: {
      wins: unders.filter(p => p.result === 'WIN').length,
      losses: unders.filter(p => p.result === 'LOSS').length,
    },
    pending: picks.filter(p => p.result === null).length,
  };
}

// ── Print record ─────────────────────────────────────────────────────────

export function printRecord(opts = {}) {
  const r = getRecord(opts);

  console.log('');
  console.log('═══════════════════════════════════════════════════');
  console.log('             📊 BET TRACKER RECORD');
  console.log('═══════════════════════════════════════════════════');

  if (r.total === 0) {
    console.log('  No resolved picks yet.');
    if (r.pending > 0) console.log(`  ${r.pending} pick(s) pending.`);
    console.log('═══════════════════════════════════════════════════\n');
    return;
  }

  console.log(`  RECORD:  ${r.wins}W - ${r.losses}L - ${r.pushes}P  (${r.winPct}% win rate)`);
  console.log(`  ROI:     ${r.roi}%  (profit: ${r.profit} units)`);
  console.log('');

  // By pick type
  console.log('  OVER:    ' +
    `${r.over.wins}W - ${r.over.losses}L`);
  console.log('  UNDER:   ' +
    `${r.under.wins}W - ${r.under.losses}L`);
  console.log('');

  // By confidence
  console.log('  BY CONFIDENCE:');
  for (const [conf, data] of Object.entries(r.byConfidence)) {
    const t = data.wins + data.losses + data.pushes;
    if (t === 0) continue;
    const pct = (data.wins + data.losses) > 0
      ? ((data.wins / (data.wins + data.losses)) * 100).toFixed(1)
      : '0.0';
    const icon = conf === 'high' ? '★★★' : conf === 'medium' ? '★★☆' : '★☆☆';
    console.log(`    ${icon} ${conf.padEnd(8)} ${data.wins}W - ${data.losses}L (${pct}%)`);
  }

  if (r.pending > 0) {
    console.log(`\n  ⏳ ${r.pending} pick(s) still pending`);
  }

  console.log('═══════════════════════════════════════════════════\n');
}

// ── Recent picks ─────────────────────────────────────────────────────────

export function printRecentPicks(n = 10) {
  const picks = loadPicks();
  const resolved = picks
    .filter(p => p.result !== null)
    .sort((a, b) => b.date.localeCompare(a.date))
    .slice(0, n);

  if (resolved.length === 0) {
    console.log('  No resolved picks yet.\n');
    return;
  }

  console.log('');
  console.log('  📋 RECENT PICKS:');
  console.log('  ─────────────────────────────────────────────');

  for (const p of resolved) {
    const resultIcon = p.result === 'WIN' ? '✅' : p.result === 'LOSS' ? '❌' : '➖';
    const edge = p.edge > 0 ? `+${p.edge.toFixed(2)}` : p.edge.toFixed(2);
    console.log(
      `  ${resultIcon} ${p.date}  ${(p.away + ' @ ' + p.home).padEnd(12)} ` +
      `${p.pick.padEnd(5)} line=${p.line}  edge=${edge}  actual=${p.actualTotal}`
    );
  }

  console.log('');
}
