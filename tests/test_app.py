import importlib
import os
import sys

import pytest


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    key_file = tmp_path_factory.mktemp("keys") / "test-key.p8"
    key_file.write_text("not-used-by-these-tests", encoding="utf-8")

    environment = {
        "APPLE_TEAM_ID": "test-team",
        "APPLE_KEY_ID": "test-key",
        "APPLE_SERVICE_ID": "com.example.weather",
        "APPLE_KEY_FILE": str(key_file),
        "WEATHER_API_KEY": "test-api-key",
    }

    os.environ.update(environment)
    sys.modules.pop("app", None)
    application = importlib.import_module("app").app
    application.config.update(TESTING=True)

    with application.test_client() as test_client:
        yield test_client


def test_health(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_weather_route_requires_api_key(client):
    response = client.get("/weather/current?lat=39.7392&lon=-104.9903")

    assert response.status_code == 401
    assert response.get_json() == {"error": "unauthorized"}


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("", "lat and lon are required"),
        ("?lat=north&lon=-104", "lat and lon must be numbers"),
        ("?lat=91&lon=-104", "lat must be between -90 and 90"),
        ("?lat=39&lon=-181", "lon must be between -180 and 180"),
    ],
)
def test_weather_route_validates_coordinates(client, query, message):
    response = client.get(
        f"/weather/current{query}",
        headers={"X-Weather-Key": "test-api-key"},
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": message}

