// src/model/fip.js — Pitcher rating via FIP + ERA blend with home/road splits

import { loadConfig } from '../api/client.js';

function calculateFIP(log, constant) {
  if (!log || log.ip <= 0) return null;
  return ((13 * log.hr) + (3 * log.bb) - (2 * log.k)) / log.ip + constant;
}

function calculateERA(log) {
  if (!log || log.ip <= 0) return null;
  return (log.er * 9) / log.ip;
}

function recentFormStats(starts, lastN = 5, weight = 2.0) {
  if (!starts || starts.length === 0) return null;
  const recent = starts.slice(0, lastN);
  const older = starts.slice(lastN);
  const blend = { ip: 0, er: 0, h: 0, bb: 0, k: 0, hr: 0 };
  for (const g of recent) {
    blend.ip += g.ip * weight;
    blend.er += g.er * weight;
    blend.h += g.h * weight;
    blend.bb += g.bb * weight;
    blend.k += g.k * weight;
    blend.hr += g.hr * weight;
  }
  for (const g of older) {
    blend.ip += g.ip;
    blend.er += g.er;
    blend.h += g.h;
    blend.bb += g.bb;
    blend.k += g.k;
    blend.hr += g.hr;
  }
  return blend;
}

/**
 * Blend a partial-season stat set with league average.
 * When a pitcher has 10-30 IP, we trust their data proportionally.
 */
function blendWithLeagueAvg(stat, ip, config) {
  const minIP = config.minPitcherIP || 30;
  const minPartial = config.minPitcherIPPartial || 10;
  const blendPct = config.partialDataBlend || 0.5;

  if (ip >= minIP) return stat; // Enough data, use as-is
  if (ip < minPartial) return null; // Too little data

  // Linear blend: at minPartial IP → 0% real, at minIP → 100% real
  const pct = ((ip - minPartial) / (minIP - minPartial)) * blendPct;
  const lg = config.leagueAvgRunsPerGame;

  return stat * pct + lg * (1 - pct);
}

/**
 * Rate a pitcher for a specific context (home or away).
 * Uses home/road splits + recent form + partial data blending.
 */
export function ratePitcher(pitcherLog, isHome = false) {
  const config = loadConfig();
  const minIP = config.minPitcherIP || 30;
  const minPartial = config.minPitcherIPPartial || 10;
  const totalIP = pitcherLog?.total?.ip || 0;

  // Complete fallback — no data at all or under partial threshold
  if (!pitcherLog || totalIP < minPartial) {
    return {
      era: config.leagueAvgRunsPerGame,
      fip: config.leagueAvgRunsPerGame,
      blended: config.leagueAvgRunsPerGame,
      confidence: 'low',
      ip: totalIP,
      source: 'default',
    };
  }

  // Use home/road split if available
  const split = isHome ? pitcherLog.home : pitcherLog.away;
  const splitMinIP = minIP / 2;

  let baseFIP, baseERA, confidence, source;

  if (split && split.ip >= splitMinIP) {
    baseFIP = calculateFIP(split, config.fipConstant);
    baseERA = calculateERA(split);
    // Blend split stats with league avg if still thin
    baseFIP = blendWithLeagueAvg(baseFIP, split.ip, config) || config.leagueAvgRunsPerGame;
    baseERA = blendWithLeagueAvg(baseERA, split.ip, config) || config.leagueAvgRunsPerGame;
    confidence = split.ip >= minIP ? 'high' : 'medium';
    source = isHome ? 'home' : 'road';
  } else {
    baseFIP = calculateFIP(pitcherLog.total, config.fipConstant);
    baseERA = calculateERA(pitcherLog.total);
    baseFIP = blendWithLeagueAvg(baseFIP, totalIP, config) || config.leagueAvgRunsPerGame;
    baseERA = blendWithLeagueAvg(baseERA, totalIP, config) || config.leagueAvgRunsPerGame;
    confidence = totalIP >= minIP ? 'medium' : 'low';
    source = 'total';
  }

  // Recent-form blend (last 5 starts weighted 2x)
  const recentStats = recentFormStats(
    pitcherLog.total.starts,
    5,
    config.recentFormWeight || 2.0
  );
  const recentFIP = recentStats ? calculateFIP(recentStats, config.fipConstant) : baseFIP;
  const recentERA = recentStats ? calculateERA(recentStats) : baseERA;

  // Blend: 70% base, 30% recent form (stable, not over-reactive)
  const blendedFIP = recentFIP ? baseFIP * 0.7 + recentFIP * 0.3 : baseFIP;
  const blendedERA = recentERA ? baseERA * 0.7 + recentERA * 0.3 : baseERA;

  // Final blend: weighted FIP + ERA
  const blended = blendedFIP * (config.fipWeight || 0.65) + blendedERA * (config.eraWeight || 0.35);

  return { era: baseERA, fip: baseFIP, blended, confidence, ip: totalIP, source };
}
