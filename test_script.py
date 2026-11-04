#!/usr/bin/env python3

import time
import json
import requests
import jwt  # pip install pyjwt cryptography


TEAM_ID = "C3F7H8C997"
KEY_ID = "4VCDW876YY"
SERVICE_ID = "com.paweatheraction.paweather"

P8_FILE = "./AuthKey_4VCDW876YY.p8"

# Test coordinates
LAT = 33.3943
LON = -104.5230


def make_token():
    with open(P8_FILE, "r") as f:
        private_key = f.read()

    now = int(time.time())

    headers = {
        "alg": "ES256",
        "kid": KEY_ID,
        "id": f"{TEAM_ID}.{SERVICE_ID}",
    }

    payload = {
        "iss": TEAM_ID,
        "iat": now,
        "exp": now + 3600,
        "sub": SERVICE_ID,
    }

    return jwt.encode(
        payload,
        private_key,
        algorithm="ES256",
        headers=headers,
    )


def main():
    token = make_token()

    url = (
        f"https://weatherkit.apple.com/api/v1/weather/en/"
        f"{LAT}/{LON}"
    )

    params = {
        "dataSets": "forecastNextHour",
        "timezone": "America/Denver",
    }

    headers = {
        "Authorization": f"Bearer {token}"
    }

    r = requests.get(url, params=params, headers=headers, timeout=30)

    print("HTTP:", r.status_code)
    print("URL:", r.url)

    try:
        data = r.json()
        print(json.dumps(data, indent=2))
    except Exception:
        print(r.text)


if __name__ == "__main__":
    main()
