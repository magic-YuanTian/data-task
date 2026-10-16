"""Helpers shared by the build and the tasks of the NYC bike share environment."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import linprog

from datataskbench.anomaly import flag_days

# service stations (depots, warehouses, workshops, labs and test points), recognized by their names
SERVICE_RE = (r"(?:\bdepot\b|warehouse|workshop|\blab\b|\bqc\b|\bops\b|\btest\b|kiosk in a box|don't use|8d mobile|"
              r"lpi facility|ssp - basement)")
SERVICE = f"regexp_matches(lower({{c}}), '{SERVICE_RE.replace(chr(39), chr(39) * 2)}')"
PUBLIC = (f"NOT {SERVICE.format(c='start_station_name')} AND NOT {SERVICE.format(c='end_station_name')}")
# Central Park daily weather from GHCN-Daily, converted: tmax and tmin in C, prcp in mm, snow and snow depth in cm
WX = """(SELECT date, max(value) FILTER (WHERE element = 'TMAX') / 10.0 AS tmax,
            max(value) FILTER (WHERE element = 'TMIN') / 10.0 AS tmin,
            max(value) FILTER (WHERE element = 'PRCP') / 10.0 AS prcp,
            max(value) FILTER (WHERE element = 'SNOW') / 10.0 AS snow,
            max(value) FILTER (WHERE element = 'SNWD') / 10.0 AS snwd,
            max(value) FILTER (WHERE element = 'AWND') / 10.0 AS awnd
         FROM {t} WHERE id = 'USW00094728' GROUP BY 1)"""


def d(x) -> str:
    return str(pd.Timestamp(x).date())


def hav_sql(la1, lo1, la2, lo2) -> str:
    """Great-circle distance in meters."""
    return (f"(2 * 6371000 * asin(sqrt(power(sin(radians({la2} - {la1}) / 2), 2) + cos(radians({la1})) * "
            f"cos(radians({la2})) * power(sin(radians({lo2} - {lo1}) / 2), 2))))")


def hav_km(a, b):
    la1, lo1, la2, lo2 = map(np.radians, (a[0], a[1], b[0], b[1]))
    return 2 * 6371 * np.arcsin(np.sqrt(np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2))


def nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """n-th weekday (0=Mon) of a month; n=-1 gives the last one."""
    if n > 0:
        first = dt.date(year, month, 1)
        return first + dt.timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = dt.date(year + (month == 12), month % 12 + 1, 1) - dt.timedelta(days=1)
    return last - dt.timedelta(days=(last.weekday() - weekday) % 7)


def federal_holidays(years) -> pd.DataFrame:
    rows = []
    for y in years:
        fixed = [("New Year's Day", dt.date(y, 1, 1)), ("Independence Day", dt.date(y, 7, 4)),
                 ("Veterans Day", dt.date(y, 11, 11)), ("Christmas Day", dt.date(y, 12, 25))]
        for name, day in fixed:
            obs = day + dt.timedelta(days=1) if day.weekday() == 6 else day - dt.timedelta(days=1) \
                if day.weekday() == 5 else day
            rows.append((obs, name if obs == day else f"{name} (observed)"))
        rows += [(nth_weekday(y, 1, 0, 3), "Martin Luther King Jr. Day"), (nth_weekday(y, 2, 0, 3), "Presidents' Day"),
                 (nth_weekday(y, 5, 0, -1), "Memorial Day"), (nth_weekday(y, 9, 0, 1), "Labor Day"),
                 (nth_weekday(y, 10, 0, 2), "Columbus Day"), (nth_weekday(y, 11, 3, 4), "Thanksgiving Day")]
    return pd.DataFrame(sorted(rows), columns=["date", "holiday"])


# ---------------------------------------------------------------- transportation problem (rebalancing)
def solve_transport(sup, dem, D):
    m, n = D.shape
    A_ub = np.zeros((m, m * n))
    for i in range(m):
        A_ub[i, i * n:(i + 1) * n] = 1
    A_eq = np.zeros((n, m * n))
    for j in range(n):
        A_eq[j, j::n] = 1
    return linprog(D.ravel(), A_ub=A_ub, b_ub=sup, A_eq=A_eq, b_eq=dem, bounds=(0, None), method="highs")


def greedy_transport(sup, dem, D):
    s = sup.copy().astype(float)
    cost = 0.0
    for j in np.argsort(-dem):
        need = float(dem[j])
        for i in np.argsort(D[:, j]):
            take = min(need, s[i])
            if take > 0:
                cost += take * D[i, j]
                s[i] -= take
                need -= take
            if need == 0:
                break
    return cost


# ---------------------------------------------------------------- Spider 2.0 gold results
def _norm(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "∅"
    try:
        return f"{float(x):.1f}"
    except (TypeError, ValueError):
        return str(x).strip().lower()


def _close(a, b):
    try:
        a, b = float(a), float(b)
    except ValueError:
        return False
    return abs(a - b) <= 0.01 * max(1.0, abs(b))


def spider_match(df: pd.DataFrame, iid: str, cols: list[int], gold_dir: Path) -> dict:
    """True if the result equals one Spider gold variant on the compared columns (order ignored)."""
    ours = sorted(tuple(_norm(v) for v in r) for r in df.iloc[:, cols].itertuples(index=False))
    tried = []
    files = sorted(gold_dir.glob(f"{iid}_*.csv")) or sorted(gold_dir.glob(f"{iid}.csv"))
    for f in files:
        g = pd.read_csv(f)
        if g.shape[1] <= max(cols):
            continue
        theirs = sorted(tuple(_norm(v) for v in r) for r in g.iloc[:, cols].itertuples(index=False))
        tried.append(f.name)
        if ours == theirs:
            return dict(match=True, variant=f.name)
        if len(ours) == len(theirs) and all(
                all(a == b or _close(a, b) for a, b in zip(r1, r2_)) for r1, r2_ in zip(ours, theirs)):
            return dict(match=True, variant=f.name)
    return dict(match=False, tried=tried)


# ---------------------------------------------------------------- unusual days
def series(con, start="2013-07-01", end="2018-12-31") -> pd.DataFrame:
    """Daily public trips: total, departures and arrivals by borough, pass holder trips."""
    d = con.execute(f"""
        SELECT CAST(t.starttime AS DATE) AS day, count(*) AS total,
          count(*) FILTER (WHERE t.usertype = 'Customer') AS pass,
          count(*) FILTER (WHERE s1.borough = 'Manhattan') AS dep_manhattan,
          count(*) FILTER (WHERE s1.borough = 'Brooklyn') AS dep_brooklyn,
          count(*) FILTER (WHERE s1.borough = 'Queens') AS dep_queens,
          count(*) FILTER (WHERE s2.borough = 'Brooklyn') AS arr_brooklyn,
          count(*) FILTER (WHERE s2.borough = 'Queens') AS arr_queens
        FROM trips t LEFT JOIN stations s1 ON s1.station_id = t.start_station_id
        LEFT JOIN stations s2 ON s2.station_id = t.end_station_id
        WHERE t.starttime >= '{start}' AND t.starttime < DATE '{end}' + 1 AND {PUBLIC} GROUP BY 1""").df()
    d = d.set_index(pd.to_datetime(d.day)).drop(columns="day").sort_index()
    return d.reindex(pd.date_range(start, end), fill_value=0)


def context(con) -> pd.DataFrame:
    w = con.execute(f"SELECT * FROM {WX.format(t='ghcnd')}").df()
    w = w.set_index(pd.to_datetime(w.date)).drop(columns="date")
    h = con.execute("SELECT date, holiday FROM holidays").df()
    w["holiday"] = pd.Series(h.holiday.values, index=pd.to_datetime(h.date)).reindex(w.index)
    return w


def explain(day, ctx_row) -> str:
    """Weather (Central Park; prcp in mm, snow and snow depth in cm, tmax in C) or calendar reason for a low day."""
    if ctx_row is None:
        return "unexplained"
    if isinstance(ctx_row.get("holiday"), str):
        return f"holiday ({ctx_row['holiday']})"
    if day.month == 11 and day.weekday() == 4 and 23 <= day.day <= 29:
        return "holiday (day after Thanksgiving)"
    if day.month == 12 and day.day in (24, 31):
        return "holiday (Christmas Eve or New Year's Eve)"
    if (ctx_row.get("snow") or 0) >= 1 or (ctx_row.get("snwd") or 0) >= 5:
        return "snow"
    if (ctx_row.get("prcp") or 0) >= 2:
        return "rain"
    tmax = ctx_row.get("tmax")
    if tmax is not None and tmax <= 4:
        return "cold"
    return "unexplained"


def scan(con, start, end, cols=("total", "dep_manhattan", "dep_brooklyn", "dep_queens", "arr_brooklyn", "pass")) -> list:
    s = series(con, end=str(pd.Timestamp(end).date()))
    w = context(con)
    out = []
    for c in cols:
        if c not in s:
            continue
        for f in flag_days(s[c], start, end):
            d = pd.Timestamp(f["date"])
            row = w.loc[d].to_dict() if d in w.index else None
            out.append(dict(f, series=c, explained_by=explain(d, row),
                            weather=None if row is None else {k: row.get(k) for k in ("tmax", "prcp", "snow", "snwd")}))
    return out


def unexplained(flags) -> list:
    return [f for f in flags if f["explained_by"] == "unexplained"]
