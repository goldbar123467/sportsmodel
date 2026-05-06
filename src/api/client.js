// src/api/client.js — Shared fetch helpers with retry logic

import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const ROOT = join(__dirname, '..', '..');

// Load config
let _config = null;
export function loadConfig() {
  if (!_config) {
    _config = JSON.parse(readFileSync(join(ROOT, 'config', 'defaults.json'), 'utf8'));
  }
  return _config;
}

let _teams = null;
export function loadTeams() {
  if (!_teams) {
    _teams = JSON.parse(readFileSync(join(ROOT, 'config', 'teams.json'), 'utf8'));
  }
  return _teams;
}

// Load .env
let _envLoaded = false;
export function loadEnv() {
  if (_envLoaded) return;
  try {
    const envPath = join(ROOT, '.env');
    const lines = readFileSync(envPath, 'utf8').split('\n');
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;
      const eq = trimmed.indexOf('=');
      if (eq === -1) continue;
      const key = trimmed.slice(0, eq).trim();
      const val = trimmed.slice(eq + 1).trim();
      if (!process.env[key]) process.env[key] = val;
    }
  } catch { /* .env not found — that's ok, keys may come from env */ }
  _envLoaded = true;
}

// Fetch with retry
export async function fetchJSON(url, { retries = 3, backoffMs = 1000, label = '' } = {}) {
  let lastErr;
  for (let attempt = 1; attempt <= retries; attempt++) {
    try {
      const r = await fetch(url);
      if (r.status === 429) {
        const wait = parseInt(r.headers.get('retry-after') || '5') * 1000;
        console.log(`  ⏳ Rate limited${label ? ` (${label})` : ''}, waiting ${wait}ms...`);
        await sleep(wait);
        continue;
      }
      if (!r.ok) {
        const text = await r.text().catch(() => '');
        throw new Error(`HTTP ${r.status}: ${text.slice(0, 200)}`);
      }
      return await r.json();
    } catch (err) {
      lastErr = err;
      if (attempt < retries) {
        const wait = backoffMs * attempt;
        console.log(`  ⚠️  Retry ${attempt}/${retries}${label ? ` (${label})` : ''}: ${err.message}`);
        await sleep(wait);
      }
    }
  }
  throw new Error(`Failed after ${retries} attempts${label ? ` (${label})` : ''}: ${lastErr.message}`);
}

export function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}
