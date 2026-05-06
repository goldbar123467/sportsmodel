// src/model/offense.js — Team offensive rating with home/road splits and recent form

import { loadConfig } from '../api/client.js';

/**
 * Blend a partial stat with league average.
 */
function blendWithLeagueAvg(rpa, pa, config) {
  const minPA = config.minTeamPA || 200;
  const minPartial = config.minTeamPAPartial || 50;
  const blendPct = config.partialDataBlend || 0.5;

  if (pa >= minPA) return rpa;
  if (pa < minPartial) return null;

  const pct = ((pa - minPartial) / (minPA - minPartial)) * blendPct;
  return rpa * pct + config.leagueAvgRunsPerPA * (1 - pct);
}

/**
 * Rate a team's offense for a specific context (home or away).
 * Uses home/road splits + recent form + partial data blending.
 */
export function rateOffense(teamLog, isHome = false) {
  const config = loadConfig();
  const minPA = config.minTeamPA || 200;
  const minPartial = config.minTeamPAPartial || 50;

  if (!teamLog) {
    return {
      multiplier: 1.0,
      runsPerPA: config.leagueAvgRunsPerPA,
      confidence: 'low',
      source: 'default',
    };
  }

  const split = isHome ? teamLog.home : teamLog.away;
  const splitMinPA = minPA / 2;

  let runsPerPA, confidence, source;

  if (split && split.pa >= splitMinPA) {
    const rawRPA = split.runs / split.pa;
    runsPerPA = blendWithLeagueAvg(rawRPA, split.pa, config) || config.leagueAvgRunsPerPA;
    confidence = split.pa >= minPA ? 'high' : 'medium';
    source = isHome ? 'home' : 'road';
  } else {
    const rawRPA = teamLog.total.runs / teamLog.total.pa;
    runsPerPA = blendWithLeagueAvg(rawRPA, teamLog.total.pa, config) || config.leagueAvgRunsPerPA;
    confidence = teamLog.total.pa >= minPA ? 'medium' : 'low';
    source = 'total';
  }

  // Recent form adjustment (last 14 days)
  let recentMult = 1.0;
  if (teamLog.recent && teamLog.recent.pa >= 50) {
    const recentRPA = teamLog.recent.runs / teamLog.recent.pa;
    const seasonRPA = teamLog.total.runs / teamLog.total.pa;
    if (seasonRPA > 0) {
      recentMult = recentRPA / seasonRPA;
      // Clamp to prevent extreme swings (0.85 to 1.15)
      recentMult = Math.max(0.85, Math.min(1.15, recentMult));
    }
  }

  const adjustedRPA = runsPerPA * recentMult;
  const multiplier = adjustedRPA / config.leagueAvgRunsPerPA;

  return { multiplier, runsPerPA: adjustedRPA, confidence, source };
}
