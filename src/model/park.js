// src/model/park.js — Park factor adjustments

import { loadTeams } from '../api/client.js';

/**
 * Get the park factor for a team's home stadium.
 * @param {string} homeAbbr - Home team abbreviation
 * @returns {number} Park factor (1.0 = neutral, >1.0 = offense-friendly)
 */
export function getParkFactor(homeAbbr) {
  const teams = loadTeams();
  return teams.parkFactors[homeAbbr] || 1.0;
}

/**
 * Get stadium info for a team.
 * @param {string} abbr
 * @returns {Object|null} Stadium data
 */
export function getStadium(abbr) {
  const teams = loadTeams();
  return teams.stadiums[abbr] || null;
}

/**
 * Calculate altitude adjustment for Coors Field.
 * Ball carries ~6% more per 1000ft above sea level.
 * @param {number} altitudeFt
 * @returns {number} Run multiplier adjustment
 */
export function altitudeAdjustment(altitudeFt) {
  if (!altitudeFt || altitudeFt < 1000) return 0;
  // Conservative: ~2-3% per 1000ft above 1000 (Coors at 5200ft ≈ +0.08-0.12)
  return ((altitudeFt - 1000) / 1000) * 0.03;
}
