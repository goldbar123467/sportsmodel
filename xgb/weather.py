#!/usr/bin/env python3
"""api.weather.gov weather ingestion for MLB games."""

import argparse
import json
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from db import Database, DB_PATH
from util import validate_date

SCRIPT_DIR = Path(__file__).parent
CONFIG_DIR = SCRIPT_DIR.parent / "config"
NWS_BASE_URL = "https://api.weather.gov"
WEATHERBIT_BASE_URL = "https://api.weatherbit.io/v2.0"
ENV_PATH = SCRIPT_DIR.parent / ".env"
DEFAULT_USER_AGENT = os.environ.get(
    "NWS_USER_AGENT",
    "SportsBotv2 MLB Weather (set NWS_USER_AGENT with contact info)",
)
MAX_WEATHERBIT_RETRY_AFTER_SECONDS = 60


class WeatherbitRateLimitError(RuntimeError):
    def __init__(self, retry_after):
        super().__init__(f"Weatherbit rate limited; retry after {retry_after} seconds")
        self.retry_after = retry_after


def load_team_data():
    with open(CONFIG_DIR / "teams.json") as f:
        return json.load(f)


def load_env_file(path=ENV_PATH):
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def weatherbit_api_key():
    load_env_file()
    return os.environ.get("WEATHERBIT_API_KEY")


def nws_headers(user_agent=None):
    return {
        "User-Agent": user_agent or DEFAULT_USER_AGENT,
        "Accept": "application/geo+json",
    }


def parse_time(value):
    if isinstance(value, datetime):
        dt = value
    elif value:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def game_time_utc(game):
    parsed = parse_time(game.get("game_time_utc") or game.get("gameDate"))
    if parsed:
        return parsed
    date_value = game.get("date")
    if not date_value:
        return datetime.now(timezone.utc)
    return datetime.strptime(str(date_value), "%Y-%m-%d").replace(
        hour=19,
        minute=0,
        tzinfo=timezone.utc,
    )


def iso_z(dt):
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fetch_json(session, url, params=None, user_agent=None, timeout=20):
    response = session.get(
        url,
        params=params,
        headers=nws_headers(user_agent),
        timeout=timeout,
    )
    if response.status_code == 429:
        retry = int(response.headers.get("Retry-After", 5))
        time.sleep(retry)
        response = session.get(
            url,
            params=params,
            headers=nws_headers(user_agent),
            timeout=timeout,
        )
    if response.status_code != 200:
        return {}
    return response.json()


def fetch_weatherbit_json(
    session,
    path,
    params,
    timeout=20,
    max_retry_after=MAX_WEATHERBIT_RETRY_AFTER_SECONDS,
):
    response = session.get(
        f"{WEATHERBIT_BASE_URL}{path}",
        params=params,
        timeout=timeout,
    )
    if response.status_code == 429:
        retry = int(response.headers.get("Retry-After", 5))
        if retry > max_retry_after:
            raise WeatherbitRateLimitError(retry)
        time.sleep(retry)
        response = session.get(
            f"{WEATHERBIT_BASE_URL}{path}",
            params=params,
            timeout=timeout,
        )
    if response.status_code != 200:
        return {}
    return response.json()


def compact_coord(value):
    return f"{float(value):.4f}".rstrip("0").rstrip(".")


def point_properties(stadium, session=requests, user_agent=None):
    lat = compact_coord(stadium["lat"])
    lon = compact_coord(stadium["lon"])
    data = fetch_json(session, f"{NWS_BASE_URL}/points/{lat},{lon}", user_agent=user_agent)
    return data.get("properties", {})


def station_id_from_feature(feature):
    props = feature.get("properties", {})
    station = props.get("stationIdentifier")
    if station:
        return station
    feature_id = feature.get("id") or ""
    return feature_id.rstrip("/").split("/")[-1] or None


def observation_stations(stadium, session=requests, user_agent=None):
    props = point_properties(stadium, session=session, user_agent=user_agent)
    stations_url = props.get("observationStations")
    if not stations_url:
        return []
    data = fetch_json(session, stations_url, user_agent=user_agent)
    stations = []
    for feature in data.get("features", []):
        station = station_id_from_feature(feature)
        if station:
            stations.append(station)
    return stations


