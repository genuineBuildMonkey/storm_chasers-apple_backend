import importlib
import os
import sys
from datetime import date, datetime, timedelta

import pytest


@pytest.fixture(scope="module")
def app_module(tmp_path_factory):
    key_file = tmp_path_factory.mktemp("keys") / "test-key.p8"
    key_file.write_text("not-used-by-these-tests", encoding="utf-8")
    os.environ.update(
        {
            "APPLE_TEAM_ID": "test-team",
            "APPLE_KEY_ID": "test-key",
            "APPLE_SERVICE_ID": "com.example.weather",
            "APPLE_KEY_FILE": str(key_file),
            "WEATHER_API_KEY": "test-api-key",
            "WEATHERKIT_TIMEZONE": "America/Denver",
            "WEATHERKIT_COUNTRY_CODE": "US",
        }
    )
    sys.modules.pop("app", None)
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True)
    return module


@pytest.fixture
def client(app_module):
    with app_module.app.test_client() as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def fixed_timezone(app_module, monkeypatch):
    monkeypatch.setattr(
        app_module,
        "timezone_for_coordinates",
        lambda lat, lon: "America/Denver",
    )


def auth_headers():
    return {"X-Weather-Key": "test-api-key"}


def daily_weatherkit_response(day_count=10):
    first_day = date(2026, 11, 3)
    days = []
    for index in range(day_count):
        forecast_date = first_day + timedelta(days=index)
        days.append(
            {
                "forecastStart": f"{forecast_date.isoformat()}T07:00:00-07:00",
                "temperatureMax": 18.5 + index,
                "temperatureMin": 4.25 + index,
                "conditionCode": "MostlyClear",
                "precipitationChance": 0.125,
                "sunrise": f"{forecast_date.isoformat()}T06:30:00-07:00",
                "sunset": f"{forecast_date.isoformat()}T16:55:00-07:00",
                "moonPhase": "waningCrescent",
                "moonrise": f"{forecast_date.isoformat()}T02:15:00-07:00",
                "moonset": f"{forecast_date.isoformat()}T14:20:00-07:00",
                "daytimeForecast": {
                    "forecastStart": f"{forecast_date.isoformat()}T07:00:00-07:00",
                    "forecastEnd": f"{forecast_date.isoformat()}T19:00:00-07:00",
                    "conditionCode": "MostlyClear",
                    "precipitationChance": 0.125,
                    "precipitationAmount": 1.5,
                    "snowfallAmount": 0,
                    "cloudCover": 0.24,
                    "humidity": 0.375,
                    "windSpeed": 12.5,
                    "windDirection": 245,
                },
                "overnightForecast": {
                    "forecastStart": f"{forecast_date.isoformat()}T19:00:00-07:00",
                    "forecastEnd": (
                        f"{(forecast_date + timedelta(days=1)).isoformat()}"
                        "T07:00:00-07:00"
                    ),
                    "conditionCode": "Clear",
                    "precipitationChance": 0.05,
                    "precipitationAmount": 0.25,
                    "snowfallAmount": 0,
                    "cloudCover": 0.1,
                    "humidity": 0.625,
                    "windSpeed": 7.25,
                    "windDirection": 210,
                },
            }
        )

    return {
        "forecastDaily": {
            "metadata": {
                "latitude": 39.7392,
                "longitude": -104.9903,
                "readTime": "2026-11-03T20:00:00Z",
                "units": "metric",
            },
            "days": days,
        }
    }


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_weather_route_requires_api_key(client):
    response = client.get("/weather/daily?lat=39.7392&lon=-104.9903")
    assert response.status_code == 401
    assert response.get_json() == {"error": "unauthorized"}


@pytest.mark.parametrize(
    "route", ["hourly/detail", "daily", "daily/detail", "alerts"]
)
@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("", "lat and lon are required"),
        ("?lat=north&lon=-104", "lat and lon must be numbers"),
        ("?lat=91&lon=-104", "lat must be between -90 and 90"),
        ("?lat=39&lon=-181", "lon must be between -180 and 180"),
    ],
)
def test_new_routes_validate_coordinates(client, route, query, message):
    response = client.get(f"/weather/{route}{query}", headers=auth_headers())
    assert response.status_code == 400
    assert response.get_json() == {"error": message}


