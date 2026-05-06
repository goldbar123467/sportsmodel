// src/api/odds.js — Fetch FanDuel totals lines

import { fetchJSON, loadEnv, loadTeams } from './client.js';

/**
 * Fetch FanDuel over/under totals for today's MLB games.
 * @returns {Promise<Array>} Array of { away, home, total, commenceTime }
 */
export async function fetchOdds() {
  loadEnv();
  const apiKey = process.env.ODDS_API_KEY;
  if (!apiKey) {
    console.log('  ⚠️  No ODDS_API_KEY set, skipping odds');
    return [];
  }

  const teams = loadTeams();
  const url = `https://api.the-odds-api.com/v4/sports/baseball_mlb/odds?apiKey=${apiKey}&regions=us&markets=totals&oddsFormat=american&bookmakers=fanduel`;

  try {
    const data = await fetchJSON(url, { label: 'odds' });
    return data.map(g => {
      const fd = g.bookmakers?.find(b => b.key === 'fanduel');
      const totals = fd?.markets.find(m => m.key === 'totals');
      const over = totals?.outcomes.find(o => o.name === 'Over');
      return {
        away: teams.nameToAbbr[g.away_team] || g.away_team,
        home: teams.nameToAbbr[g.home_team] || g.home_team,
        total: over?.point,
        commenceTime: g.commence_time,
      };
    }).filter(x => x.total != null);
  } catch (err) {
    console.error(`  ❌ Odds API: ${err.message}`);
    return [];
  }
}
