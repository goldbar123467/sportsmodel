// scripts/edge-calc.js — Tactician edge calculator
// Computes advanced metrics from raw data and returns adjustment values.
// Usage: import and call individual functions, or run standalone for testing.

// Note: standalone test mode at bottom of file

// ═══════════════════════════════════════════════════════════════════
// § PITCHER FATIGUE MODEL
// ═══════════════════════════════════════════════════════════════════

/**
 * Calculate third-time-through-order penalty.
 * @param {Object} pitcherLog - { starts: [{ ip }] }
 * @param {number} teamLineupLen - number of batters faced (estimate)
 * @returns {number} Run adjustment (positive = more runs allowed)
 */
export function ttoPenalty(pitcherLog, avgLineupSpots = 9) {
  if (!pitcherLog || !pitcherLog.starts) return 0;

  const avgIP = pitcherLog.total?.ip / Math.max(pitcherLog.games || 1, 1);
  // Average start: ~5 IP = ~19 batters = most face lineup 2x
  // 6+ IP = ~22+ batters = top of order sees 3rd time
  if (avgIP >= 6.5) return 0.4; // Likely 3rd time through
  if (avgIP >= 5.5) return 0.2; // Borderline
  return 0;
}

/**
 * Calculate pitch count fatigue adjustment.
 * @param {number} avgPitchCount - pitcher's average pitches per start
 * @returns {number} Run adjustment
 */
export function pitchCountFatigue(avgPitchCount) {
  if (avgPitchCount >= 115) return 0.7;
  if (avgPitchCount >= 105) return 0.4;
  if (avgPitchCount >= 95) return 0.2;
  if (avgPitchCount >= 85) return 0.0;
  return -0.1; // Fresh, efficient
}

/**
 * Calculate days-rest adjustment.
 * @param {number} daysRest - days since last start (2, 3, 4, 5, 6+)
 * @returns {number} Run adjustment
 */
export function daysRestAdjustment(daysRest) {
  if (daysRest <= 2) return 0.3;  // Short rest
  if (daysRest === 3) return 0.0; // Normal
  if (daysRest === 4) return -0.1; // Extra day
  return -0.2; // Extended rest (but possible rust if 6+)
}

/**
 * Calculate season workload decay.
 * @param {number} seasonIP - innings pitched this season
 * @returns {number} Run adjustment per start
 */
export function workloadDecay(seasonIP) {
  if (seasonIP >= 200) return 0.2;
  if (seasonIP >= 180) return 0.1;
  if (seasonIP >= 140) return 0.05;
  return 0;
}

// ═══════════════════════════════════════════════════════════════════
// § BATTED BALL QUALITY
// ═══════════════════════════════════════════════════════════════════

/**
 * Calculate offense adjustment from barrel rate.
 * @param {number} barrelRate - team barrel rate as decimal (0.08 = 8%)
 * @returns {number} Run adjustment per game
 */
export function barrelRateAdjustment(barrelRate) {
  const leagueAvg = 0.075; // 7.5%
  const diff = barrelRate - leagueAvg;
  // Each 1% above average ≈ +0.15 runs per game
  return diff * 15;
}

/**
 * Calculate offense adjustment from hard-hit rate.
 * @param {number} hardHitRate - team hard hit rate as decimal
 * @returns {number} Run adjustment
 */
export function hardHitAdjustment(hardHitRate) {
  const leagueAvg = 0.34;
  const diff = hardHitRate - leagueAvg;
  return diff * 3; // Each 1% above avg ≈ +0.03 runs
}

// ═══════════════════════════════════════════════════════════════════
// § PLATOON SPLITS
// ═══════════════════════════════════════════════════════════════════

/**
 * Calculate platoon advantage from lineup vs pitcher handedness.
 * @param {Array} lineupSplits - [{ name, bats: 'L'|'R'|'S' }]
 * @param {string} pitcherHand - 'L' or 'R'
 * @returns {number} Run adjustment
 */
export function platoonAdvantage(lineupSplits, pitcherHand) {
  let favorable = 0;
  let unfavorable = 0;

  for (const hitter of lineupSplits.slice(0, 9)) {
    if (hitter.bats === 'S') continue; // Switch hitters neutralize
    if (pitcherHand === 'R') {
      if (hitter.bats === 'L') favorable++;
      else unfavorable++;
    } else { // LHP
      if (hitter.bats === 'R') favorable++;
      else unfavorable++;
    }
  }

  const net = favorable - unfavorable;
  return net * 0.04; // Each net favorable matchup ≈ +0.04 runs
}

// ═══════════════════════════════════════════════════════════════════
// § BULLPEN FATIGUE
// ═══════════════════════════════════════════════════════════════════

/**
 * Calculate bullpen fatigue adjustment.
 * @param {Object} bullpenUsage - { consecutiveDays: number, totalPitchesWeek: number, closerAvailable: boolean }
 * @returns {number} Run adjustment
 */
