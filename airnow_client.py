"""Authenticated requests to AirNow's current observation and forecast endpoints."""

import logging
import os

import requests

AIRNOW_BASE_URL = "https://www.airnowapi.org/aq"
AIRNOW_OBSERVATION_PATH = "/observation/current/ziplatlong/"
AIRNOW_FORECAST_PATH = "/forecast/current/"
AIRNOW_TIMEOUT_SECONDS = 15


class AirNowError(Exception):
    pass


def _omit_airnow_query(record):
    # urllib3 logs request targets with their query strings at debug level.
    message = record.getMessage()
    return not any(
        f"/aq{path}?" in message for path in (AIRNOW_OBSERVATION_PATH, AIRNOW_FORECAST_PATH)
    )


logging.getLogger("urllib3.connectionpool").addFilter(_omit_airnow_query)


def fetch_airnow(path, query):
    api_key = os.environ.get("AIRNOW_API_KEY")
    if not api_key:
        raise AirNowError("AirNow API key is not configured")

    params = {
        "latitude": query.get("latitude"),
        "longitude": query.get("longitude"),
        "format": "application/json",
        "API_KEY": api_key,
    }
    try:
        response = requests.get(
            f"{AIRNOW_BASE_URL}{path}",
            params=params,
            timeout=AIRNOW_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
    except requests.RequestException:
        raise AirNowError("AirNow request failed") from None

    if not 200 <= response.status_code < 300:
        raise AirNowError(f"AirNow returned HTTP {response.status_code}")
    return response
