# Storm Chasers Apple Backend

A small Flask API that authenticates with Apple WeatherKit and exposes a
shared-key-protected subset of current, hourly, and next-hour weather data.

## Local setup

Python 3.12 or newer is required.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cp .env.example .env
```

Edit `.env`, point `APPLE_KEY_FILE` at your downloaded Apple WeatherKit `.p8`
key, and export the variables before starting the app:

```bash
set -a
source .env
set +a
flask --app app run --port 5001
```

For a production-style local run:

```bash
gunicorn --bind 127.0.0.1:5001 app:app
```

Do not commit `.env`, `.p8` keys, or virtual environments. They are covered by
the repository's `.gitignore`.

## API

`GET /health` is public. Weather routes require the configured shared secret in
the `X-Weather-Key` header and `lat`/`lon` query parameters:

- `GET /weather/current`
- `GET /weather/hourly`
- `GET /weather/next-hour`

Example:

```bash
curl -H "X-Weather-Key: $WEATHER_API_KEY" \
  "http://127.0.0.1:5001/weather/current?lat=39.7392&lon=-104.9903"
```

## Checks

```bash
ruff check .
pytest
```

`test_script.py` is a manual WeatherKit connectivity check. It currently uses
the local Apple key file and makes a real network request; it is not part of the
automated test suite.

## GitHub

This repository can be committed locally without a GitHub account. To publish
it later, create an empty GitHub repository and add its URL as `origin`:

```bash
git remote add origin git@github.com:YOUR_ACCOUNT/storm-chasers-apple-backend.git
git push -u origin main
```

