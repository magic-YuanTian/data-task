"""Helpers shared by the build and the tasks of the Brazilian e-commerce environment."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd


def easter(y: int) -> dt.date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return dt.date(y, month, day)


def nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """n-th weekday (0=Mon) of a month."""
    first = dt.date(year, month, 1)
    return first + dt.timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def brazil_calendar(years) -> pd.DataFrame:
    """National holidays, secondary holidays and observances as listed on the Wikipedia page "Public holidays in
    Brazil", and Black Friday (the Friday after the fourth Thursday of November). Black Consciousness Day
    (November 20), which the page lists today, became a national holiday only in 2024 and is left out."""
    rows = []
    for y in years:
        e = easter(y)
        for (m, d_, name) in [(1, 1, "New Year's Day"), (4, 21, "Tiradentes Day"), (5, 1, "Labour Day"),
                              (9, 7, "Independence Day"), (10, 12, "Our Lady of Aparecida"), (11, 2, "All Souls' Day"),
                              (11, 15, "Republic Day"), (12, 25, "Christmas Day")]:
            rows.append((dt.date(y, m, d_), name, "national holiday"))
        rows += [(e - dt.timedelta(days=47), "Carnival (Fat Tuesday)", "secondary holiday"),
                 (e - dt.timedelta(days=46), "Ash Wednesday", "secondary holiday"),
                 (e - dt.timedelta(days=2), "Good Friday", "secondary holiday"),
                 (e + dt.timedelta(days=60), "Corpus Christi", "secondary holiday"),
                 (dt.date(y, 6, 12), "Dia dos Namorados", "observance"),
                 (nth_weekday(y, 5, 6, 2), "Mother's Day", "observance"),
                 (nth_weekday(y, 8, 6, 2), "Father's Day", "observance"),
                 (dt.date(y, 10, 12), "Children's Day", "observance"),
                 (nth_weekday(y, 11, 3, 4) + dt.timedelta(days=1), "Black Friday", "shopping event")]
        rows += [(dt.date(y, 6, d_), f"Festa Junina ({n})", "observance")
                 for d_, n in [(13, "Santo Antônio"), (24, "São João"), (29, "São Pedro")]]
    return pd.DataFrame(sorted(rows), columns=["date", "name", "kind"])


def hav_sql(la1, lo1, la2, lo2) -> str:
    """Great-circle distance in kilometers."""
    return (f"(2 * 6371 * asin(sqrt(power(sin(radians({la2} - {la1}) / 2), 2) + cos(radians({la1})) * "
            f"cos(radians({la2})) * power(sin(radians({lo2} - {lo1}) / 2), 2))))")


def hav_km(la1, lo1, la2, lo2):
    la1, lo1, la2, lo2 = map(np.radians, (la1, lo1, la2, lo2))
    return 2 * 6371 * np.arcsin(np.sqrt(np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2))