export function bullpenFatigue(bullpenUsage) {
  if (!bullpenUsage) return 0;

  let adj = 0;

  // Consecutive days effect
  if (bullpenUsage.consecutiveDays >= 4) adj += 0.4;
  else if (bullpenUsage.consecutiveDays >= 3) adj += 0.2;
  else if (bullpenUsage.consecutiveDays >= 2) adj += 0.1;

  // Weekly workload
  if (bullpenUsage.totalPitchesWeek >= 80) adj += 0.2;
  else if (bullpenUsage.totalPitchesWeek >= 60) adj += 0.1;

  // Closer unavailable
  if (bullpenUsage.closerAvailable === false) adj += 0.2;

  return adj;
}

// ═══════════════════════════════════════════════════════════════════
// § WEATHER BALL-FLIGHT PHYSICS
// ═══════════════════════════════════════════════════════════════════

/**
 * Calculate air density from conditions.
 * @param {number} tempF - temperature in Fahrenheit
 * @param {number} humidity - relative humidity (0-100)
 * @param {number} altitudeFt - altitude in feet
 * @param {number} pressureInHg - barometric pressure (default 29.92)
 * @returns {number} Air density in kg/m³
 */
export function airDensity(tempF, humidity, altitudeFt, pressureInHg = 29.92) {
  // Convert to metric
  const tempC = (tempF - 32) * 5 / 9;
  const T = tempC + 273.15; // Kelvin
  const P = pressureInHg * 3386.39; // Convert inHg to Pa
  const alt_m = altitudeFt * 0.3048; // Convert to meters

  // Altitude pressure drop (barometric formula)
  const P_alt = P * Math.exp(-alt_m / 8500);

  // Saturation vapor pressure (Magnus formula)
  const es = 610.78 * Math.exp((17.27 * tempC) / (tempC + 237.3));
  const pv = (humidity / 100) * es;

  // Air density (ideal gas law with humidity correction)
  const Rd = 287.05; // Dry air constant
  const Rv = 461.495; // Water vapor constant
  const rho = (P_alt - pv) / (Rd * T) + pv / (Rv * T);

  return rho;
}

/**
 * Calculate carry distance change from air density.
 * @param {number} density - actual air density (kg/m³)
 * @returns {number} Multiplier (1.0 = no change, >1 = farther)
 */
export function carryMultiplier(density) {
  const standard = 1.225;
  return 1 + (1 - density / standard) * 1.5; // 1.5x Magnus amplification
}

/**
 * Calculate full weather run adjustment from physics.
 * @param {Object} weather - { tempF, humidity, windMph, windDeg }
 * @param {number} altitudeFt
 * @param {number} cfBearing - center field bearing
 * @returns {number} Run adjustment
 */
export function weatherPhysicsAdjustment(weather, altitudeFt = 0, cfBearing = 0) {
  if (!weather) return 0;

  const density = airDensity(
    weather.tempF || 70,
    weather.humidity || 50,
    altitudeFt
  );
  const carry = carryMultiplier(density);

  // Carry effect: each 1% extra carry ≈ +0.01 runs
  // Conservative: ~0.08 runs per 1% carry change
  let adj = (carry - 1) * 100 * 0.08;

  // Wind (vector toward center field)
  if (weather.windMph && cfBearing) {
    const towardCF = (weather.windDeg + 180) % 360;
    let diff = Math.abs(towardCF - cfBearing);
    if (diff > 180) diff = 360 - diff;
    const windComponent = weather.windMph * Math.cos(diff * Math.PI / 180);
    adj += windComponent >= 0
      ? windComponent * 0.10
      : windComponent * 0.08;
  }

  return adj;
}

// ═══════════════════════════════════════════════════════════════════
// § SITUATIONAL FACTORS
// ═══════════════════════════════════════════════════════════════════

/**
 * Calculate travel fatigue.
 * @param {Object} travel - { crossCountry: boolean, timezones: number, daysSinceTravel: number }
 * @returns {number} Run adjustment (applied to traveling team)
 */
export function travelFatigue(travel) {
  if (!travel) return 0;
  let adj = 0;
  if (travel.crossCountry) adj += 0.15;
  else if (travel.timezones >= 2) adj += 0.10;
  else if (travel.timezones === 1) adj += 0.05;

  // Effect fades over days
  if (travel.daysSinceTravel === 0) return adj;
  if (travel.daysSinceTravel === 1) return adj * 0.5;
  return 0;
}

/**
 * Calculate scheduling spot adjustment.
 * @param {Object} spot - { dayGameAfterNight: boolean, gamesInRow: number, daysSinceOff: number }
 * @returns {number} Run adjustment
 */
export function schedulingSpot(spot) {
  if (!spot) return 0;
  let adj = 0;

  if (spot.dayGameAfterNight) adj += 0.15;
  if (spot.gamesInRow >= 14) adj += 0.3;
  else if (spot.gamesInRow >= 10) adj += 0.2;
  else if (spot.gamesInRow >= 7) adj += 0.1;

  if (spot.daysSinceOff >= 6) adj += 0.2;
  else if (spot.daysSinceOff >= 4) adj += 0.1;

  return adj;
}

