// src/validate.js — Input and API shape guards

export function isValidDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return false;
  }
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

export function requireDate(value, label = 'date') {
  if (!isValidDate(value)) {
    throw new Error(`Invalid ${label}: expected YYYY-MM-DD, got "${value}"`);
  }
  return value;
}

export function validateArray(value, label) {
  if (!Array.isArray(value)) {
    throw new Error(`${label} response must be an array`);
  }
  return value;
}

export function validateObject(value, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${label} response must be an object`);
  }
  return value;
}

export function warnStaleOdds(odds, now = new Date()) {
  const warnings = [];
  for (const line of odds || []) {
    if (!line?.commenceTime) continue;
    const start = new Date(line.commenceTime);
    if (Number.isNaN(start.getTime())) continue;
    const ageHours = (now.getTime() - start.getTime()) / 36e5;
    if (ageHours > 4) {
      warnings.push(`${line.away} @ ${line.home}: odds/game start is ${ageHours.toFixed(1)} hours old`);
    }
  }
  return warnings;
}