def test_daily_returns_ten_chronological_metric_days(client, app_module, monkeypatch):
    upstream = daily_weatherkit_response(11)
    upstream["forecastDaily"]["days"].reverse()
    calls = []

    def fake_fetch(lat, lon, dataset, extra_params=None):
        calls.append((dataset, extra_params))
        return upstream

    monkeypatch.setattr(app_module, "fetch_weatherkit", fake_fetch)

    response = client.get(
        "/weather/daily?lat=39.7392&lon=-104.9903", headers=auth_headers()
    )
    body = response.get_json()

    assert response.status_code == 200
    assert len(body["days"]) == 10
    assert body["days"][0]["forecastStart"].startswith("2026-11-03")
    assert body["days"][-1]["forecastStart"].startswith("2026-11-12")
    assert body["timeZone"] == "America/Denver"
    assert body["units"] == "metric"
    assert body["days"][0]["temperatureMax"] == 18.5
    assert body["days"][0]["temperatureMin"] == 4.25
    assert body["days"][0]["precipitationChance"] == 0.125
    assert body["days"][0]["daytimeForecast"]["humidity"] == 0.375
    assert body["days"][0]["daytimeForecast"]["windSpeed"] == 12.5
    assert calls == [("forecastDaily", {"timezone": "America/Denver"})]


def test_daily_detail_returns_direct_and_aggregated_fields(
    client, app_module, monkeypatch
):
    upstream = daily_weatherkit_response()
    upstream["forecastHourly"] = {
        "hours": [
            {
                "forecastStart": "2026-11-03T06:00:00-07:00",
                "temperatureApparent": -20,
                "windGust": 100,
                "uvIndex": 10,
                "pressure": 900,
            },
            {
                "forecastStart": "2026-11-03T07:00:00-07:00",
                "temperatureApparent": 8,
                "windGust": 18,
                "uvIndex": 1,
                "pressure": 1008,
            },
            {
                "forecastStart": "2026-11-03T13:00:00-07:00",
                "temperatureApparent": 17,
                "windGust": 26,
                "uvIndex": 4,
                "pressure": 1012,
            },
            {
                "forecastStart": "2026-11-03T19:00:00-07:00",
                "temperatureApparent": 9,
                "windGust": 14,
                "uvIndex": 1,
                "pressure": 1014,
            },
            {
                "forecastStart": "2026-11-04T03:00:00-07:00",
                "temperatureApparent": 3,
                "windGust": 20,
                "uvIndex": 0,
                "pressure": 1010,
            },
            {
                "forecastStart": "2026-11-04T07:00:00-07:00",
                "temperatureApparent": -10,
                "windGust": 90,
                "uvIndex": 9,
                "pressure": 800,
            },
        ]
    }
    calls = []

    def fake_fetch(lat, lon, dataset, extra_params=None):
        calls.append((dataset, extra_params))
        return upstream

    monkeypatch.setattr(app_module, "fetch_weatherkit", fake_fetch)

    response = client.get(
        "/weather/daily/detail?lat=39.7392&lon=-104.9903&date=2026-11-03",
        headers=auth_headers(),
    )
    body = response.get_json()
    first_day = body["day"]

    assert response.status_code == 200
    assert body["timeZone"] == "America/Denver"
    assert body["units"] == "metric"
    assert first_day["sunrise"] == "2026-11-03T06:30:00-07:00"
    assert first_day["sunset"] == "2026-11-03T16:55:00-07:00"
    assert first_day["moonPhase"] == "waningCrescent"
    assert first_day["moonrise"] == "2026-11-03T02:15:00-07:00"
    assert first_day["moonset"] == "2026-11-03T14:20:00-07:00"
    assert first_day["daytimeForecast"]["precipitationAmount"] == 1.5
    assert first_day["daytimeForecast"]["snowfallAmount"] == 0
    assert first_day["daytimeForecast"]["cloudCover"] == 0.24
    assert first_day["daytimeForecast"]["temperatureApparent"] == 17
    assert first_day["daytimeForecast"]["windGust"] == 26
    assert first_day["daytimeForecast"]["maxUvIndex"] == 4
    assert first_day["daytimeForecast"]["pressure"] == 1010
    assert first_day["overnightForecast"]["temperatureApparent"] == 3
    assert first_day["overnightForecast"]["windGust"] == 20
    assert first_day["overnightForecast"]["maxUvIndex"] == 1
    assert first_day["overnightForecast"]["pressure"] == 1012
    assert calls == [
        (
            "forecastDaily,forecastHourly",
            {
                "timezone": "America/Denver",
                "dailyStart": "2026-11-03T00:00:00-07:00",
                "dailyEnd": "2026-11-04T00:00:00-07:00",
                "hourlyStart": "2026-11-03T07:00:00-07:00",
                "hourlyEnd": "2026-11-04T07:00:00-07:00",
            },
        )
    ]


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("?lat=39.7392&lon=-104.9903", "date is required"),
        (
            "?lat=39.7392&lon=-104.9903&date=November-3",
            "date must use YYYY-MM-DD format",
        ),
    ],
)
def test_daily_detail_validates_date(client, query, message):
    response = client.get(f"/weather/daily/detail{query}", headers=auth_headers())
    assert response.status_code == 400
    assert response.get_json() == {"error": message}