/**
 * Calculate lineup depth advantage.
 * @param {Array} obpStats - OBP for 1-9 in lineup
 * @returns {number} Run adjustment vs league average
 */
export function lineupDepth(obpStats) {
  if (!obpStats || obpStats.length < 9) return 0;
  const avgOBP = obpStats.reduce((s, v) => s + v, 0) / obpStats.length;
  const leagueAvg = 0.310;
  return (avgOBP - leagueAvg) * 10; // Each .010 OBP above avg ≈ +0.1 runs
}

// ═══════════════════════════════════════════════════════════════════
// § COMBINED TACTICIAN SCORE
// ═══════════════════════════════════════════════════════════════════

/**
 * Calculate total tactician adjustment for a game.
 * @param {Object} data - All the inputs for each factor
 * @returns {Object} { total, breakdown, confidence }
 */
export function tacticianScore(data) {
  const breakdown = {};
  let total = 0;

  // Pitcher factors
  if (data.pitcher) {
    breakdown.tto = ttoPenalty(data.pitcher);
    breakdown.pitchCount = pitchCountFatigue(data.pitcher.avgPitchCount);
    breakdown.workload = workloadDecay(data.pitcher.seasonIP);
    total += breakdown.tto + breakdown.pitchCount + breakdown.workload;
  }

  // Bullpen
  if (data.bullpen) {
    breakdown.bullpen = bullpenFatigue(data.bullpen);
    total += breakdown.bullpen;
  }

  // Weather physics
  if (data.weather && data.altitudeFt !== undefined) {
    breakdown.weather = weatherPhysicsAdjustment(data.weather, data.altitudeFt, data.cfBearing);
    total += breakdown.weather;
  }

  // Situational
  if (data.travel) {
    breakdown.travel = travelFatigue(data.travel);
    total += breakdown.travel;
  }
  if (data.scheduling) {
    breakdown.scheduling = schedulingSpot(data.scheduling);
    total += breakdown.scheduling;
  }

  // Batted ball
  if (data.barrelRate !== undefined) {
    breakdown.barrel = barrelRateAdjustment(data.barrelRate);
    total += breakdown.barrel;
  }

  // Lineup
  if (data.lineupOBP) {
    breakdown.lineup = lineupDepth(data.lineupOBP);
    total += breakdown.lineup;
  }

  // Confidence: how many factors did we actually compute?
  const factorsComputed = Object.keys(breakdown).filter(k => breakdown[k] !== 0).length;
  const totalFactors = Object.keys(breakdown).length;
  const confidence = totalFactors >= 5 ? 'high' : totalFactors >= 3 ? 'medium' : 'low';

  return {
    total: Math.round(total * 100) / 100,
    breakdown,
    factorsComputed,
    confidence,
  };
}

// ═══════════════════════════════════════════════════════════════════
// § STANDALONE TESTING
// ═══════════════════════════════════════════════════════════════════

if (process.argv[1] && process.argv[1].endsWith('edge-calc.js')) {
  console.log('🧠 Tactician Edge Calculator — Test Mode\n');

  // Test air density
  const d1 = airDensity(70, 50, 0);
  const d2 = airDensity(90, 80, 0);
  const d3 = airDensity(70, 50, 5200);
  console.log('Air Density Tests:');
  console.log(`  70°F, 50%, sea level: ${d1.toFixed(4)} kg/m³ (std: 1.225)`);
  console.log(`  90°F, 80%, sea level: ${d2.toFixed(4)} kg/m³`);
  console.log(`  70°F, 50%, Coors:     ${d3.toFixed(4)} kg/m³`);
  console.log(`  Carry multiplier Coors: ${carryMultiplier(d3).toFixed(3)}x`);
  console.log('');

  // Test full score
  const score = tacticianScore({
    pitcher: { avgPitchCount: 98, seasonIP: 85 },
    bullpen: { consecutiveDays: 3, totalPitchesWeek: 65, closerAvailable: true },
    weather: { tempF: 88, humidity: 70, windMph: 8, windDeg: 180 },
    altitudeFt: 0,
    cfBearing: 45,
    travel: { crossCountry: true, timezones: 3, daysSinceTravel: 0 },
    scheduling: { dayGameAfterNight: false, gamesInRow: 5, daysSinceOff: 3 },
    barrelRate: 0.09,
    lineupOBP: [.350, .330, .360, .310, .290, .320, .280, .300, .270],
  });

  console.log('Sample Tactician Score:');
  console.log(`  Total adjustment: ${score.total > 0 ? '+' : ''}${score.total} runs`);
  console.log(`  Confidence: ${score.confidence} (${score.factorsComputed} factors)`);
  for (const [k, v] of Object.entries(score.breakdown)) {
    if (v !== 0) console.log(`    ${k}: ${v > 0 ? '+' : ''}${v}`);
  }
}
