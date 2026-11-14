import os
from datetime import UTC, date, datetime, timedelta
from functools import wraps

from flask import Flask, jsonify, request
from flask_cors import CORS
from timezonefinder import TimezoneFinder

from moon_illumination import FIRST_YEAR, LAST_YEAR, moon_illumination
from weather_format import (
    day_part_detail_response,
    day_part_response,
    detail_date_bounds,
    hour_detail_response,
    metadata_response_fields,
    normalize_alert,
    parse_weatherkit_time,
)
from weatherkit_client import (
    WEATHERKIT_TIMEZONE,
    WeatherKitError,
    fetch_weatherkit,
    fetch_weatherkit_alert_details,
)

WEATHER_API_KEY = os.environ["WEATHER_API_KEY"]
WEATHERKIT_COUNTRY_CODE = os.environ.get("WEATHERKIT_COUNTRY_CODE", "US")
DAILY_FORECAST_DAYS = 10
HOURLY_FORECAST_HOURS = 240
TIMEZONE_FINDER = TimezoneFinder()


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


def get_forecast_date():
    date_raw = request.args.get("date")
    if date_raw is None:
        return None, (jsonify({"error": "date is required"}), 400)
    try:
        return date.fromisoformat(date_raw), None
    except ValueError:
        return None, (jsonify({"error": "date must use YYYY-MM-DD format"}), 400)


def timezone_for_coordinates(lat, lon):
    return TIMEZONE_FINDER.timezone_at(lat=lat, lng=lon) or WEATHERKIT_TIMEZONE


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

    hours_raw = request.args.get("hours")
    if hours_raw is None:
        hours_requested = HOURLY_FORECAST_HOURS
    else:
        try:
            hours_requested = int(hours_raw)
        except ValueError:
            return jsonify({"error": "hours must be an integer"}), 400

        if not 1 <= hours_requested <= HOURLY_FORECAST_HOURS:
            return jsonify({"error": "hours must be between 1 and 240"}), 400

    forecast_start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    forecast_end = forecast_start + timedelta(hours=hours_requested)
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

    forecast_date, error = get_forecast_date()
    if error:
        return error

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
        "moonIllumination": (
            moon_illumination(forecast_date)
            if FIRST_YEAR <= forecast_date.year <= LAST_YEAR
            else None
        ),
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


@app.get("/weather/moon")
@require_api_key
def weather_moon():
    lat, lon, error = get_coordinates()
    if error:
        return error

    forecast_date, error = get_forecast_date()
    if error:
        return error
    if not FIRST_YEAR <= forecast_date.year <= LAST_YEAR:
        return jsonify({"error": "date must be between 2026 and 2032"}), 400

    time_zone = timezone_for_coordinates(lat, lon)
    return jsonify(
        {
            "date": forecast_date.isoformat(),
            "timeZone": time_zone,
            "moonIllumination": moon_illumination(forecast_date),
        }
    )


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
        # WeatherKit v1 rejects countryCode with HTTP 400; its alerts query uses country.
        {"country": WEATHERKIT_COUNTRY_CODE},
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
        if summary.get("id") and not summary.get("messages"):
            try:
                details = fetch_weatherkit_alert_details(summary)
            except WeatherKitError:
                # A summary is still useful if Apple temporarily cannot return
                # the corresponding detail document.
                details = {}

        alerts.append(normalize_alert(summary, details, collection.get("detailsUrl")))

    metadata = collection.get("metadata", {})
    response = metadata_response_fields(metadata, lat, lon)
    response["alerts"] = alerts
    return jsonify(response)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001)
