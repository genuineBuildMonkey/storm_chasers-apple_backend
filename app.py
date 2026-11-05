import os
import time
from datetime import date, datetime, timedelta
from datetime import time as datetime_time
from functools import wraps
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

import jwt
import requests
from flask import Flask, jsonify, request
from flask_cors import CORS
from timezonefinder import TimezoneFinder

APPLE_TEAM_ID = os.environ["APPLE_TEAM_ID"]
APPLE_KEY_ID = os.environ["APPLE_KEY_ID"]
APPLE_SERVICE_ID = os.environ["APPLE_SERVICE_ID"]
APPLE_KEY_FILE = os.environ["APPLE_KEY_FILE"]
WEATHER_API_KEY = os.environ["WEATHER_API_KEY"]

WEATHERKIT_BASE_URL = "https://weatherkit.apple.com/api/v1/weather/en"
WEATHERKIT_ALERT_BASE_URL = "https://weatherkit.apple.com/api/v1/weatherAlert/en"
WEATHERKIT_TIMEZONE = os.environ.get("WEATHERKIT_TIMEZONE", "UTC")
WEATHERKIT_COUNTRY_CODE = os.environ.get("WEATHERKIT_COUNTRY_CODE", "US")
WEATHERKIT_TIMEOUT_SECONDS = 15
DAILY_FORECAST_DAYS = 10
TIMEZONE_FINDER = TimezoneFinder()

with open(APPLE_KEY_FILE, encoding="utf-8") as key_file:
    APPLE_PRIVATE_KEY = key_file.read()


app = Flask(__name__)

CORS(
    app,
    resources={
        r"/weather/*": {
            "origins": "*",
            "allow_headers": ["X-Weather-Key"],
        }
    },
)


class WeatherKitError(Exception):
    pass


def create_weatherkit_token():
    now = int(time.time())

    headers = {
        "alg": "ES256",
        "kid": APPLE_KEY_ID,
        "id": f"{APPLE_TEAM_ID}.{APPLE_SERVICE_ID}",
    }

    payload = {
        "iss": APPLE_TEAM_ID,
        "iat": now,
        "exp": now + 3300,
        "sub": APPLE_SERVICE_ID,
    }

    return jwt.encode(
        payload,
        APPLE_PRIVATE_KEY,
        algorithm="ES256",
        headers=headers,
    )


