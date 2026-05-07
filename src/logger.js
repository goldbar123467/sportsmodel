// src/logger.js — Small file logger for daily runs

import { appendFileSync, mkdirSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const ROOT = join(__dirname, '..');
const LOG_DIR = join(ROOT, 'data', 'logs');

function logPath(date) {
  mkdirSync(LOG_DIR, { recursive: true });
  return join(LOG_DIR, `${date}.log`);
}

export function logEvent(date, level, message, meta = {}) {
  const entry = {
    timestamp: new Date().toISOString(),
    level,
    message,
    ...meta,
  };
  appendFileSync(logPath(date), `${JSON.stringify(entry)}\n`);
}

export function logError(date, message, err, meta = {}) {
  logEvent(date, 'error', message, {
    error: err?.message || String(err),
    stack: err?.stack,
    ...meta,
  });
}
