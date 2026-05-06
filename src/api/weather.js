// src/api/weather.js — Fetch weather with rate limiting

import { fetchJSON, sleep, loadConfig, loadEnv } from './client.js';

/**
 * Fetch weather for a stadium location.
 * @param {number} lat
 * @param {number} lon
 * @returns {Promise<Object|null>} { tempF, windMph, windDeg, humidity }
 */
export async function fetchWeather(lat, lon) {
  loadEnv();
  const apiKey = process.env.OPENWEATHER_API_KEY;
  if (!apiKey) return null;

  try {
    const url = `https://api.openweathermap.org/data/2.5/weather?lat=${lat}&lon=${lon}&appid=${apiKey}&units=imperial`;
    const d = await fetchJSON(url, { retries: 2, backoffMs: 500, label: 'weather' });
    return {
      tempF: d.main?.temp,
      windMph: d.wind?.speed || 0,
      windDeg: d.wind?.deg || 0,
      humidity: d.main?.humidity || 50,
    };
  } catch {
    return null;
  }
}

/**
 * Fetch weather for multiple stadiums with rate limiting.
 * @param {Object} stadiums - Stadium data from teams.json
 * @returns {Promise<Object>} Map of abbr → weather data
 */
export async function fetchAllWeather(stadiums) {
  const config = loadConfig();
  const weather = {};
  const delay = config.weatherRequestDelayMs || 200;

  for (const [abbr, stadium] of Object.entries(stadiums)) {
    if (stadium.roof) {
      weather[abbr] = null;
      continue;
    }
    weather[abbr] = await fetchWeather(stadium.lat, stadium.lon);
    if (delay > 0) await sleep(delay);
  }

  return weather;
}
