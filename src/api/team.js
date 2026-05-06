// src/api/team.js — Fetch team hitting stats with home/road splits

import { fetchJSON } from './client.js';

/**
 * Fetch a team's season hitting stats with home/road splits and recent form.
 * @param {number} teamId
 * @param {string|number} season
 * @returns {Promise<Object>} Aggregated hitting stats with splits
 */
export async function fetchTeamStats(teamId, season) {
  try {
    const url = `https://statsapi.mlb.com/api/v1/teams/${teamId}/stats?stats=gameLog&season=${season}&group=hitting`;
    const data = await fetchJSON(url, { label: `team ${teamId}` });
    const splits = data.stats?.[0]?.splits || [];

    const total = { runs: 0, pa: 0, hr: 0, games: 0 };
    const home = { runs: 0, pa: 0, hr: 0, games: 0 };
    const away = { runs: 0, pa: 0, hr: 0, games: 0 };
    const recent = { runs: 0, pa: 0, hr: 0, games: 0 }; // last 14 days
    const games_log = [];

    // 14 days ago for recent form
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - 14);
    const cutoffStr = cutoff.toISOString().slice(0, 10);

    for (const s of splits) {
      const stat = s.stat || {};
      const gameData = {
        date: s.date,
        runs: parseInt(stat.runs || 0),
        pa: parseInt(stat.plateAppearances || 0),
        hr: parseInt(stat.homeRuns || 0),
        isHome: s.isHome,
      };

      // Total
      total.runs += gameData.runs;
      total.pa += gameData.pa;
      total.hr += gameData.hr;
      total.games += 1;

      // Home/road
      if (gameData.isHome) {
        home.runs += gameData.runs;
        home.pa += gameData.pa;
        home.hr += gameData.hr;
        home.games += 1;
      } else {
        away.runs += gameData.runs;
        away.pa += gameData.pa;
        away.hr += gameData.hr;
        away.games += 1;
      }

      // Recent form (last 14 days)
      if (s.date >= cutoffStr) {
        recent.runs += gameData.runs;
        recent.pa += gameData.pa;
        recent.hr += gameData.hr;
        recent.games += 1;
      }

      games_log.push(gameData);
    }

    return { total, home, away, recent, games_log };
  } catch (err) {
    console.error(`  ❌ Team ${teamId}: ${err.message}`);
    return null;
  }
}
