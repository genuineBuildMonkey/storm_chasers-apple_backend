"""Authenticated requests to Apple's WeatherKit REST API."""

import os
import time
from urllib.parse import quote

import jwt
import requests

APPLE_TEAM_ID = os.environ["APPLE_TEAM_ID"]
APPLE_KEY_ID = os.environ["APPLE_KEY_ID"]
APPLE_SERVICE_ID = os.environ["APPLE_SERVICE_ID"]
APPLE_KEY_FILE = os.environ["APPLE_KEY_FILE"]

WEATHERKIT_BASE_URL = "https://weatherkit.apple.com/api/v1/weather/en"
WEATHERKIT_ALERT_BASE_URL = "https://weatherkit.apple.com/api/v1/weatherAlert/en"
WEATHERKIT_TIMEZONE = os.environ.get("WEATHERKIT_TIMEZONE", "UTC")
WEATHERKIT_TIMEOUT_SECONDS = 15

with open(APPLE_KEY_FILE, encoding="utf-8") as key_file:
    APPLE_PRIVATE_KEY = key_file.read()


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
    return jwt.encode(payload, APPLE_PRIVATE_KEY, algorithm="ES256", headers=headers)


def request_weatherkit_json(url, params=None):
    headers = {
        "Authorization": f"Bearer {create_weatherkit_token()}",
        "Accept": "application/json",
    }
    try:
        response = requests.get(
            url, params=params, headers=headers, timeout=WEATHERKIT_TIMEOUT_SECONDS
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
    params = {"dataSets": datasets, "timezone": WEATHERKIT_TIMEZONE}
    if extra_params:
        params.update(extra_params)
    return request_weatherkit_json(f"{WEATHERKIT_BASE_URL}/{lat}/{lon}", params)


def fetch_weatherkit_alert_details(alert):
    alert_id = alert.get("id")
    if not alert_id:
        return {}
    url = f"{WEATHERKIT_ALERT_BASE_URL}/{quote(str(alert_id), safe='')}"
    return request_weatherkit_json(url)