def scalar(prop):
    if isinstance(prop, dict):
        return prop.get("value")
    return prop


def unit_code(prop):
    if isinstance(prop, dict):
        return str(prop.get("unitCode") or "")
    return ""


def temp_to_f(value, unit=""):
    if value is None:
        return None
    value = float(value)
    unit = unit.lower()
    if "degc" in unit or unit == "c":
        return value * 9 / 5 + 32
    return value


def wind_to_mph(value, unit=""):
    if value is None:
        return None
    value = float(value)
    unit = unit.lower()
    if "km_h" in unit or "km/h" in unit:
        return value * 0.621371
    if "m_s" in unit or "m/s" in unit:
        return value * 2.23694
    return value


def pressure_to_mb(value, unit=""):
    if value is None:
        return None
    value = float(value)
    unit = unit.lower()
    if unit.endswith(":pa") or "unit:pa" in unit or "wmoUnit:Pa".lower() in unit:
        return value / 100
    return value


CARDINAL_TO_DEGREES = {
    "N": 0,
    "NNE": 22.5,
    "NE": 45,
    "ENE": 67.5,
    "E": 90,
    "ESE": 112.5,
    "SE": 135,
    "SSE": 157.5,
    "S": 180,
    "SSW": 202.5,
    "SW": 225,
    "WSW": 247.5,
    "W": 270,
    "WNW": 292.5,
    "NW": 315,
    "NNW": 337.5,
}


def direction_to_degrees(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().upper()
    if text in CARDINAL_TO_DEGREES:
        return float(CARDINAL_TO_DEGREES[text])
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if match:
        return float(match.group(0))
    return None


def wind_speed_text_to_mph(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).lower()
    if "calm" in text:
        return 0.0
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text)]
    if not nums:
        return None
    speed = sum(nums[:2]) / min(len(nums), 2)
    if "km" in text:
        return speed * 0.621371
    return speed


def wind_out_to_cf(wind_from_degrees, cf_bearing):
    if wind_from_degrees is None or cf_bearing is None:
        return None
    wind_to = (float(wind_from_degrees) + 180.0) % 360.0
    diff = (wind_to - float(cf_bearing) + 180.0) % 360.0 - 180.0
    return round(math.cos(math.radians(diff)), 3)


def round_or_none(value, digits=1):
    if value is None:
        return None
    return round(float(value), digits)


def neutral_indoor_weather(source="indoor"):
    return {
        "weather_temp_f": 72.0,
        "weather_wind_mph": 0.0,
        "weather_wind_dir_degrees": None,
        "weather_wind_out_cf": 0.0,
        "weather_humidity_pct": 50.0,
        "weather_precip_pct": 0.0,
        "weather_pressure_mb": 1013.25,
        "weather_is_indoor": True,
        "weather_source": source,
        "weather_station": None,
        "weather_observed_at": None,
    }


def empty_weather(source):
    return {
        "weather_temp_f": None,
        "weather_wind_mph": None,
        "weather_wind_dir_degrees": None,
        "weather_wind_out_cf": None,
        "weather_humidity_pct": None,
        "weather_precip_pct": None,
        "weather_pressure_mb": None,
        "weather_is_indoor": False,
        "weather_source": source,
        "weather_station": None,
        "weather_observed_at": None,
    }


def observation_to_weather(feature, station, stadium):
    props = feature.get("properties", {})
    temp_prop = props.get("temperature")
    wind_prop = props.get("windSpeed")
    pressure_prop = props.get("barometricPressure")
    temp_f = temp_to_f(scalar(temp_prop), unit_code(temp_prop))
    wind_mph = wind_to_mph(scalar(wind_prop), unit_code(wind_prop))
    wind_dir = direction_to_degrees(scalar(props.get("windDirection")))
    humidity = scalar(props.get("relativeHumidity"))
    pressure = pressure_to_mb(scalar(pressure_prop), unit_code(pressure_prop))

    return {
        "weather_temp_f": round_or_none(temp_f, 1),
        "weather_wind_mph": round_or_none(wind_mph, 1),
        "weather_wind_dir_degrees": round_or_none(wind_dir, 1),
        "weather_wind_out_cf": wind_out_to_cf(wind_dir, stadium.get("cfBearing")),
        "weather_humidity_pct": round_or_none(humidity, 1),
        "weather_precip_pct": None,
        "weather_pressure_mb": round_or_none(pressure, 1),
        "weather_is_indoor": False,
        "weather_source": "api.weather.gov:observations",
        "weather_station": station,
        "weather_observed_at": props.get("timestamp"),
    }