def test_daily_preserves_days_with_missing_parts(client, app_module, monkeypatch):
    upstream = daily_weatherkit_response()
    del upstream["forecastDaily"]["days"][0]["daytimeForecast"]
    del upstream["forecastDaily"]["days"][1]["overnightForecast"]
    monkeypatch.setattr(app_module, "fetch_weatherkit", lambda *args, **kwargs: upstream)

    response = client.get(
        "/weather/daily?lat=39.7392&lon=-104.9903", headers=auth_headers()
    )
    days = response.get_json()["days"]

    assert response.status_code == 200
    assert len(days) == 10
    assert days[0]["daytimeForecast"] is None
    assert days[1]["overnightForecast"] is None


def test_daily_returns_short_upstream_forecast(client, app_module, monkeypatch):
    monkeypatch.setattr(
        app_module,
        "fetch_weatherkit",
        lambda *args, **kwargs: daily_weatherkit_response(9),
    )
    response = client.get(
        "/weather/daily?lat=39.7392&lon=-104.9903", headers=auth_headers()
    )
    assert response.status_code == 200
    assert len(response.get_json()["days"]) == 9


def test_hourly_defaults_to_240_hours_on_v1(client, app_module, monkeypatch):
    calls = []

    def fake_fetch(lat, lon, dataset, extra_params=None):
        calls.append((dataset, extra_params))
        return {
            "forecastHourly": {
                "metadata": {"readTime": "2026-11-03T20:00:00Z"},
                "hours": [],
            }
        }

    monkeypatch.setattr(app_module, "fetch_weatherkit", fake_fetch)
    response = client.get(
        "/weather/hourly?lat=39.7392&lon=-104.9903", headers=auth_headers()
    )

    assert response.status_code == 200
    assert response.get_json()["timeZone"] == "America/Denver"
    assert calls[0][0] == "forecastHourly"
    params = calls[0][1]
    assert params["timezone"] == "America/Denver"
    assert datetime.fromisoformat(params["hourlyEnd"]) - datetime.fromisoformat(
        params["hourlyStart"]
    ) == timedelta(hours=240)


def test_hourly_accepts_hours_parameter(client, app_module, monkeypatch):
    calls = []

    def fake_fetch(lat, lon, dataset, extra_params=None):
        calls.append(extra_params)
        return {"forecastHourly": {"metadata": {}, "hours": []}}

    monkeypatch.setattr(app_module, "fetch_weatherkit", fake_fetch)
    response = client.get(
        "/weather/hourly?lat=39.7392&lon=-104.9903&hours=72",
        headers=auth_headers(),
    )

    assert response.status_code == 200
    params = calls[0]
    assert datetime.fromisoformat(params["hourlyEnd"]) - datetime.fromisoformat(
        params["hourlyStart"]
    ) == timedelta(hours=72)


