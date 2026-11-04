# Repository guidance

## Scope

This repository contains a Python 3.12 Flask proxy for Apple WeatherKit. Keep
changes small and focused; the API is consumed by a separate client.

## Setup and checks

- Create a virtual environment with `python3.12 -m venv .venv`.
- Install development dependencies with `python -m pip install -e ".[dev]"`.
- Run static checks with `ruff check .`.
- Run automated tests with `pytest`.
- Run locally with `flask --app app run --port 5001` after exporting the
  variables documented in `.env.example`.

## Conventions

- Never commit Apple `.p8` keys, `.env` files, API keys, tokens, or credentials.
- Read configuration and secrets from environment variables.
- Use request timeouts for all outbound HTTP calls.
- Keep error responses JSON and avoid exposing credential or upstream response
  details.
- Add or update tests for behavior changes. Mock WeatherKit calls in automated
  tests; do not make live network requests from the test suite.
- Preserve the existing response field names unless an API change is explicit.