def select_nearest_observation(features, target_time):
    best = None
    best_delta = None
    for feature in features:
        ts = parse_time(feature.get("properties", {}).get("timestamp"))
        if not ts:
            continue
        delta = abs((ts - target_time).total_seconds())
        if best_delta is None or delta < best_delta:
            best = feature
            best_delta = delta
    return best


def fetch_observation_weather(game, stadium, session=requests, user_agent=None):
    target_time = game_time_utc(game)
    start = target_time - timedelta(hours=6)
    end = target_time + timedelta(hours=6)

    for station in observation_stations(stadium, session=session, user_agent=user_agent)[:3]:
        data = fetch_json(
            session,
            f"{NWS_BASE_URL}/stations/{station}/observations",
            params={"start": iso_z(start), "end": iso_z(end)},
            user_agent=user_agent,
        )
        feature = select_nearest_observation(data.get("features", []), target_time)
        if feature:
            return observation_to_weather(feature, station, stadium)

    return empty_weather("api.weather.gov:observations:missing")


def forecast_period_to_weather(period, stadium):
    temp = temp_to_f(period.get("temperature"), period.get("temperatureUnit", "F"))
    wind_mph = wind_speed_text_to_mph(period.get("windSpeed"))
    wind_dir = direction_to_degrees(period.get("windDirection"))
    humidity = scalar(period.get("relativeHumidity"))
    precip = scalar(period.get("probabilityOfPrecipitation"))
    return {
        "weather_temp_f": round_or_none(temp, 1),
        "weather_wind_mph": round_or_none(wind_mph, 1),
        "weather_wind_dir_degrees": round_or_none(wind_dir, 1),
        "weather_wind_out_cf": wind_out_to_cf(wind_dir, stadium.get("cfBearing")),
        "weather_humidity_pct": round_or_none(humidity, 1),
        "weather_precip_pct": round_or_none(precip, 1),
        "weather_pressure_mb": None,
        "weather_is_indoor": False,
        "weather_source": "api.weather.gov:forecastHourly",
        "weather_station": None,
        "weather_observed_at": period.get("startTime"),
    }


def select_hourly_period(periods, target_time):
    best = None
    best_delta = None
    for period in periods:
        start = parse_time(period.get("startTime"))
        if not start:
            continue
        end = parse_time(period.get("endTime"))
        if end and start <= target_time < end:
            return period
        delta = abs((start - target_time).total_seconds())
        if best_delta is None or delta < best_delta:
            best = period
            best_delta = delta
    return best


def fetch_forecast_weather(game, stadium, session=requests, user_agent=None):
    props = point_properties(stadium, session=session, user_agent=user_agent)
    hourly_url = props.get("forecastHourly")
    if not hourly_url:
        return empty_weather("api.weather.gov:forecastHourly:missing")
    data = fetch_json(session, hourly_url, user_agent=user_agent)
    period = select_hourly_period(data.get("properties", {}).get("periods", []), game_time_utc(game))
    if not period:
        return empty_weather("api.weather.gov:forecastHourly:missing")
    return forecast_period_to_weather(period, stadium)


def weatherbit_timestamp(row):
    return (
        row.get("timestamp_utc")
        or row.get("timestamp_local")
        or row.get("datetime")
        or row.get("ts")
    )


def select_weatherbit_hour(rows, target_time):
    best = None
    best_delta = None
    for row in rows:
        ts = parse_time(weatherbit_timestamp(row))
        if not ts:
            continue
        delta = abs((ts - target_time).total_seconds())
        if best_delta is None or delta < best_delta:
            best = row
            best_delta = delta
    return best


def weatherbit_station(data):
    sources = data.get("sources") or []
    if isinstance(sources, list) and sources:
        return sources[0]
    return None


