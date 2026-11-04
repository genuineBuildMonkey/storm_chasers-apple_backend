import os
import time
from functools import wraps

import jwt
import requests
from flask import Flask, jsonify, request
from flask_cors import CORS


APPLE_TEAM_ID = os.environ["APPLE_TEAM_ID"]
APPLE_KEY_ID = os.environ["APPLE_KEY_ID"]
APPLE_SERVICE_ID = os.environ["APPLE_SERVICE_ID"]
APPLE_KEY_FILE = os.environ["APPLE_KEY_FILE"]
WEATHER_API_KEY = os.environ["WEATHER_API_KEY"]

WEATHERKIT_BASE_URL = "https://weatherkit.apple.com/api/v1/weather/en"
WEATHERKIT_TIMEZONE = os.environ.get("WEATHERKIT_TIMEZONE", "UTC")
WEATHERKIT_TIMEOUT_SECONDS = 15

with open(APPLE_KEY_FILE, "r", encoding="utf-8") as key_file:
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


def fetch_weatherkit(lat, lon, datasets):
    token = create_weatherkit_token()

    url = f"{WEATHERKIT_BASE_URL}/{lat}/{lon}"
    params = {
        "dataSets": datasets,
        "timezone": WEATHERKIT_TIMEZONE,
    }
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


def metadata_response_fields(metadata, lat, lon):
    return {
        "latitude": metadata.get("latitude", lat),
        "longitude": metadata.get("longitude", lon),
        "readTime": metadata.get("readTime"),
        "reportedTime": metadata.get("reportedTime"),
        "expireTime": metadata.get("expireTime"),
    }


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

    data = fetch_weatherkit(lat, lon, "forecastHourly")
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
    response["hours"] = hours

    return jsonify(response)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001)
