// src/api/roster.js — Fetch team roster with batter handedness for platoon splits

import { fetchJSON } from './client.js';

/**
 * Fetch a team's active roster with batter handedness.
 * @param {number} teamId
 * @returns {Promise<Object>} { batters: [{ name, bats }], lhbCount, rhbCount, switchCount }
 */
export async function fetchRoster(teamId) {
  try {
    const url = `https://statsapi.mlb.com/api/v1/teams/${teamId}/roster?rosterType=active&season=2026&hydrate=person`;
    const data = await fetchJSON(url, { label: `roster ${teamId}` });
    const roster = data.roster || [];

    const batters = [];
    let lhb = 0, rhb = 0, switchH = 0;

    for (const p of roster) {
      const person = p.person;
      const pos = p.position?.abbreviation;

      // Skip pitchers (they don't hit in AL, and we want position players)
      if (pos === 'P') continue;

      const batSide = person?.batSide?.code;
      if (batSide === 'L') lhb++;
      else if (batSide === 'R') rhb++;
      else if (batSide === 'S') switchH++;

      batters.push({
        name: person?.fullName,
        bats: batSide,
        position: pos,
      });
    }

    return { batters, lhbCount: lhb, rhbCount: rhb, switchCount: switchH };
  } catch (err) {
    console.error(`  ❌ Roster ${teamId}: ${err.message}`);
    return { batters: [], lhbCount: 0, rhbCount: 0, switchCount: 0 };
  }
}

/**
 * Calculate platoon advantage for a lineup against a pitcher.
 * @param {Object} roster - From fetchRoster
 * @param {string} pitcherHand - 'L' or 'R'
 * @returns {Object} { adjustment, favorable, unfavorable, neutral }
 */
export function calcPlatoon(roster, pitcherHand) {
  if (!pitcherHand || !roster?.batters?.length) {
    return { adjustment: 0, favorable: 0, unfavorable: 0, neutral: 0 };
  }

  let favorable = 0;
  let unfavorable = 0;
  let neutral = 0;

  // Use the top 8 position players (typical lineup minus pitcher/DH)
  const lineup = roster.batters.slice(0, 8);

  for (const batter of lineup) {
    if (batter.bats === 'S') {
      neutral++; // Switch hitters neutralize the matchup
    } else if (pitcherHand === 'R') {
      if (batter.bats === 'L') favorable++;
      else unfavorable++;
    } else { // LHP
      if (batter.bats === 'R') favorable++;
      else unfavorable++;
    }
  }

  const net = favorable - unfavorable;
  // Each net favorable matchup ≈ +0.04 runs
  const adjustment = net * 0.04;

  return { adjustment, favorable, unfavorable, neutral };
}
