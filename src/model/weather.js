// src/model/weather.js — Wind, temperature, & humidity adjustments

import { loadConfig } from '../api/client.js';

function windOutComponent(windMph, windDeg, cfBearing) {
  const towardCF = (windDeg + 180) % 360;
  let diff = Math.abs(towardCF - cfBearing);
  if (diff > 180) diff = 360 - diff;
  return windMph * Math.cos(diff * Math.PI / 180);
}

/**
 * Calculate weather adjustment in runs.
 * Now includes humidity factor.
 *
 * @param {Object} weather - { tempF, windMph, windDeg, humidity }
 * @param {number} cfBearing - Center field bearing in degrees
 * @param {Object} stadium - Stadium data (for dome check)
 * @returns {number} Run adjustment (positive = more offense)
 */
export function weatherAdjustment(weather, cfBearing, stadium) {
  if (!weather || stadium?.roof) return 0;

  const config = loadConfig();
  let adj = 0;

  // Wind adjustment
  const windOut = windOutComponent(weather.windMph, weather.windDeg, cfBearing);
  if (windOut >= 0) {
    adj += windOut * (config.windOutRunsPerMPH || 0.10);
  } else {
    adj += windOut * (config.windInRunsPerMPH || 0.08);
  }

  // Temperature adjustment
  const tempF = weather.tempF || 70;
  adj += ((tempF - 70) / 10) * (config.tempRunsPer10F || 0.15);

  // High heat boost (>90°F = more offense)
  if (tempF > (config.tempHighThreshold || 90)) {
    adj += config.tempHighBoost || 0.10;
  }

  // Humidity factor (high humidity = ball carries farther)
  // Above 70% humidity adds offense, below 40% suppresses
  const humidity = weather.humidity || 50;
  if (humidity > 70) {
    adj += ((humidity - 70) / 10) * (config.humidityRunsPer10Pct || 0.08);
  } else if (humidity < 40) {
    adj -= ((40 - humidity) / 10) * (config.humidityRunsPer10Pct || 0.08);
  }

  return adj;
}
