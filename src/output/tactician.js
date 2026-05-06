// src/output/tactician.js — Display tactician analysis per game

/**
 * Print tactician breakdown for a single game.
 * @param {string} matchup - "AWAY @ HOME"
 * @param {Object} tactician - { total, breakdown, confidence, factorsComputed }
 */
export function printTactician(matchup, tactician) {
  if (!tactician) return;

  const sign = tactician.total > 0 ? '+' : '';
  console.log(`  🧠 Tactician: ${sign}${tactician.total.toFixed(2)} runs (${tactician.confidence} confidence)`);

  // Only show non-zero factors
  const entries = Object.entries(tactician.breakdown).filter(([, v]) => Math.abs(v) > 0.01);
  if (entries.length > 0) {
    for (const [key, val] of entries) {
      const label = {
        tto: '3rd-time-through',
        pitchCount: 'Pitch count fatigue',
        workload: 'Season workload',
        bullpen: 'Bullpen fatigue',
        weather: 'Weather physics',
        travel: 'Travel fatigue',
        scheduling: 'Scheduling spot',
        barrel: 'Barrel rate',
        lineup: 'Lineup depth',
        hardHit: 'Hard-hit rate',
        platoon: 'Platoon advantage',
      }[key] || key;
      const v = val > 0 ? `+${val.toFixed(2)}` : val.toFixed(2);
      console.log(`    ${label}: ${v}`);
    }
  }
}

/**
 * Print the combined base + tactician projection.
 * @param {Object} result - Game result with base and tactician data
 */
export function printCombined(result) {
  const base = result.projected;
  const tact = result.tactician?.total || 0;
  const combined = base + tact;
  const sign = tact > 0 ? '+' : '';

  if (Math.abs(tact) > 0.01) {
    console.log(`  📊 Base: ${base.toFixed(2)} → Combined: ${combined.toFixed(2)} (${sign}${tact.toFixed(2)} tactician)`);
  }
}
