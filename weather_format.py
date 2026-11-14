"""Response shaping helpers for WeatherKit data."""

from datetime import datetime, timedelta
from datetime import time as datetime_time
from zoneinfo import ZoneInfo


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


def normalize_alert(summary, details, collection_details_url=None):
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
        "issuedTime": alert.get("issuedTime"),
        "eventOnsetTime": alert.get("eventOnsetTime"),
        "eventEndTime": alert.get("eventEndTime"),
        "expireTime": alert.get("expireTime"),
        "areaName": area_name,
        "instructions": instructions,
        "detailsUrl": (
            alert.get("detailsUrl") or summary.get("detailsUrl") or collection_details_url
        ),
    }
    normalized.update(
        {field: value for field, value in optional_fields.items() if value is not None}
    )
    return normalized


