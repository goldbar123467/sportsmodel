// src/model/hrpicks.js — Home Run Parlay Picks
// Finds the 2 best HR matchups using season stats + pitcher HR allowed + park factors

import { fetchJSON } from '../api/client.js';
import { getStadium } from './park.js';

/**
 * Fetch top HR hitters for a team this season.
 * @param {number} teamId
 * @param {string} season - e.g. '2026'
 * @returns {Promise<Array>} Top hitters sorted by HR desc
 */
async function fetchTopHitters(teamId, season) {
  try {
    const url = `https://statsapi.mlb.com/api/v1/stats?stats=season&season=${season}&group=hitting&limit=10&teamIds=${teamId}&sortStat=homeRuns&order=desc`;
    const data = await fetchJSON(url, { label: `hitters ${teamId}` });
    return (data.stats?.[0]?.splits || []).map(s => ({
      id: s.player?.id,
      name: s.player?.fullName,
      hr: s.stat?.homeRuns || 0,
      pa: s.stat?.plateAppearances || 0,
      slg: parseFloat(s.stat?.slg) || 0,
      ops: parseFloat(s.stat?.ops) || 0,
      hrPerPA: s.stat?.plateAppearances ? s.stat.homeRuns / s.stat.plateAppearances : 0,
    }));
  } catch {
    return [];
  }
}

/**
 * Merge roster handedness with top hitter stats.
 */
function mergeWithRoster(hitters, roster) {
  const handMap = {};
  const activeNames = new Set();
  if (roster?.batters) {
    for (const b of roster.batters) {
      handMap[b.name] = b.bats;
      activeNames.add(b.name);
    }
  }
  return hitters.map(h => ({
    ...h,
    batSide: handMap[h.name] || null,
  })).filter(h => h.pa >= 10 && (activeNames.size === 0 || activeNames.has(h.name))); // min PA filter
}

/**
 * Score a HR matchup.
 */
function scoreMatchup(batter, pitcherHrRate, parkHrFactor, pitcherHand) {
  // Platoon advantage
  let platoonBoost = 0;
  if (batter.batSide && pitcherHand) {
    if (batter.batSide === 'L' && pitcherHand === 'R') platoonBoost = 0.15;
    else if (batter.batSide === 'R' && pitcherHand === 'L') platoonBoost = 0.10;
  }

  // SLG is a proxy for power (we don't have barrel rate from this API)
  const slgBonus = (batter.slg - 0.400) * 0.5; // above average SLG gets bonus

  const score = (
    (batter.hrPerPA * 3.0) +      // HR rate is primary
    (batter.hr / batter.pa * 2.0) + // raw HR frequency
    (pitcherHrRate * 1.5) +        // pitcher gives up HRs
    (slgBonus * 0.8) +             // power indicator
    (platoonBoost * 0.5)           // platoon edge
  ) * parkHrFactor;                // park multiplier

  return {
    score: Math.round(score * 1000) / 1000,
    hrPerPA: Math.round(batter.hrPerPA * 1000) / 1000,
    slg: batter.slg,
    pitcherHrRate: Math.round(pitcherHrRate * 1000) / 1000,
    parkHrFactor,
    platoonBoost,
  };
}

/**
 * Pick the 2 best HR candidates from today's games.
 */
export async function pickHomeRuns({ games, rosters, pitcherStats, pitcherHands }) {
  const season = new Date().getFullYear().toString();
  const candidates = [];

  // Collect unique team IDs
  const teamIds = new Set();
  for (const g of games) {
    teamIds.add(g.away.id);
    teamIds.add(g.home.id);
  }

  // Fetch top hitters for each team (batch by unique teams)
  const teamHitters = {};
  for (const tid of teamIds) {
    const hitters = await fetchTopHitters(tid, season);
    teamHitters[tid] = mergeWithRoster(hitters, rosters[tid]);
  }

  for (const g of games) {
    const stadium = getStadium(g.home.abbr);
    const parkHr = stadium?.parkHrFactor || 1.0;

    const homePitcherId = g.home.starter?.id;
    const awayPitcherId = g.away.starter?.id;
    const homePitcherHand = homePitcherId ? pitcherHands[homePitcherId] : null;
    const awayPitcherHand = awayPitcherId ? pitcherHands[awayPitcherId] : null;

    // Get pitcher HR rates
    const homePitcherHr = pitcherStats[homePitcherId]?.hr_rate || 0.032;
    const awayPitcherHr = pitcherStats[awayPitcherId]?.hr_rate || 0.032;

    // Away batters vs home pitcher
    for (const batter of (teamHitters[g.away.id] || [])) {
      const result = scoreMatchup(batter, homePitcherHr, parkHr, homePitcherHand);
      if (result.score > 0) {
        candidates.push({
          name: batter.name,
          team: g.away.abbr,
          opponent: g.home.abbr,
          batSide: batter.batSide,
          pitcherHand: homePitcherHand,
          matchup: `${g.away.abbr} @ ${g.home.abbr}`,
          hr: batter.hr,
          pa: batter.pa,
          ...result,
        });
      }
    }

    // Home batters vs away pitcher
    // Park factor is different for home team (their park)
    for (const batter of (teamHitters[g.home.id] || [])) {
      const result = scoreMatchup(batter, awayPitcherHr, parkHr, awayPitcherHand);
      if (result.score > 0) {
        candidates.push({
          name: batter.name,
          team: g.home.abbr,
          opponent: g.away.abbr,
          batSide: batter.batSide,
          pitcherHand: awayPitcherHand,
          matchup: `${g.away.abbr} @ ${g.home.abbr}`,
          hr: batter.hr,
          pa: batter.pa,
          ...result,
        });
      }
    }
  }

  // Sort by score, pick top 2 from different games
  candidates.sort((a, b) => b.score - a.score);

  const picks = [];
  const usedMatchups = new Set();
  for (const c of candidates) {
    if (picks.length >= 2) break;
    if (usedMatchups.has(c.matchup)) continue;
    picks.push(c);
    usedMatchups.add(c.matchup);
  }

  return picks;
}

/**
 * Format HR picks for display.
 */
export function printHRPicks(picks) {
  if (picks.length === 0) {
    console.log('  ⚾ No HR picks available today\n');
    return;
  }

  console.log('');
  console.log('═══════════════════════════════════════════════════');
  console.log('         💣 DAILY HOME RUN PARLAY');
  console.log('═══════════════════════════════════════════════════');

  for (let i = 0; i < picks.length; i++) {
    const p = picks[i];
    const platoonNote = p.platoonBoost > 0
      ? ` (${p.batSide === 'L' ? 'L' : 'R'} vs ${p.pitcherHand === 'L' ? 'LHP' : 'RHP'})`
      : '';
    console.log(`  ${i + 1}. ${p.name} (${p.team})${platoonNote}`);
    console.log(`     ${p.matchup} | ${p.hr} HR in ${p.pa} PA | HR/PA: ${(p.hrPerPA * 100).toFixed(1)}% | SLG: ${p.slg.toFixed(3)}`);
    console.log(`     Pitcher HR allowed: ${(p.pitcherHrRate * 100).toFixed(1)}% | Park HR: ${p.parkHrFactor.toFixed(2)}x | Score: ${p.score.toFixed(3)}`);
  }

  console.log('───────────────────────────────────────────────────');
  console.log('  💰 Parlay: Both players to hit a HR');
  console.log('═══════════════════════════════════════════════════\n');
}
