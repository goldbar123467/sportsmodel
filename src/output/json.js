// src/output/json.js — Save projections to JSON

import { writeFileSync, mkdirSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const ROOT = join(__dirname, '..', '..');
const RESULTS_DIR = join(ROOT, 'data', 'results');

/**
 * Save projection results to a JSON file.
 * @param {string} date - YYYY-MM-DD
 * @param {Array} results - Array of game result objects
 */
export function saveResults(date, results) {
  if (!existsSync(RESULTS_DIR)) {
    mkdirSync(RESULTS_DIR, { recursive: true });
  }

  const filePath = join(RESULTS_DIR, `${date}.json`);
  const output = {
    date,
    timestamp: new Date().toISOString(),
    games: results.map(r => ({
      matchup: `${r.away.abbr} @ ${r.home.abbr}`,
      away: r.away.abbr,
      home: r.home.abbr,
      projected: r.projected,
      combined: r.combined || r.projected,
      line: r.line,
      edge: r.edge,
      pick: r.pick,
      confidence: r.confidence,
      breakdown: r.breakdown || null,
      tactician: r.tactician || null,
    })),
    summary: {
      totalGames: results.length,
      picksMade: results.filter(r => r.pick && r.pick !== 'NO PLAY').length,
      overs: results.filter(r => r.pick === 'OVER').length,
      unders: results.filter(r => r.pick === 'UNDER').length,
      noPlay: results.filter(r => r.pick === 'NO PLAY').length,
    },
  };

  writeFileSync(filePath, JSON.stringify(output, null, 2));
  console.log(`📁 Saved to ${filePath}`);
  return filePath;
}