def weatherbit_row_to_weather(row, stadium, source, station=None):
    wind_dir = direction_to_degrees(row.get("wind_dir"))
    precip = row.get("pop")
    if precip is None:
        precip = row.get("precip")
    observed = parse_time(weatherbit_timestamp(row))
    return {
        "weather_temp_f": round_or_none(row.get("temp"), 1),
        "weather_wind_mph": round_or_none(row.get("wind_spd"), 1),
        "weather_wind_dir_degrees": round_or_none(wind_dir, 1),
        "weather_wind_out_cf": wind_out_to_cf(wind_dir, stadium.get("cfBearing")),
        "weather_humidity_pct": round_or_none(row.get("rh"), 1),
        "weather_precip_pct": round_or_none(precip, 1),
        "weather_pressure_mb": round_or_none(row.get("pres"), 1),
        "weather_is_indoor": False,
        "weather_source": source,
        "weather_station": station,
        "weather_observed_at": observed.isoformat() if observed else None,
    }


def fetch_weatherbit_history_weather(game, stadium, session=requests, key=None):
    key = key or weatherbit_api_key()
    if not key:
        return empty_weather("weatherbit:history-hourly:missing-key")

    target_time = game_time_utc(game)
    params = {
        "lat": stadium["lat"],
        "lon": stadium["lon"],
        "start_date": target_time.date().isoformat(),
        "end_date": (target_time + timedelta(days=1)).date().isoformat(),
        "units": "I",
        "key": key,
    }
    data = fetch_weatherbit_json(session, "/history/hourly", params)
    row = select_weatherbit_hour(data.get("data", []), target_time)
    if not row:
        return empty_weather("weatherbit:history-hourly:missing")
    return weatherbit_row_to_weather(
        row,
        stadium,
        "weatherbit:history-hourly",
        station=weatherbit_station(data),
    )


def fetch_weatherbit_forecast_weather(game, stadium, session=requests, key=None, now_utc=None):
    key = key or weatherbit_api_key()
    if not key:
        return empty_weather("weatherbit:forecast-hourly:missing-key")

    target_time = game_time_utc(game)
    now = parse_time(now_utc) if now_utc else datetime.now(timezone.utc)
    hours_ahead = max(1, int(math.ceil((target_time - now).total_seconds() / 3600)) + 2)
    params = {
        "lat": stadium["lat"],
        "lon": stadium["lon"],
        "hours": min(hours_ahead, 240),
        "units": "I",
        "key": key,
    }
    data = fetch_weatherbit_json(session, "/forecast/hourly", params)
    row = select_weatherbit_hour(data.get("data", []), target_time)
    if not row:
        return empty_weather("weatherbit:forecast-hourly:missing")
    return weatherbit_row_to_weather(
        row,
        stadium,
        "weatherbit:forecast-hourly",
        station=weatherbit_station(data),
    )


def weather_is_usable(weather):
    source = str(weather.get("weather_source", ""))
    return not (
        source.endswith(":missing")
        or source.endswith(":missing-key")
        or source.endswith(":error")
        or source.endswith(":unavailable")
    )


def fetch_weather_for_game(game, stadium, session=requests, now_utc=None, user_agent=None):
    if not stadium or "lat" not in stadium or "lon" not in stadium:
        return empty_weather("missing-stadium")
    if stadium.get("roof"):
        return neutral_indoor_weather()

    target_time = game_time_utc(game)
    now = parse_time(now_utc) if now_utc else datetime.now(timezone.utc)
    key = weatherbit_api_key()
    if key:
        try:
            if target_time > now:
                weather = fetch_weatherbit_forecast_weather(
                    game,
                    stadium,
                    session=session,
                    key=key,
                    now_utc=now,
                )
            else:
                weather = fetch_weatherbit_history_weather(
                    game,
                    stadium,
                    session=session,
                    key=key,
                )
            if weather_is_usable(weather):
                return weather
        except WeatherbitRateLimitError:
            raise
        except Exception:
            pass

    if target_time > now:
        return fetch_forecast_weather(game, stadium, session=session, user_agent=user_agent)
    return fetch_observation_weather(game, stadium, session=session, user_agent=user_agent)


def needs_weather(row, force=False, retry_missing=False, retry_unavailable=False):
    if force:
        return True
    source = row.get("weather_source")
    if not source:
        return True
    if retry_unavailable:
        return str(source).endswith(":missing") or str(source).endswith(":unavailable")
    return retry_missing and str(source).endswith(":missing")


