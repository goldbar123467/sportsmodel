// src/model/project.js — Final projection engine (v2.1 with park-adjusted pitchers)

import { ratePitcher } from './fip.js';
import { rateOffense } from './offense.js';
import { getParkFactor, altitudeAdjustment, getStadium } from './park.js';
import { weatherAdjustment } from './weather.js';
import { loadConfig } from '../api/client.js';

/**
 * Project total runs for a game (v2.1).
 *
 * Key improvements over v2:
 * - Park factor applies to pitcher ERAs (Coors inflates pitcher stats too)
 * - Away pitcher gets park-adjusted for the venue they're pitching in
 * - Partial data blending for early-season accuracy
 *
 * @param {Object} params
 * @returns {Object} { projected, confidence, breakdown }
 */
export function project({
  homeAbbr,
  awayPitcherLog,
  homePitcherLog,
  awayTeamLog,
  homeTeamLog,
  weather,
}) {
  const config = loadConfig();
  const LG = config.leagueAvgRunsPerGame;

  // Park factor for this stadium
  const parkFactor = getParkFactor(homeAbbr);
  const stadium = getStadium(homeAbbr);
  const altAdj = altitudeAdjustment(stadium?.altitude || 0);
  const totalPark = parkFactor + altAdj;

  // Rate pitchers (raw, before park adjustment)
  const awayPitcher = ratePitcher(awayPitcherLog, false);
  const homePitcher = ratePitcher(homePitcherLog, true);

  // Park-adjust pitcher ERAs
  // The away pitcher is pitching IN this park, so their ERA gets inflated/deflated
  // The home pitcher is also in this park, same adjustment
  // But: park factor affects both pitchers equally since they're in the same stadium
  const awayPitcherAdj = awayPitcher.blended * totalPark;
  const homePitcherAdj = homePitcher.blended * totalPark;

  // Rate offenses (with home/road splits)
  const awayOffense = rateOffense(awayTeamLog, false);
  const homeOffense = rateOffense(homeTeamLog, true);

  // Project runs for each team
  // Away runs = lg_avg * away_offense * (home_pitcher_park_adjusted / lg_avg)
  // Home runs = lg_avg * home_offense * (away_pitcher_park_adjusted / lg_avg)
  const awayRunsRaw = LG * awayOffense.multiplier * (homePitcherAdj / LG);
  const homeRunsRaw = LG * homeOffense.multiplier * (awayPitcherAdj / LG);

  // Weather adjustment
  const windAdj = weatherAdjustment(weather, stadium?.cfBearing || 0, stadium);

  // Final projection
  const total = awayRunsRaw + homeRunsRaw + windAdj;

  // Confidence based on data quality
  const confidences = [
    awayPitcher.confidence,
    homePitcher.confidence,
    awayOffense.confidence,
    homeOffense.confidence,
  ];
  const lowCount = confidences.filter(c => c === 'low').length;
  const overallConfidence = lowCount >= 2 ? 'low' : lowCount === 1 ? 'medium' : 'high';

  return {
    projected: Math.round(total * 100) / 100,
    confidence: overallConfidence,
    breakdown: {
      awayStarter: { name: null, ...awayPitcher, parkAdjusted: awayPitcherAdj },
      homeStarter: { name: null, ...homePitcher, parkAdjusted: homePitcherAdj },
      awayOffense: { ...awayOffense },
      homeOffense: { ...homeOffense },
      parkFactor,
      altitudeAdj: altAdj,
      totalPark,
      weatherAdj: windAdj,
    },
  };
}
