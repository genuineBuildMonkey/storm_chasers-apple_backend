import calendar
from datetime import date

import pytest

from moon_illumination import MOON_ILLUMINATION_PERCENT, moon_illumination


@pytest.mark.parametrize(
    ("year", "first", "last"),
    [
        (2026, 91, 48),
        (2027, 38, 9),
        (2028, 15, 99),
        (2029, 100, 22),
        (2030, 13, 44),
        (2031, 55, 95),
        (2032, 91, 3),
    ],
)
def test_year_tables_cover_calendar_with_source_boundaries(year, first, last):
    months = MOON_ILLUMINATION_PERCENT[year]
    assert len(months) == 12
    assert [len(month) for month in months] == [
        calendar.monthrange(year, month)[1] for month in range(1, 13)
    ]
    assert all(0 <= value <= 100 for month in months for value in month)
    assert moon_illumination(date(year, 1, 1)) == first
    assert moon_illumination(date(year, 12, 31)) == last


def test_leap_days_match_usno_year_tables():
    assert moon_illumination(date(2028, 2, 29)) == 11
    assert moon_illumination(date(2032, 2, 29)) == 93