@pytest.mark.parametrize(
    ("hours", "message"),
    [
        ("storm", "hours must be an integer"),
        ("0", "hours must be between 1 and 240"),
        ("241", "hours must be between 1 and 240"),
    ],
)
def test_hourly_validates_hours_parameter(client, hours, message):
    response = client.get(
        f"/weather/hourly?lat=39.7392&lon=-104.9903&hours={hours}",
        headers=auth_headers(),
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": message}


def test_hourly_detail_returns_hour_conditions(client, app_module, monkeypatch):
    upstream_hour = {
        "forecastStart": "2026-11-03T03:00:00-07:00",
        "conditionCode": "MostlyClear",
        "daylight": False,
        "temperature": 18.333,
        "temperatureApparent": 17.778,
        "precipitationAmount": 0,
        "precipitationChance": 0.05,
        "precipitationType": "clear",
        "snowfallIntensity": 0,
        "humidity": 0.42,
        "windSpeed": 9.656,
        "windDirection": 270,
        "windGust": 16.093,
        "uvIndex": 0,
        "cloudCover": 0.12,
        "pressure": 1010,
        "pressureTrend": "falling",
        "temperatureDewPoint": 5.1,
        "visibility": 24000,
    }
    calls = []

    def fake_fetch(lat, lon, dataset, extra_params=None):
        calls.append((dataset, extra_params))
        return {
            "forecastHourly": {
                "metadata": {
                    "latitude": 39.7392,
                    "longitude": -104.9903,
                    "readTime": "2026-11-03T08:00:00Z",
                    "units": "metric",
                },
                "hours": [upstream_hour],
            }
        }

    monkeypatch.setattr(app_module, "fetch_weatherkit", fake_fetch)
    response = client.get(
        "/weather/hourly/detail"
        "?lat=39.7392&lon=-104.9903&time=2026-11-03T03:00:00-07:00",
        headers=auth_headers(),
    )
    body = response.get_json()

    assert response.status_code == 200
    assert body["timeZone"] == "America/Denver"
    assert body["units"] == "metric"
    assert body["hour"] == upstream_hour
    assert calls == [
        (
            "forecastHourly",
            {
                "timezone": "America/Denver",
                "hourlyStart": "2026-11-03T03:00:00-07:00",
                "hourlyEnd": "2026-11-03T04:00:00-07:00",
            },
        )
    ]


@pytest.mark.parametrize(
    ("time_query", "message"),
    [
        ("", "time is required"),
        ("&time=3am", "time must be an ISO 8601 timestamp with offset"),
        (
            "&time=2026-11-03T03:00:00",
            "time must be an ISO 8601 timestamp with offset",
        ),
    ],
)
def test_hourly_detail_validates_time(client, time_query, message):
    response = client.get(
        f"/weather/hourly/detail?lat=39.7392&lon=-104.9903{time_query}",
        headers=auth_headers(),
    )
    assert response.status_code == 400
    assert response.get_json() == {"error": message}


def test_hourly_detail_returns_unavailable_for_missing_hour(
    client, app_module, monkeypatch
):
    monkeypatch.setattr(
        app_module,
        "fetch_weatherkit",
        lambda *args, **kwargs: {"forecastHourly": {"hours": []}},
    )
    response = client.get(
        "/weather/hourly/detail"
        "?lat=39.7392&lon=-104.9903&time=2026-11-03T03:00:00Z",
        headers=auth_headers(),
    )
    assert response.status_code == 502
    assert response.get_json() == {"error": "hourly detail unavailable"}


def test_alerts_returns_empty_list_when_unavailable(client, app_module, monkeypatch):
    monkeypatch.setattr(app_module, "fetch_weatherkit", lambda *args, **kwargs: {})
    response = client.get(
        "/weather/alerts?lat=39.7392&lon=-104.9903", headers=auth_headers()
    )
    assert response.status_code == 200
    assert response.get_json()["alerts"] == []


def test_alerts_expand_details_and_deduplicate(client, app_module, monkeypatch):
    summary = {
        "id": "alert-123",
        "description": "Tornado Warning",
        "detailsUrl": "https://weatherkit.apple.com/alertDetails/alert-123",
        "severity": "extreme",
        "urgency": "immediate",
        "certainty": "observed",
        "source": "National Weather Service",
        "effectiveTime": "2026-11-03T20:00:00Z",
        "issuedTime": "2026-11-03T19:58:00Z",
        "eventOnsetTime": "2026-11-03T20:05:00Z",
        "eventEndTime": "2026-11-03T21:00:00Z",
        "expireTime": "2026-11-03T21:15:00Z",
        "areaName": "Denver County",
    }
    calls = []

    def fake_fetch(lat, lon, dataset, extra_params=None):
        calls.append((dataset, extra_params))
        return {
            "weatherAlerts": {
                "metadata": {"readTime": "2026-11-03T20:00:00Z"},
                "alerts": [summary, dict(summary)],
            }
        }

    def fake_details(alert):
        calls.append(("detail", alert["id"]))
        return {
            "id": "alert-123",
            "messages": [
                {"language": "en", "text": "A tornado is occurring. Take cover now."},
                {"type": "instruction", "text": "Move to an interior room."},
            ],
        }

    monkeypatch.setattr(app_module, "fetch_weatherkit", fake_fetch)
    monkeypatch.setattr(app_module, "fetch_weatherkit_alert_details", fake_details)

    response = client.get(
        "/weather/alerts?lat=39.7392&lon=-104.9903", headers=auth_headers()
    )
    alerts = response.get_json()["alerts"]

    assert response.status_code == 200
    assert calls == [
        ("weatherAlerts", {"countryCode": "US"}),
        ("detail", "alert-123"),
    ]
    assert len(alerts) == 1
    assert alerts[0] == {
        "id": "alert-123",
        "event": "Tornado Warning",
        "headline": "Tornado Warning",
        "description": "A tornado is occurring. Take cover now.",
        "severity": "extreme",
        "urgency": "immediate",
        "certainty": "observed",
        "source": "National Weather Service",
        "effectiveTime": "2026-11-03T20:00:00Z",
        "issuedTime": "2026-11-03T19:58:00Z",
        "eventOnsetTime": "2026-11-03T20:05:00Z",
        "eventEndTime": "2026-11-03T21:00:00Z",
        "expireTime": "2026-11-03T21:15:00Z",
        "areaName": "Denver County",
        "instructions": "Move to an interior room.",
        "detailsUrl": "https://weatherkit.apple.com/alertDetails/alert-123",
    }


def test_alerts_keep_summary_when_details_fail_and_use_collection_link(
    client, app_module, monkeypatch
):
    summary = {
        "id": "alert-456",
        "description": "Flood Warning",
        "severity": "severe",
        "source": "National Weather Service",
        "effectiveTime": "2026-11-03T20:00:00Z",
        "expireTime": "2026-11-03T22:00:00Z",
    }
    collection_link = "https://weatherkit.apple.com/weather-alerts"
    monkeypatch.setattr(
        app_module,
        "fetch_weatherkit",
        lambda *args, **kwargs: {
            "weatherAlerts": {
                "alerts": [summary],
                "detailsUrl": collection_link,
            }
        },
    )

    detail_calls = []

    def fail_details(alert):
        detail_calls.append(alert["id"])
        raise app_module.WeatherKitError("WeatherKit request failed")

    monkeypatch.setattr(app_module, "fetch_weatherkit_alert_details", fail_details)
    response = client.get(
        "/weather/alerts?lat=39.7392&lon=-104.9903", headers=auth_headers()
    )

    assert response.status_code == 200
    assert detail_calls == ["alert-456"]
    assert response.get_json()["alerts"] == [
        {
            "id": "alert-456",
            "event": "Flood Warning",
            "headline": "Flood Warning",
            "description": "Flood Warning",
            "severity": "severe",
            "source": "National Weather Service",
            "effectiveTime": "2026-11-03T20:00:00Z",
            "expireTime": "2026-11-03T22:00:00Z",
            "detailsUrl": collection_link,
        }
    ]


@pytest.mark.parametrize("route", ["daily", "alerts"])
def test_new_routes_surface_weatherkit_failures(client, app_module, monkeypatch, route):
    def fail(*args, **kwargs):
        raise app_module.WeatherKitError("WeatherKit returned HTTP 503")

    monkeypatch.setattr(app_module, "fetch_weatherkit", fail)
    response = client.get(
        f"/weather/{route}?lat=39.7392&lon=-104.9903", headers=auth_headers()
    )
    assert response.status_code == 502
    assert response.get_json() == {"error": "WeatherKit returned HTTP 503"}
