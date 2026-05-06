// src/api/schedule.js — Fetch MLB schedule for a given date

import { fetchJSON, loadTeams } from './client.js';

/**
 * Fetch all games for a date.
 * @param {string} date - YYYY-MM-DD
 * @returns {Promise<Array>} Array of game objects
 */
export async function fetchSchedule(date) {
  const teams = loadTeams();
  const url = `https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=${date}&hydrate=probablePitcher,venue,team`;
  const data = await fetchJSON(url, { label: 'schedule' });

  const games = [];
  for (const dateEntry of data.dates || []) {
    for (const g of dateEntry.games || []) {
      const awayTeam = teams.teams[g.teams.away.team.id];
      const homeTeam = teams.teams[g.teams.home.team.id];

      games.push({
        gamePk: g.gamePk,
        away: {
          id: g.teams.away.team.id,
          name: g.teams.away.team.name,
          abbr: awayTeam?.abbr || g.teams.away.team.abbreviation,
          starter: g.teams.away.probablePitcher,
        },
        home: {
          id: g.teams.home.team.id,
          name: g.teams.home.team.name,
          abbr: homeTeam?.abbr || g.teams.home.team.abbreviation,
          starter: g.teams.home.probablePitcher,
        },
      });
    }
  }
  return games;
}
