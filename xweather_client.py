"""Authenticated requests to the Xweather lightning endpoint."""

import logging
import os

import requests

XWEATHER_LIGHTNING_URL = "https://data.api.xweather.com/lightning/closest"
XWEATHER_TIMEOUT_SECONDS = 15
XWEATHER_CREDENTIAL_PARAMS = {"client_id", "client_secret"}


class XweatherError(Exception):
    pass


def _omit_lightning_query(record):
    # urllib3 includes the request target, including its query string, in debug logs.
    return "/lightning/closest?" not in record.getMessage()


logging.getLogger("urllib3.connectionpool").addFilter(_omit_lightning_query)


def fetch_lightning_closest(query):
    client_id = os.environ.get("XWEATHER_CLIENT_ID")
    client_secret = os.environ.get("XWEATHER_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise XweatherError("Xweather credentials are not configured")

    params = [(name, value) for name, value in query if name not in XWEATHER_CREDENTIAL_PARAMS]
    params.extend([("client_id", client_id), ("client_secret", client_secret)])
    try:
        return requests.get(
            XWEATHER_LIGHTNING_URL,
            params=params,
            timeout=XWEATHER_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
    except requests.RequestException:
        raise XweatherError("Xweather request failed") from None
