// src/api/pitcher.js — Fetch pitcher stats with home/road splits, rest days, handedness

import { fetchJSON, loadConfig } from './client.js';

/**
 * Fetch a pitcher's season game log with home/road splits, recent form, and rest days.
 * @param {number} pitcherId
 * @param {string|number} season
 * @param {string} currentDate - YYYY-MM-DD for rest day calculation
 * @returns {Promise<Object|null>} Aggregated pitching stats with splits + metadata
 */
export async function fetchPitcherStats(pitcherId, season, currentDate) {
  try {
    const url = `https://statsapi.mlb.com/api/v1/people/${pitcherId}/stats?stats=gameLog&season=${season}&group=pitching`;
    const data = await fetchJSON(url, { label: `pitcher ${pitcherId}` });
    const splits = data.stats?.[0]?.splits || [];

    const total = { ip: 0, er: 0, h: 0, bb: 0, k: 0, hr: 0, bf: 0, games: 0, starts: [] };
    const home = { ip: 0, er: 0, h: 0, bb: 0, k: 0, hr: 0, bf: 0, games: 0 };
    const away = { ip: 0, er: 0, h: 0, bb: 0, k: 0, hr: 0, bf: 0, games: 0 };

    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - 14);
    const cutoffStr = cutoff.toISOString().slice(0, 10);
    const recent = { ip: 0, er: 0, h: 0, bb: 0, k: 0, hr: 0, bf: 0, games: 0 };

    for (const s of splits) {
      const stat = s.stat || {};
      const ip = parseFloat(stat.inningsPitched || 0);
      const er = parseInt(stat.earnedRuns || 0);
      const gameData = {
        date: s.date,
        ip,
        er,
        h: parseInt(stat.hits || 0),
        bb: parseInt(stat.baseOnBalls || 0),
        k: parseInt(stat.strikeOuts || 0),
        hr: parseInt(stat.homeRuns || 0),
        isHome: s.isHome,
      };

      total.ip += ip;
      total.er += er;
      total.h += gameData.h;
      total.bb += gameData.bb;
      total.k += gameData.k;
      total.hr += gameData.hr;
      total.bf += parseInt(stat.battersFaced || 0);
      total.games += 1;
      total.starts.push(gameData);

      const dest = gameData.isHome ? home : away;
      dest.ip += ip;
      dest.er += er;
      dest.h += gameData.h;
      dest.bb += gameData.bb;
      dest.k += gameData.k;
      dest.hr += gameData.hr;
      dest.bf += parseInt(stat.battersFaced || 0);
      dest.games += 1;

      if (s.date >= cutoffStr) {
        recent.ip += ip;
        recent.er += er;
        recent.h += gameData.h;
        recent.bb += gameData.bb;
        recent.k += gameData.k;
        recent.hr += gameData.hr;
        recent.bf += parseInt(stat.battersFaced || 0);
        recent.games += 1;
      }
    }

    total.starts.sort((a, b) => b.date.localeCompare(a.date));

    // Calculate rest days
    let restDays = null;
    if (total.starts.length >= 2 && currentDate) {
      const lastStart = total.starts[0].date;
      const diff = (new Date(currentDate) - new Date(lastStart)) / (1000 * 60 * 60 * 24);
      restDays = Math.round(diff * 10) / 10;
    }

    return { total, home, away, recent, restDays };
  } catch (err) {
    console.error(`  ❌ Pitcher ${pitcherId}: ${err.message}`);
    return null;
  }
}

/**
 * Fetch a pitcher's handedness from the people endpoint.
 * @param {number} pitcherId
 * @returns {Promise<string|null>} 'L' or 'R'
 */
export async function fetchPitcherHand(pitcherId) {
  try {
    const url = `https://statsapi.mlb.com/api/v1/people/${pitcherId}`;
    const data = await fetchJSON(url, { retries: 1, label: `pitcher hand ${pitcherId}` });
    return data.people?.[0]?.pitchHand?.code || null;
  } catch {
    return null;
  }
}