def require_api_key(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        key = request.headers.get("X-Weather-Key")
        if key != WEATHER_API_KEY:
            return jsonify({"error": "unauthorized"}), 401
        return fn(*args, **kwargs)

    return wrapper


def get_coordinates():
    lat_raw = request.args.get("lat")
    lon_raw = request.args.get("lon")

    if lat_raw is None or lon_raw is None:
        return None, None, (jsonify({"error": "lat and lon are required"}), 400)

    try:
        lat = float(lat_raw)
        lon = float(lon_raw)
    except ValueError:
        return None, None, (jsonify({"error": "lat and lon must be numbers"}), 400)

    if not -90 <= lat <= 90:
        return None, None, (jsonify({"error": "lat must be between -90 and 90"}), 400)

    if not -180 <= lon <= 180:
        return None, None, (jsonify({"error": "lon must be between -180 and 180"}), 400)

    return lat, lon, None


def request_weatherkit_json(url, params=None):
    token = create_weatherkit_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    try:
        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=WEATHERKIT_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise WeatherKitError("WeatherKit request failed") from exc

    if not response.ok:
        raise WeatherKitError(f"WeatherKit returned HTTP {response.status_code}")

    try:
        return response.json()
    except ValueError as exc:
        raise WeatherKitError("WeatherKit returned invalid JSON") from exc


def fetch_weatherkit(lat, lon, datasets, extra_params=None):
    url = f"{WEATHERKIT_BASE_URL}/{lat}/{lon}"
    params = {
        "dataSets": datasets,
        "timezone": WEATHERKIT_TIMEZONE,
    }
    if extra_params:
        params.update(extra_params)

    return request_weatherkit_json(url, params)


def fetch_weatherkit_alert_details(alert):
    alert_id = alert.get("id")
    details_url = alert.get("detailsUrl")

    if alert_id:
        url = f"{WEATHERKIT_ALERT_BASE_URL}/{quote(str(alert_id), safe='')}"
    elif details_url and urlparse(details_url).hostname == "weatherkit.apple.com":
        url = details_url
    else:
        return {}

    return request_weatherkit_json(url)


def timezone_for_coordinates(lat, lon):
    return TIMEZONE_FINDER.timezone_at(lat=lat, lng=lon) or WEATHERKIT_TIMEZONE


def metadata_response_fields(metadata, lat, lon):
    return {
        "latitude": metadata.get("latitude", lat),
        "longitude": metadata.get("longitude", lon),
        "readTime": metadata.get("readTime"),
        "reportedTime": metadata.get("reportedTime"),
        "expireTime": metadata.get("expireTime"),
    }


def parse_weatherkit_time(value):
    if not isinstance(value, str):
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def hourly_records_in_period(hours, part):
    period_start = parse_weatherkit_time(part.get("forecastStart"))
    period_end = parse_weatherkit_time(part.get("forecastEnd"))
    if period_start is None or period_end is None:
        return []

    records = []
    for hour in hours:
        if not isinstance(hour, dict):
            continue
        forecast_start = parse_weatherkit_time(hour.get("forecastStart"))
        if forecast_start is None:
            continue
        try:
            in_period = period_start <= forecast_start < period_end
        except TypeError:
            # Ignore malformed upstream timestamps that mix offset-aware and
            # offset-naive values instead of failing the complete forecast.
            continue
        if in_period:
            records.append(hour)
    return records


def aggregate_hourly_field(hours, field, aggregate):
    values = [
        hour[field]
        for hour in hours
        if isinstance(hour.get(field), (int, float))
        and not isinstance(hour[field], bool)
    ]
    return aggregate(values) if values else None


def average(values):
    return sum(values) / len(values)


def day_part_response(part):
    if not isinstance(part, dict):
        return None

    fields = (
        "conditionCode",
        "precipitationChance",
        "humidity",
        "windSpeed",
        "windDirection",
    )
    return {field: part[field] for field in fields if field in part}


def day_part_detail_response(part, hours, daytime):
    response = day_part_response(part)
    if response is None:
        return None

    direct_fields = ("precipitationAmount", "snowfallAmount", "cloudCover")
    response.update({field: part[field] for field in direct_fields if field in part})
    period_hours = hourly_records_in_period(hours, part)
    apparent_aggregate = max if daytime else min
    response.update(
        {
            "temperatureApparent": aggregate_hourly_field(
                period_hours, "temperatureApparent", apparent_aggregate
            ),
            "windGust": aggregate_hourly_field(period_hours, "windGust", max),
            "maxUvIndex": aggregate_hourly_field(period_hours, "uvIndex", max),
            "pressure": aggregate_hourly_field(period_hours, "pressure", average),
        }
    )
    return response


def hour_detail_response(hour):
    fields = (
        "forecastStart",
        "conditionCode",
        "daylight",
        "temperature",
        "temperatureApparent",
        "precipitationAmount",
        "precipitationChance",
        "precipitationType",
        "snowfallIntensity",
        "humidity",
        "windSpeed",
        "windDirection",
        "windGust",
        "uvIndex",
        "cloudCover",
        "pressure",
        "pressureTrend",
        "temperatureDewPoint",
        "visibility",
    )
    return {field: hour.get(field) for field in fields}


def detail_date_bounds(forecast_date, time_zone):
    zone = ZoneInfo(time_zone)
    next_date = forecast_date + timedelta(days=1)
    day_start = datetime.combine(forecast_date, datetime_time.min, tzinfo=zone)
    day_end = datetime.combine(next_date, datetime_time.min, tzinfo=zone)
    hourly_start = datetime.combine(
        forecast_date, datetime_time(hour=7), tzinfo=zone
    )
    hourly_end = datetime.combine(next_date, datetime_time(hour=7), tzinfo=zone)
    return day_start, day_end, hourly_start, hourly_end


def alert_message_fields(alert):
    descriptions = []
    instructions = []

    for message in alert.get("messages", []):
        if isinstance(message, str):
            descriptions.append(message)
            continue
        if not isinstance(message, dict):
            continue

        text = message.get("text")
        if not text:
            continue

        message_type = str(
            message.get("type") or message.get("messageType") or ""
        ).lower()
        if "instruction" in message_type:
            instructions.append(text)
        else:
            descriptions.append(text)

    direct_instructions = alert.get("instructions") or alert.get("instruction")
    if direct_instructions:
        if isinstance(direct_instructions, list):
            instructions.extend(str(value) for value in direct_instructions if value)
        else:
            instructions.append(str(direct_instructions))

    return "\n\n".join(descriptions) or None, "\n\n".join(instructions) or None


def normalize_alert(summary, details):
    alert = {**summary, **details}
    event = alert.get("event") or alert.get("phenomenon") or summary.get("description")
    headline = alert.get("headline") or event or alert.get("description")
    message_description, instructions = alert_message_fields(alert)
    description = message_description or alert.get("description") or headline

    normalized = {
        "id": str(alert.get("id") or summary.get("detailsUrl")),
        "event": event,
        "headline": headline,
        "description": description,
    }

    area = alert.get("area")
    area_name = alert.get("areaName")
    if not area_name and isinstance(area, dict):
        area_name = area.get("name") or area.get("description")

    optional_fields = {
        "severity": alert.get("severity"),
        "urgency": alert.get("urgency"),
        "certainty": alert.get("certainty"),
        "source": alert.get("source"),
        "effectiveTime": alert.get("effectiveTime"),
        "eventOnsetTime": alert.get("eventOnsetTime"),
        "eventEndTime": alert.get("eventEndTime"),
        "expireTime": alert.get("expireTime"),
        "areaName": area_name,
        "instructions": instructions,
        "detailsUrl": alert.get("detailsUrl") or summary.get("detailsUrl"),
    }
    normalized.update(
        {field: value for field, value in optional_fields.items() if value is not None}
    )
    return normalized


@app.errorhandler(WeatherKitError)
def handle_weatherkit_error(exc):
    return jsonify({"error": str(exc)}), 502


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/weather/next-hour")
@require_api_key
def weather_next_hour():
    lat, lon, error = get_coordinates()
    if error:
        return error

    data = fetch_weatherkit(lat, lon, "forecastNextHour")
    forecast = data.get("forecastNextHour")

    if not forecast:
        return jsonify({"error": "forecastNextHour unavailable"}), 502

    metadata = forecast.get("metadata", {})

    minutes = [
        {
            "time": minute.get("startTime"),
            "chance": minute.get("precipitationChance"),
            "intensity": minute.get("precipitationIntensity"),
        }
        for minute in forecast.get("minutes", [])
    ]

    response = metadata_response_fields(metadata, lat, lon)
    response["minutes"] = minutes

    return jsonify(response)


@app.get("/weather/current")
@require_api_key
def weather_current():
    lat, lon, error = get_coordinates()
    if error:
        return error

    data = fetch_weatherkit(lat, lon, "currentWeather")
    current = data.get("currentWeather")

    if not current:
        return jsonify({"error": "currentWeather unavailable"}), 502

    metadata = current.get("metadata", {})

    response = metadata_response_fields(metadata, lat, lon)
    response.update(
        {
            "asOf": current.get("asOf"),
            "conditionCode": current.get("conditionCode"),
            "daylight": current.get("daylight"),
            "temperature": current.get("temperature"),
            "temperatureApparent": current.get("temperatureApparent"),
            "windSpeed": current.get("windSpeed"),
            "windGust": current.get("windGust"),
            "windDirection": current.get("windDirection"),
            "humidity": current.get("humidity"),
            "temperatureDewPoint": current.get("temperatureDewPoint"),
            "uvIndex": current.get("uvIndex"),
            "visibility": current.get("visibility"),
            "pressure": current.get("pressure"),
        }
    )

    return jsonify(response)


@app.get("/weather/hourly")
@require_api_key
def weather_hourly():
    lat, lon, error = get_coordinates()
    if error:
        return error

    time_zone = timezone_for_coordinates(lat, lon)
    data = fetch_weatherkit(lat, lon, "forecastHourly", {"timezone": time_zone})
    forecast = data.get("forecastHourly")

    if not forecast:
        return jsonify({"error": "forecastHourly unavailable"}), 502

    metadata = forecast.get("metadata", {})

    hours = [
        {
            "time": hour.get("forecastStart"),
            "conditionCode": hour.get("conditionCode"),
            "daylight": hour.get("daylight"),
            "temperature": hour.get("temperature"),
            "temperatureApparent": hour.get("temperatureApparent"),
            "precipitationChance": hour.get("precipitationChance"),
        }
        for hour in forecast.get("hours", [])
    ]

    response = metadata_response_fields(metadata, lat, lon)
    response["timeZone"] = time_zone
    response["hours"] = hours

    return jsonify(response)


@app.get("/weather/hourly/detail")
@require_api_key
def weather_hourly_detail():
    lat, lon, error = get_coordinates()
    if error:
        return error

    time_raw = request.args.get("time")
    if time_raw is None:
        return jsonify({"error": "time is required"}), 400

    forecast_start = parse_weatherkit_time(time_raw)
    if forecast_start is None or forecast_start.utcoffset() is None:
        return jsonify({"error": "time must be an ISO 8601 timestamp with offset"}), 400

    forecast_end = forecast_start + timedelta(hours=1)
    time_zone = timezone_for_coordinates(lat, lon)
    data = fetch_weatherkit(
        lat,
        lon,
        "forecastHourly",
        {
            "timezone": time_zone,
            "hourlyStart": forecast_start.isoformat(),
            "hourlyEnd": forecast_end.isoformat(),
        },
    )

    hourly_forecast = data.get("forecastHourly") or {}
    hours = hourly_forecast.get("hours") or []
    hour = next(
        (
            value
            for value in hours
            if isinstance(value, dict)
            and parse_weatherkit_time(value.get("forecastStart")) == forecast_start
        ),
        None,
    )
    if hour is None:
        return jsonify({"error": "hourly detail unavailable"}), 502

    metadata = hourly_forecast.get("metadata", {})
    response = metadata_response_fields(metadata, lat, lon)
    response["timeZone"] = time_zone
    response["units"] = metadata.get("units", "metric")
    response["hour"] = hour_detail_response(hour)
    return jsonify(response)


@app.get("/weather/daily")
@require_api_key
def weather_daily():
    lat, lon, error = get_coordinates()
    if error:
        return error

    time_zone = timezone_for_coordinates(lat, lon)
    data = fetch_weatherkit(lat, lon, "forecastDaily", {"timezone": time_zone})
    forecast = data.get("forecastDaily")

    if not forecast:
        return jsonify({"error": "forecastDaily unavailable"}), 502

    metadata = forecast.get("metadata", {})
    valid_days = [
        day
        for day in forecast.get("days", [])
        if isinstance(day, dict) and day.get("forecastStart")
    ]
    valid_days.sort(key=lambda day: day["forecastStart"])

    days = []
    for day in valid_days[:DAILY_FORECAST_DAYS]:
        days.append(
            {
                "forecastStart": day.get("forecastStart"),
                "temperatureMax": day.get("temperatureMax"),
                "temperatureMin": day.get("temperatureMin"),
                "conditionCode": day.get("conditionCode"),
                "precipitationChance": day.get("precipitationChance"),
                "daytimeForecast": day_part_response(day.get("daytimeForecast")),
                "overnightForecast": day_part_response(day.get("overnightForecast")),
            }
        )

    response = metadata_response_fields(metadata, lat, lon)
    response["timeZone"] = time_zone
    response["units"] = metadata.get("units", "metric")
    response["days"] = days
    return jsonify(response)


@app.get("/weather/daily/detail")
@require_api_key
def weather_daily_detail():
    lat, lon, error = get_coordinates()
    if error:
        return error

    date_raw = request.args.get("date")
    if date_raw is None:
        return jsonify({"error": "date is required"}), 400
    try:
        forecast_date = date.fromisoformat(date_raw)
    except ValueError:
        return jsonify({"error": "date must use YYYY-MM-DD format"}), 400

    time_zone = timezone_for_coordinates(lat, lon)
    day_start, day_end, hourly_start, hourly_end = detail_date_bounds(
        forecast_date, time_zone
    )
    data = fetch_weatherkit(
        lat,
        lon,
        "forecastDaily,forecastHourly",
        {
            "timezone": time_zone,
            "dailyStart": day_start.isoformat(),
            "dailyEnd": day_end.isoformat(),
            "hourlyStart": hourly_start.isoformat(),
            "hourlyEnd": hourly_end.isoformat(),
        },
    )

    daily_forecast = data.get("forecastDaily") or {}
    days = daily_forecast.get("days") or []
    day = next((value for value in days if isinstance(value, dict)), None)
    if day is None:
        return jsonify({"error": "daily detail unavailable"}), 502

    hourly_forecast = data.get("forecastHourly") or {}
    hours = hourly_forecast.get("hours") or []
    detail = {
        "forecastStart": day.get("forecastStart"),
        "temperatureMax": day.get("temperatureMax"),
        "temperatureMin": day.get("temperatureMin"),
        "conditionCode": day.get("conditionCode"),
        "precipitationChance": day.get("precipitationChance"),
        "sunrise": day.get("sunrise"),
        "sunset": day.get("sunset"),
        "moonPhase": day.get("moonPhase"),
        "moonrise": day.get("moonrise"),
        "moonset": day.get("moonset"),
        "daytimeForecast": day_part_detail_response(
            day.get("daytimeForecast"), hours, daytime=True
        ),
        "overnightForecast": day_part_detail_response(
            day.get("overnightForecast"), hours, daytime=False
        ),
    }

    metadata = daily_forecast.get("metadata", {})
    response = metadata_response_fields(metadata, lat, lon)
    response["timeZone"] = time_zone
    response["units"] = metadata.get("units", "metric")
    response["day"] = detail
    return jsonify(response)


@app.get("/weather/alerts")
@require_api_key
def weather_alerts():
    lat, lon, error = get_coordinates()
    if error:
        return error

    data = fetch_weatherkit(
        lat,
        lon,
        "weatherAlerts",
        {"countryCode": WEATHERKIT_COUNTRY_CODE},
    )
    collection = data.get("weatherAlerts") or {}
    summaries = collection.get("alerts") or []

    alerts = []
    seen_ids = set()
    for summary in summaries:
        if not isinstance(summary, dict):
            continue

        stable_id = summary.get("id") or summary.get("detailsUrl")
        if not stable_id or stable_id in seen_ids:
            continue
        seen_ids.add(stable_id)

        details = {}
        if summary.get("detailsUrl") and not summary.get("messages"):
            try:
                details = fetch_weatherkit_alert_details(summary)
            except WeatherKitError:
                # A summary is still useful if Apple temporarily cannot return
                # the corresponding detail document.
                details = {}

        alerts.append(normalize_alert(summary, details))

    metadata = collection.get("metadata", {})
    response = metadata_response_fields(metadata, lat, lon)
    response["alerts"] = alerts
    return jsonify(response)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001)