def backfill_weather(
    db_path=DB_PATH,
    start_date=None,
    end_date=None,
    force=False,
    retry_missing=False,
    retry_unavailable=False,
    limit=None,
    sleep_s=0.2,
    session=requests,
    max_observation_age_days=None,
    now_utc=None,
):
    teams_data = load_team_data()
    stadiums = teams_data.get("stadiums", {})
    db = Database(db_path)
    updated = 0
    missing = 0
    unavailable = 0
    rate_limited = False
    retry_after_seconds = None
    now = parse_time(now_utc) if now_utc else datetime.now(timezone.utc)
    has_weatherbit = bool(weatherbit_api_key())
    try:
        where = ["1=1"]
        params = []
        if start_date:
            where.append("date >= ?")
            params.append(start_date)
        if end_date:
            where.append("date <= ?")
            params.append(end_date)
        if force:
            pass
        elif retry_unavailable:
            where.append(
                "(weather_source IS NULL OR weather_source LIKE '%:missing' "
                "OR weather_source LIKE '%:unavailable')"
            )
        elif retry_missing:
            where.append("(weather_source IS NULL OR weather_source LIKE '%:missing')")
        else:
            where.append("weather_source IS NULL")
        sql = f"""
            SELECT game_pk, date, game_time_utc, home_team, stadium_roof, weather_source
            FROM games
            WHERE {' AND '.join(where)}
            ORDER BY date, game_pk
        """
        rows = db.query(sql, params)
        if limit:
            rows = rows[:limit]

        for row in rows:
            if not needs_weather(
                row,
                force=force,
                retry_missing=retry_missing,
                retry_unavailable=retry_unavailable,
            ):
                continue
            stadium = stadiums.get(row["home_team"], {})
            game = {
                "game_pk": row["game_pk"],
                "date": row["date"],
                "game_time_utc": row["game_time_utc"],
                "home_team": row["home_team"],
            }
            try:
                target = game_time_utc(game)
                stale_cutoff = (
                    now - timedelta(days=max_observation_age_days)
                    if max_observation_age_days is not None
                    else None
                )
                if (
                    stale_cutoff is not None
                    and target < stale_cutoff
                    and not stadium.get("roof")
                    and not has_weatherbit
                ):
                    weather = empty_weather("api.weather.gov:observations:unavailable")
                else:
                    weather = fetch_weather_for_game(game, stadium, session=session, now_utc=now)
            except WeatherbitRateLimitError as exc:
                print(f"weatherbit rate limited; retry after {exc.retry_after} seconds")
                rate_limited = True
                retry_after_seconds = exc.retry_after
                break
            except Exception as exc:
                print(f"weather failed for {row['game_pk']}: {exc}")
                weather = empty_weather("api.weather.gov:error")
            db.update_game_weather(row["game_pk"], weather)
            source = str(weather.get("weather_source", ""))
            if source.endswith(":unavailable"):
                unavailable += 1
            elif source.endswith(":missing"):
                missing += 1
            else:
                updated += 1
            if sleep_s:
                time.sleep(sleep_s)
    finally:
        db.close()
    return {
        "updated": updated,
        "missing": missing,
        "unavailable": unavailable,
        "checked": updated + missing + unavailable,
        "rate_limited": rate_limited,
        "retry_after_seconds": retry_after_seconds,
    }


def main():
    parser = argparse.ArgumentParser(description="Backfill SportsBotv2 MLB weather from api.weather.gov")
    parser.add_argument("--db-path", default=str(DB_PATH))
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--retry-missing", action="store_true")
    parser.add_argument("--retry-unavailable", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--sleep", type=float, default=0.2)
    parser.add_argument("--max-observation-age-days", type=int)
    args = parser.parse_args()
    start_date = validate_date(args.start_date, "start-date")
    end_date = validate_date(args.end_date, "end-date")

    result = backfill_weather(
        db_path=args.db_path,
        start_date=start_date,
        end_date=end_date,
        force=args.force,
        retry_missing=args.retry_missing,
        retry_unavailable=args.retry_unavailable,
        limit=args.limit,
        sleep_s=args.sleep,
        max_observation_age_days=args.max_observation_age_days,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
