"""Daily anomaly detection against a weekday baseline ."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .db import ordered


def daily(con, sql) -> pd.Series:
    d = con.execute(ordered(con, sql)).df()
    s = pd.Series(d.iloc[:, 1].values, index=pd.to_datetime(d.iloc[:, 0])).sort_index()
    return s.reindex(pd.date_range(s.index.min(), s.index.max()), fill_value=0)


def weekday_baseline(s: pd.Series, weeks: int = 4) -> pd.Series:
    """Expected count: mean of the same weekday over the previous `weeks` weeks."""
    lag = [s.shift(7 * k) for k in range(1, weeks + 1)]
    return pd.concat(lag, axis=1).mean(axis=1)


def flag_days(s: pd.Series, start, end, z=4.0, min_change=0.4) -> list:
    """Days whose count departs from the weekday baseline by more than z robust deviations (log ratio, scale from the
    previous eight weeks) and by more than min_change in relative terms. Robust to overdispersed real counts."""
    e = weekday_baseline(s)
    r = np.log((s + 1) / (e + 1))
    out = []
    for d in s.index[(s.index >= pd.Timestamp(start)) & (s.index <= pd.Timestamp(end))]:
        if pd.isna(e[d]):
            continue
        hist = r[(r.index < d) & (r.index >= d - pd.Timedelta(days=56))].dropna()
        if len(hist) < 20:
            continue
        scale = max(1.4826 * np.median(np.abs(hist - hist.median())), 0.05)
        zz = (r[d] - hist.median()) / scale
        ch = (s[d] + 1) / (e[d] + 1) - 1
        if abs(zz) > z and abs(ch) > min_change:
            out.append(dict(date=str(d.date()), count=int(s[d]), expected=round(float(e[d]), 1),
                            direction="up" if zz > 0 else "down", z=round(float(zz), 1)))
    return out
