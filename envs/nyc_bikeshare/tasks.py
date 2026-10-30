"""Tasks of the NYC bike share environment, each with its ground-truth reasoning path.

Every task is answered from the records (out/nyc_bikeshare/world.duckdb), from public documents of the data
publishers, or from an existing benchmark (Spider 2.0). Forecast tasks state the period they may use and are scored
against the recorded values. No record is planted; a task that needs a planted event would set planted=True and name
the plant.

A task lists the lower-level tasks it builds on (depends_on); a step that reuses one names it (uses).

    python -m envs.nyc_bikeshare.tasks       # writes out/nyc_bikeshare/tasks.json
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy import stats

from datataskbench import spectrum
from datataskbench import stats as S
from datataskbench.db import ordered
from datataskbench.levels import LEVEL_NAMES, TASK_TYPES
from datataskbench.paths import repo_path

from .build import CFG, DOCUMENTS, OUT, SOURCES
from .common import PUBLIC, SERVICE, WX, greedy_transport, hav_km, hav_sql, scan, solve_transport, spider_match

DOW = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
DOCS = DOCUMENTS
SPIDER = repo_path(CFG["source"]["spider_gold"]) / "exec_result"


def quote(key, *idx):
    d = DOCS[key]
    return dict(document=d["document"], url=d["url"], text=" ".join(d["text"][i] for i in idx))


class Env:
    def __init__(self, build: Path = OUT):
        self.build = build
        # the explorer runs the step queries on the same database
        self.con = duckdb.connect(str(build / "world.duckdb"), read_only=True)
        self.con.execute("LOAD spatial")

    def q(self, sql):
        return self.con.execute(ordered(self.con, sql)).df()

    def v(self, sql):
        return self.con.execute(sql).fetchone()[0]


def tab(df: pd.DataFrame, digits=4, limit=None) -> dict:
    rows = []
    for r in (df if limit is None else df.head(limit)).itertuples(index=False):
        out = []
        for x in r:
            if isinstance(x, pd.Timestamp):
                x = str(x.date()) if x == x.normalize() else str(x)
            elif isinstance(x, (float, np.floating)):
                x = None if np.isnan(x) else float(f"{x:.{digits}g}")
            elif isinstance(x, (np.integer,)):
                x = int(x)
            elif isinstance(x, (np.bool_,)):
                x = bool(x)
            elif hasattr(x, "isoformat"):
                x = x.isoformat()
            out.append(x)
        rows.append(out)
    res = {"columns": list(df.columns), "rows": rows}
    if limit is not None and len(df) > limit:
        res["note"] = f"first {limit} of {len(df)} rows"
    return res


def step(title, *, sql=None, doc=None, result=None, note=None, uses=None, source=None):
    src = source or ("db" if sql else "document" if doc else "sub-task" if uses else "calculation")
    return {k: v for k, v in dict(title=title, source=src, sql=sql.strip() if sql else None,
                                  doc=doc, result=result, note=note, uses=uses).items() if v is not None}


def pct(x, k=0):
    return f"{x * 100:.{k}f}%"


TASKS = []


def task(id, task_type, question, answer_format, depends_on=(), scoring=None, control=None):
    def deco(fn):
        TASKS.append(dict(id=id, task_type=task_type, question=question, answer_format=answer_format,
                          depends_on=list(depends_on), scoring=scoring or {"metric": "key_fact_recall", "judge": "llm"},
                          control=control, fn=fn))
        return fn
    return deco


TEXT = {"type": "text"}

# ====================================================================== Q1 Definition
@task("q1.trip_records", "Q1.1",
      "What does one row of trips record, and what does tripduration measure? Is it the same as stoptime minus "
      "starttime, and which rides are not in the data?", TEXT)
def trip_records(e):
    sql = """SELECT count(*) AS trips,
  round(avg((abs(tripduration - epoch(stoptime - starttime)) <= 1)::INT) * 100, 2) AS pct_within_1s,
  count(*) FILTER (WHERE abs(tripduration - epoch(stoptime - starttime)) > 60) AS off_by_more_than_1min,
  min(tripduration) AS min_seconds,
  count(*) FILTER (WHERE tripduration > 86400) AS over_24h,
  round(max(tripduration) / 86400.0, 1) AS max_days
FROM trips"""
    a = e.q(sql).iloc[0]
    steps = [
        step("Read the publisher's description of the fields and of the processing", doc=quote("publisher", 3, 4)),
        step("Compare tripduration with stoptime minus starttime, and find the extremes", sql=sql, result=tab(e.q(sql))),
    ]
    gold = {"key_facts": [
        "One row is one ride, from unlocking a bike at a station to returning it; tripduration is the ride time in seconds.",
        f"tripduration equals stoptime minus starttime within one second for {a.pct_within_1s}% of trips; "
        f"{int(a.off_by_more_than_1min):,} trips differ by more than a minute.",
        f"The publisher removed staff trips, trips to or from test stations and trips under 60 seconds: the shortest "
        f"value is {int(a.min_seconds)} seconds.",
        f"{int(a.over_24h):,} trips last more than 24 hours (the longest {a.max_days} days); they are bikes not docked "
        "properly or lost, and should be excluded or capped when averaging ride time."], "required": [0, 1, 2]}
    resp = ("Each row of `trips` is one ride: from unlocking a bike at a station to returning it at a station. "
            f"`tripduration` is the ride time in seconds and matches stoptime minus starttime within a second for "
            f"{a.pct_within_1s}% of trips. The publisher removed staff trips, test-station trips and rides under 60 seconds, "
            f"so the minimum is {int(a.min_seconds)} s. {int(a.over_24h):,} rides exceed 24 hours (up to {a.max_days} days); "
            "treat them as outliers (exclude or cap) when computing average ride time.")
    return steps, gold, resp


@task("q1.usertype", "Q1.1",
      "What do the values of trips.usertype mean, what share of 2017 trips does each make, and are there trips "
      "without a user type?", TEXT)
def usertype(e):
    sql = """SELECT coalesce(usertype, '(missing)') AS usertype, count(*) AS trips_2017,
  round(count(*) * 100.0 / sum(count(*)) OVER (), 1) AS pct
FROM trips WHERE year(starttime) = 2017 GROUP BY 1 ORDER BY 2 DESC"""
    sql2 = """SELECT count(*) AS trips_without_usertype, min(starttime)::DATE AS first, max(starttime)::DATE AS last
FROM trips WHERE usertype IS NULL"""
    a, b = e.q(sql), e.q(sql2).iloc[0]
    sh = a.set_index("usertype").pct
    steps = [
        step("Read the publisher's definition", doc=quote("publisher", 0)),
        step("Count 2017 trips by user type", sql=sql, result=tab(a)),
        step("Find the trips without a user type", sql=sql2, result=tab(e.q(sql2))),
    ]
    gold = {"key_facts": [
        "Subscriber means an annual member; Customer means a 24-hour pass or 3-day pass user (publisher's definition).",
        f"In 2017 members made {sh['Subscriber']}% of trips and pass users {sh['Customer']}%.",
        f"{int(b.trips_without_usertype):,} trips have no user type, all between {str(b['first'])[:10]} and {str(b['last'])[:10]}."],
        "required": [0, 2]}
    resp = (f"`Subscriber` is an annual member and `Customer` a 24-hour or 3-day pass user, as the publisher defines them. "
            f"Members made {sh['Subscriber']}% of 2017 trips, pass users {sh['Customer']}%. {int(b.trips_without_usertype):,} "
            f"trips have no user type, all between {str(b['first'])[:10]} and {str(b['last'])[:10]}; they cannot be assigned to either group.")
    return steps, gold, resp


@task("q1.birth_year_1969", "Q1.1",
      "In the 2018 trip records many riders have birth_year 1969. What does that value mean, and how should rider "
      "age be computed?", TEXT, depends_on=["q1.usertype"])
def birth_year(e):
    sql1 = """SELECT usertype, year(starttime) AS year,
  round(avg((birth_year IS NULL)::INT) * 100, 1) AS pct_missing,
  round(avg((birth_year = 1969)::INT) * 100, 1) AS pct_1969
FROM trips WHERE year(starttime) BETWEEN 2016 AND 2018 AND usertype IS NOT NULL
GROUP BY 1, 2 ORDER BY 1, 2"""
    sql2 = """SELECT date_trunc('month', starttime)::DATE AS month,
  round(avg((birth_year IS NULL)::INT) * 100, 1) AS pct_missing,
  round(avg((birth_year = 1969)::INT) * 100, 1) AS pct_1969
FROM trips WHERE usertype = 'Customer' AND starttime >= '2017-10-01' AND starttime < '2018-03-01'
GROUP BY 1 ORDER BY 1"""
    a = e.q(sql1)
    g = {(r.usertype, r.year): r for r in a.itertuples()}
    old = e.v("SELECT count(*) FROM trips WHERE birth_year < 1920")
    c17, c18 = g[("Customer", 2017)], g[("Customer", 2018)]
    m17, m18 = g[("Subscriber", 2017)], g[("Subscriber", 2018)]
    steps = [
        step("Recall what Customer and Subscriber mean", uses="q1.usertype"),
        step("Profile birth_year by user type and year: missing, and 1969", sql=sql1, result=tab(a)),
        step("Locate the change in time (pass users, by month)", sql=sql2, result=tab(e.q(sql2)),
             note="Missing values vanish and 1969 appears from one month to the next: a recording change, not new riders."),
        step("Look for other implausible values", sql="SELECT count(*) AS trips_born_before_1920 FROM trips WHERE birth_year < 1920",
             result={"trips_born_before_1920": int(old)}),
    ]
    gold = {"key_facts": [
        f"From January 2018 birth_year is never missing: missing years are filled with 1969 ({c17.pct_missing}% of pass "
        f"trips had no birth year in 2017; in 2018 none, and {c18.pct_1969}% show 1969).",
        f"For members the share of 1969 also rises ({m17.pct_1969}% in 2017, {m18.pct_1969}% in 2018), so 1969 must be "
        "treated as missing.",
        f"Compute age for members, without 1969 and implausible years ({old:,} trips have birth years before 1920)."],
        "required": [0, 1]}
    resp = (f"1969 is a default that replaced missing birth years from January 2018, not a real year. In 2017 "
            f"{c17.pct_missing}% of pass user trips had no birth year; in 2018 none has, and {c18.pct_1969}% show 1969. "
            f"Members show the same on a small scale ({m17.pct_1969}% to {m18.pct_1969}%). To compute rider age, use members, "
            f"treat 1969 as missing and drop implausible years ({old:,} trips have birth years before 1920).")
    return steps, gold, resp


@task("q1.weather_units", "Q1.2",
      "The ghcnd table holds NOAA GHCN-Daily values. In which units are TMAX, PRCP and SNOW recorded? Convert the "
      "Central Park values of January 23, 2016 to degrees Celsius, millimeters and centimeters.",
      {"type": "json", "schema": {"tmax_c": "float", "prcp_mm": "float", "snow_cm": "float"}},
      scoring={"metric": "fields", "fields": {"tmax_c": {"tol": 0.1}, "prcp_mm": {"tol": 0.1}, "snow_cm": {"tol": 0.1}}})
def weather_units(e):
    sql = """SELECT element, value FROM ghcnd
WHERE id = 'USW00094728' AND date = DATE '2016-01-23' AND element IN ('TMAX', 'TMIN', 'PRCP', 'SNOW', 'SNWD')
ORDER BY element"""
    a = e.q(sql)
    v = a.set_index("element").value
    sql2 = """SELECT "temp" AS mean_temp, "max", prcp FROM gsod WHERE wban = '94728' AND date = DATE '2016-01-23'"""
    gold = {"tmax_c": v["TMAX"] / 10, "prcp_mm": v["PRCP"] / 10, "snow_cm": v["SNOW"] / 10}
    steps = [
        step("Read the units in NOAA's documentation", doc=quote("ghcn", 0, 1, 3)),
        step("Get the raw Central Park values of the day", sql=sql, result=tab(a)),
        step("Convert: tenths of degrees C to C, tenths of mm to mm, mm of snow to cm", source="calculation", result=gold),
        step("Cross-check with the Global Summary of the Day (Fahrenheit, inches)", sql=sql2, result=tab(e.q(sql2)),
             note="GSOD's maximum in Fahrenheit matches the converted TMAX."),
    ]
    resp = (f"GHCN-Daily stores TMAX in tenths of a degree Celsius, PRCP in tenths of a millimeter and SNOW in "
            f"millimeters (NOAA readme). For Central Park on January 23, 2016: TMAX {int(v['TMAX'])} = {gold['tmax_c']} C, "
            f"PRCP {int(v['PRCP'])} = {gold['prcp_mm']} mm, SNOW {int(v['SNOW'])} mm = {gold['snow_cm']} cm.")
    return steps, gold, resp


@task("q1.gsod_vs_ghcnd", "Q1.2",
      "The gsod and ghcnd tables both give the daily precipitation of Central Park. Using NOAA's documentation of the "
      "Global Summary of the Day, explain why the two disagree on many days, and say which table to use for the "
      "precipitation of a calendar day in New York.", TEXT, depends_on=["q1.weather_units"])
def gsod_vs_ghcnd(e):
    sql = """WITH g AS (SELECT date, value / 254.0 AS prcp_in FROM ghcnd WHERE id = 'USW00094728' AND element = 'PRCP'),
     s AS (SELECT CAST(date AS DATE) AS date, prcp FROM gsod WHERE wban = '94728' AND year(date) BETWEEN 2016 AND 2017)
SELECT count(*) AS days,
  round(corr(s.prcp, g0.prcp_in), 3) AS corr_same_day,
  round(corr(s.prcp, g1.prcp_in), 3) AS corr_with_previous_day,
  round(avg((abs(s.prcp - g0.prcp_in) <= 0.01)::INT) * 100, 1) AS pct_equal_same_day,
  round(avg((abs(s.prcp - g1.prcp_in) <= 0.01)::INT) * 100, 1) AS pct_equal_previous_day
FROM s JOIN g g0 ON g0.date = s.date JOIN g g1 ON g1.date = s.date - 1"""
    sql2 = """SELECT g.date, g.value / 10.0 AS ghcnd_prcp_mm, round(s.prcp * 25.4, 1) AS gsod_prcp_mm
FROM ghcnd g JOIN gsod s ON CAST(s.date AS DATE) = g.date AND s.wban = '94728'
WHERE g.id = 'USW00094728' AND g.element = 'PRCP' AND g.date BETWEEN '2016-01-21' AND '2016-01-25' ORDER BY 1"""
    a = e.q(sql).iloc[0]
    steps = [
        step("Recall the GHCN-Daily units (PRCP in tenths of a millimeter, for the local day)", uses="q1.weather_units"),
        step("Read how GSOD defines its day and its precipitation", doc=quote("gsod", 0, 1)),
        step("Compare GSOD precipitation with GHCN-Daily of the same day and of the previous day (inches, 2016-2017)",
             sql=sql, result=tab(e.q(sql))),
        step("Look at the January 2016 blizzard in both tables (millimeters)", sql=sql2, result=tab(e.q(sql2)),
             note="GHCN-Daily has the storm on January 22-23; GSOD shows it a day later."),
    ]
    gold = {"key_facts": [
        "GSOD summarizes each day in Greenwich Mean Time (0000Z-2359Z), and its precipitation usually does not end at "
        "midnight: it may include the latter part of the previous day.",
        f"In the records GSOD precipitation of a day matches GHCN-Daily of the previous day (correlation "
        f"{a.corr_with_previous_day:.2f}, against {a.corr_same_day:.2f} for the same day).",
        "For the precipitation of a calendar day in New York use ghcnd (converted from tenths of a millimeter); a GSOD "
        "value of 0 also includes a trace."], "required": [0, 1, 2]}
    resp = ("The two tables use different days. GSOD summarizes each day in Greenwich Mean Time, and its precipitation "
            "comes from reports that usually do not end at midnight, so it can include the latter part of the previous "
            f"day (NOAA's GSOD readme). In the records GSOD precipitation of a day correlates {a.corr_with_previous_day:.2f} "
            f"with GHCN-Daily of the previous day and only {a.corr_same_day:.2f} with the same day: the January 2016 "
            "blizzard appears one day later in gsod. For the precipitation of a calendar day in New York use ghcnd "
            "(tenths of a millimeter). Note also that a GSOD value of 0 includes a trace.")
    return steps, gold, resp


# ====================================================================== Q2 Retrieve
SP_BQ202 = """WITH s AS (SELECT start_station_id FROM trips WHERE year(starttime) = 2018 AND month(starttime) <= 5 GROUP BY 1 ORDER BY count(*) DESC LIMIT 1)
SELECT dayofweek(starttime) AS day_of_week, hour(starttime) AS hour_of_day, count(*) AS trip_count
FROM trips JOIN s USING (start_station_id) WHERE year(starttime) = 2018 AND month(starttime) <= 5
GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 1"""


@task("q2.spider_bq202", "Q2.2",
      "For the station that had the highest number of trips starting there in January to May 2018, which numeric day of "
      "the week (Sunday = 0) and which hour of the day had the greatest number of trips, based on the start time of those trips?",
      {"type": "table", "columns": ["day_of_week", "hour_of_day", "trip_count"]},
      scoring={"metric": "table_match", "compare_columns": [0, 1]})
def bq202(e):
    sql1 = """SELECT start_station_id, any_value(start_station_name) AS name, count(*) AS trips_2018
FROM trips WHERE year(starttime) = 2018 AND month(starttime) <= 5 GROUP BY 1 ORDER BY 3 DESC, 1 LIMIT 3"""
    a = e.q(sql1)
    sid = int(a.start_station_id[0])
    sql2 = f"""SELECT dayofweek(starttime) AS day_of_week, hour(starttime) AS hour_of_day, count(*) AS trip_count
FROM trips WHERE year(starttime) = 2018 AND month(starttime) <= 5 AND start_station_id = {sid} GROUP BY 1, 2 ORDER BY 3 DESC, 1, 2 LIMIT 5"""
    b = e.q(sql2)
    final = e.q(SP_BQ202)
    m = spider_match(final, "bq202", [0, 1], SPIDER)
    g = final.iloc[0]
    gold = {"rows": [{"day_of_week": int(g.day_of_week), "hour_of_day": int(g.hour_of_day), "trip_count": int(g.trip_count)}]}
    steps = [
        step("Find the station with the most trips starting there in January-May 2018", sql=sql1, result=tab(a),
             note="Spider 2.0's copy of the table ends in May 2018, so the months are limited to match its question."),
        step("Count its 2018 trips by day of week and hour", sql=sql2, result=tab(b)),
        step("Compare with the Spider 2.0 gold result (bq202)", source="benchmark",
             result={"spider_instance": "bq202", "matches_spider_gold": m["match"], "gold_file": m.get("variant")},
             note="Question and gold come from Spider 2.0; this database holds the same records as its BigQuery table."),
    ]
    resp = (f"The busiest station of January-May 2018 is {a.name[0]} (station {sid}). Its busiest slot is day {gold['rows'][0]['day_of_week']} "
            f"({DOW[gold['rows'][0]['day_of_week']]}) at {gold['rows'][0]['hour_of_day']}:00, with "
            f"{gold['rows'][0]['trip_count']:,} trips.")
    return steps, gold, resp


@task("q2.spider_sf_bq358", "Q2.2",
      "Which bike trip in New York City on July 15, 2015 started and ended in ZIP code areas with the highest average "
      "temperature for that day, as recorded by the Central Park weather station (WBAN 94728)? If more than one trip "
      "meets these criteria, take the one that starts in the smallest ZIP code and ends in the largest ZIP code. "
      "Return the starting and ending ZIP codes of this trip.",
      {"type": "table", "columns": ["start_zip_code", "end_zip_code"]}, depends_on=["q1.weather_units"],
      scoring={"metric": "table_match", "compare_columns": [0, 1]})
def bq358(e):
    sql_w = """SELECT date, "temp" AS mean_temp_f FROM gsod WHERE wban = '94728' AND date = DATE '2015-07-15'"""
    sql_z = """SELECT zs.zip_code AS start_zip_code, ze.zip_code AS end_zip_code, count(*) AS trips
FROM trips t
JOIN zip_codes zs ON ST_Within(ST_Point(t.start_station_longitude, t.start_station_latitude), ST_GeomFromText(zs.zip_code_geom))
JOIN zip_codes ze ON ST_Within(ST_Point(t.end_station_longitude, t.end_station_latitude), ST_GeomFromText(ze.zip_code_geom))
WHERE CAST(t.starttime AS DATE) = DATE '2015-07-15'
GROUP BY 1, 2 ORDER BY 1 ASC, 2 DESC LIMIT 5"""
    sql = """SELECT zs.zip_code AS start_zip_code, ze.zip_code AS end_zip_code FROM trips t
JOIN zip_codes zs ON ST_Within(ST_Point(t.start_station_longitude, t.start_station_latitude), ST_GeomFromText(zs.zip_code_geom))
JOIN zip_codes ze ON ST_Within(ST_Point(t.end_station_longitude, t.end_station_latitude), ST_GeomFromText(ze.zip_code_geom))
JOIN gsod w ON w.date = CAST(t.starttime AS DATE) AND w.wban = '94728'
WHERE CAST(t.starttime AS DATE) = DATE '2015-07-15'
ORDER BY w."temp" DESC, zs.zip_code ASC, ze.zip_code DESC LIMIT 1"""
    final = e.q(sql)
    m = spider_match(final, "sf_bq358", [0, 1], SPIDER)
    gold = {"rows": [{"start_zip_code": final.start_zip_code[0], "end_zip_code": final.end_zip_code[0]}]}
    steps = [
        step("All trips of the day share one temperature: read it", sql=sql_w, result=tab(e.q(sql_w)), uses="q1.weather_units",
             note="Every trip of July 15 has the same temperature, so the tie-break decides."),
        step("Map trip ends to ZIP code areas (point in polygon) and apply the tie-break", sql=sql_z, result=tab(e.q(sql_z))),
        step("The answer and the Spider 2.0 gold result (sf_bq358)", sql=sql, result=tab(final), source="benchmark",
             note=f"Matches the Spider 2.0 gold: {m['match']}."),
    ]
    resp = (f"All trips on July 15, 2015 have the same Central Park temperature, so the tie-break decides: the trip that "
            f"starts in the smallest ZIP code and ends in the largest one goes from {gold['rows'][0]['start_zip_code']} "
            f"to {gold['rows'][0]['end_zip_code']}.")
    return steps, gold, resp


@task("q2.daily_jan2016", "Q2.1",
      "How many trips started on each day of January 2016?", {"type": "table", "columns": ["date", "trips"]},
      depends_on=["q1.trip_records"], scoring={"metric": "table_match", "compare_columns": [0, 1]})
def daily_jan2016(e):
    sql = """WITH d AS (SELECT range::DATE AS date FROM range(DATE '2016-01-01', DATE '2016-02-01', INTERVAL 1 DAY))
SELECT d.date, dayname(d.date) AS weekday, count(t.starttime) AS trips
FROM d LEFT JOIN trips t ON CAST(t.starttime AS DATE) = d.date
GROUP BY 1, 2 ORDER BY 1"""
    a = e.q(sql)
    zero = [str(x.date()) if hasattr(x, 'date') else str(x) for x in a[a.trips == 0].date]
    steps = [
        step("One row is one ride (count rows by start day)", uses="q1.trip_records"),
        step("Count trips per calendar day, keeping days without trips", sql=sql, result=tab(a),
             note=f"Days with no trips: {', '.join(zero)}. A plain GROUP BY on trips would drop them."),
    ]
    gold = {"rows": [{"date": str(r.date)[:10], "trips": int(r.trips)} for r in a.itertuples()], "zero_days": zero}
    resp = (f"January 2016 had {int(a.trips.sum()):,} trips; the daily counts are in the table. Note the days with no trips at "
            f"all: {', '.join(zero)}.")
    return steps, gold, resp


@task("q2.monthly_trips", "Q2.2",
      "How many trips were made each month from January 2014 to May 2018, in total and by user type?",
      {"type": "table", "columns": ["month", "trips", "members", "pass_users"]}, depends_on=["q1.usertype"],
      scoring={"metric": "table_match", "compare_columns": [0, 1, 2, 3]})
def monthly_trips(e):
    sql = """SELECT date_trunc('month', starttime)::DATE AS month, count(*) AS trips,
  count(*) FILTER (WHERE usertype = 'Subscriber') AS members,
  count(*) FILTER (WHERE usertype = 'Customer') AS pass_users
FROM trips WHERE starttime >= '2014-01-01' AND starttime < '2018-06-01' GROUP BY 1 ORDER BY 1"""
    a = e.q(sql)
    steps = [step("Group users as members and pass users", uses="q1.usertype"),
             step("Count trips per month and user type", sql=sql, result=tab(a))]
    gold = {"rows": tab(a)["rows"], "columns": list(a.columns)}
    peak = a.loc[a.trips.idxmax()]
    resp = (f"{len(a)} months, from {int(a.trips.min()):,} to {int(a.trips.max()):,} trips; the peak month is "
            f"{str(peak.month)[:7]} with {int(peak.trips):,} trips. Full table in the result.")
    return steps, gold, resp


@task("q2.lost_bag", "Q2.3",
      "A rider says they left a bag in the basket of bike 25536 after riding it from W 52 St & 5 Ave on the afternoon "
      "of September 27, 2016. Where did they return the bike, and when and from which station did that bike next leave?",
      {"type": "json", "schema": {"returned_at": "str", "returned_time": "str", "next_departure_time": "str",
                                  "next_departure_station": "str"}}, depends_on=["q1.trip_records"],
      scoring={"metric": "fields", "fields": {"returned_at": {"exact": True}, "next_departure_station": {"exact": True}}})
def lost_bag(e):
    sql1 = """SELECT starttime, stoptime, start_station_name, end_station_name
FROM trips WHERE bikeid = 25536 AND CAST(starttime AS DATE) = DATE '2016-09-27' ORDER BY starttime"""
    a = e.q(sql1)
    r = a[(a.start_station_name == "W 52 St & 5 Ave") & (pd.to_datetime(a.starttime).dt.hour >= 12)].iloc[0]
    sql2 = f"""SELECT starttime, start_station_name, end_station_name FROM trips
WHERE bikeid = 25536 AND starttime > TIMESTAMP '{r.stoptime}' ORDER BY starttime LIMIT 1"""
    b = e.q(sql2).iloc[0]
    gold = {"returned_at": r.end_station_name, "returned_time": str(r.stoptime)[:19],
            "next_departure_time": str(b.starttime)[:19], "next_departure_station": b.start_station_name,
            "next_trip_to": b.end_station_name}
    steps = [step("Each row is one ride of one bike, from a station to a station: follow the bike through its rides",
                  uses="q1.trip_records"),
             step("List the rides of bike 25536 on September 27, 2016", sql=sql1, result=tab(a),
                  note=f"The afternoon ride from W 52 St & 5 Ave ends at {r.end_station_name}."),
             step("Find the next ride of that bike after it was returned", sql=sql2, result=tab(e.q(sql2)))]
    mins = int((pd.Timestamp(b.starttime) - pd.Timestamp(r.stoptime)).total_seconds() // 60)
    resp = (f"The bike was returned at {r.end_station_name} at {gold['returned_time'][11:]}. It left again from "
            f"{b.start_station_name} {mins} minutes later, at {gold['next_departure_time'][11:]}, and was ridden to "
            f"{b.end_station_name}.")
    return steps, gold, resp


@task("q2.station_net_flow", "Q2.2",
      "On weekdays of May 2018 (without Memorial Day), how many bikes did each Manhattan station south of 40.745 N "
      "gain or lose per day on average (arrivals minus departures)? List the 15 largest gains and the 15 largest losses, "
      "rounded to whole bikes (on a tie, the lower station number first).",
      {"type": "table", "columns": ["station_id", "name", "bikes_per_day"]}, depends_on=["q1.trip_records"],
      scoring={"metric": "table_match", "compare_columns": [0, 2]})
def net_flow(e):
    sql = f"""SELECT s.station_id, s.name,
  (count(*) FILTER (WHERE t.end_station_id = s.station_id) - count(*) FILTER (WHERE t.start_station_id = s.station_id)) / 22.0 AS avg_net_gain
FROM stations s JOIN trips t ON s.station_id IN (t.start_station_id, t.end_station_id)
WHERE s.borough = 'Manhattan' AND s.latitude < 40.745 AND t.starttime >= '2018-05-01' AND t.starttime < '2018-06-01'
  AND dayofweek(t.starttime) BETWEEN 1 AND 5 AND CAST(t.starttime AS DATE) NOT IN (SELECT date FROM holidays) AND {PUBLIC}
GROUP BY 1, 2 ORDER BY 3 DESC"""
    net = e.q(sql)
    net["bikes"] = np.round(net.avg_net_gain).astype(int)
    net["size"] = net.bikes.abs()
    gain = net[net.bikes > 0].sort_values(["size", "station_id"], ascending=[False, True]).head(15)
    loss = net[net.bikes < 0].sort_values(["size", "station_id"], ascending=[False, True]).head(15)
    shown = pd.concat([gain.assign(role="gains"), loss.assign(role="loses")])
    shown["avg_net_gain"] = shown.avg_net_gain.round(2)
    nd = e.v("""SELECT count(DISTINCT CAST(starttime AS DATE)) FROM trips WHERE starttime >= '2018-05-01'
                AND starttime < '2018-06-01' AND dayofweek(starttime) BETWEEN 1 AND 5 AND CAST(starttime AS DATE) NOT IN (SELECT date FROM holidays)""")
    steps = [
        step("A ride leaves one bike fewer at its start station and one more at its end station", uses="q1.trip_records"),
        step("Count the weekdays of May 2018 without the holiday", source="db", result={"weekdays": int(nd)},
             sql="""SELECT count(DISTINCT CAST(starttime AS DATE)) AS weekdays FROM trips WHERE starttime >= '2018-05-01'
  AND starttime < '2018-06-01' AND dayofweek(starttime) BETWEEN 1 AND 5 AND CAST(starttime AS DATE) NOT IN (SELECT date FROM holidays)"""),
        step("Arrivals minus departures per station and day; service stations (depots, test points) excluded", sql=sql,
             result=tab(net[["station_id", "name", "avg_net_gain"]].round(2), limit=12)),
        step("Round to whole bikes and keep the 15 largest gains and losses (ties: lower station number)", source="calculation",
             result=tab(shown[["station_id", "name", "avg_net_gain", "role", "bikes"]])),
    ]
    gold = {"gains": {int(r.station_id): int(r.bikes) for r in gain.itertuples()},
            "losses": {int(r.station_id): int(r.size) for r in loss.itertuples()}}
    resp = (f"The largest gain is {gain.name.iloc[0]} (+{int(gain.bikes.iloc[0])} bikes per weekday) and the largest loss "
            f"{loss.name.iloc[0]} (-{int(loss['size'].iloc[0])}). The 15 gaining stations together gain "
            f"{int(gain.bikes.sum())} bikes a day; the 15 losing stations lose {int(loss['size'].sum())}.")
    return steps, gold, resp


@task("q2.trace_days", "Q2.4",
      "According to NOAA's GHCN-Daily readme, how is a trace of precipitation recorded? On how many days of 2016 and "
      "2017 did Central Park record a trace of rain, and what PRCP value do those days hold?",
      {"type": "json", "schema": {"flag": "str", "trace_days": "int", "prcp_value": "int"}},
      depends_on=["q1.weather_units"],
      scoring={"metric": "fields", "fields": {"flag": {"exact": True}, "trace_days": {"exact": True},
                                              "prcp_value": {"exact": True}}})
def trace_days(e):
    sql = """SELECT mflag, count(*) AS days, min(value) AS min_value, max(value) AS max_value FROM ghcnd
WHERE id = 'USW00094728' AND element = 'PRCP' AND year(date) BETWEEN 2016 AND 2017 GROUP BY 1 ORDER BY 1 NULLS FIRST"""
    sql2 = """SELECT count(*) FILTER (WHERE value = 0 AND mflag IS NULL) AS dry_days,
  count(*) FILTER (WHERE value = 0 AND mflag = 'T') AS trace_days
FROM ghcnd WHERE id = 'USW00094728' AND element = 'PRCP' AND year(date) BETWEEN 2016 AND 2017"""
    a, b = e.q(sql), e.q(sql2).iloc[0]
    t = a[a.mflag == "T"].iloc[0]
    gold = {"flag": "T", "trace_days": int(t.days), "prcp_value": int(t.max_value)}
    steps = [
        step("Find the measurement flags in the readme (one flag per value)", doc=quote("ghcn", 5, 6)),
        step("Count Central Park PRCP values of 2016-2017 by measurement flag (PRCP in tenths of a millimeter)", sql=sql,
             result=tab(a), uses="q1.weather_units"),
        step("Separate days without rain from days with a trace", sql=sql2, result=tab(e.q(sql2)),
             note="Both hold 0; only the flag tells them apart."),
    ]
    resp = (f"A trace is recorded with measurement flag T (\"trace of precipitation, snowfall, or snow depth\") and a "
            f"value of {gold['prcp_value']}. Central Park had {gold['trace_days']} trace days in 2016-2017, besides "
            f"{int(b.dry_days)} days with no precipitation at all. A filter on the value alone cannot tell them apart; "
            "use mflag.")
    return steps, gold, resp


@task("q2.gsod_missing", "Q2.4",
      "NOAA's Global Summary of the Day documentation uses codes for missing values. Which codes mean missing for the "
      "mean temperature, precipitation, snow depth and maximum gust, and how does the gsod table store them? On how "
      "many days of 2017 has Central Park no gust and no snow depth, and what does a missing snow depth usually mean?",
      {"type": "json", "schema": {"codes": "dict[str, float]", "days_without_gust": "int",
                                  "days_without_snow_depth": "int"}}, depends_on=["q1.weather_units"],
      scoring={"metric": "fields", "fields": {"days_without_gust": {"exact": True},
                                              "days_without_snow_depth": {"exact": True}}})
def gsod_missing(e):
    sql = """SELECT count(*) AS days, count(*) FILTER (WHERE "temp" IS NULL) AS no_temp,
  count(*) FILTER (WHERE prcp IS NULL) AS no_prcp, count(*) FILTER (WHERE gust IS NULL) AS no_gust,
  count(*) FILTER (WHERE sndp IS NULL) AS no_snow_depth,
  count(*) FILTER (WHERE sndp IS NULL AND month(date) BETWEEN 4 AND 10) AS no_snow_depth_apr_oct
FROM gsod WHERE wban = '94728' AND year(date) = 2017"""
    sql2 = f"""WITH w AS {WX.format(t='ghcnd')}
SELECT (s.sndp IS NULL) AS gsod_snow_depth_missing, count(*) AS days,
  count(*) FILTER (WHERE w.snwd > 0) AS ghcnd_snow_on_ground
FROM gsod s JOIN w ON w.date = CAST(s.date AS DATE) WHERE s.wban = '94728' AND year(s.date) = 2017 GROUP BY 1 ORDER BY 1"""
    a = e.q(sql).iloc[0]
    b = e.q(sql2)
    codes = {"TEMP": 9999.9, "PRCP": 99.99, "SNDP": 999.9, "GUST": 999.9}
    gold = {"codes": codes, "days_without_gust": int(a.no_gust), "days_without_snow_depth": int(a.no_snow_depth)}
    miss = b[b.gsod_snow_depth_missing].iloc[0]
    steps = [
        step("Find the missing-value codes of each field in the GSOD readme", doc=quote("gsod", 2, 3, 4, 5)),
        step("The table stores the codes as NULL (the build turns values at or above the code into NULL); count them "
             "for Central Park in 2017", sql=sql, result=tab(e.q(sql))),
        step("Check missing snow depth against GHCN-Daily snow depth", sql=sql2, result=tab(b), uses="q1.weather_units"),
    ]
    resp = (f"GSOD codes missing values with nines: TEMP 9999.9, PRCP 99.99, SNDP 999.9 and GUST 999.9; the gsod table "
            f"stores them as NULL. In 2017 Central Park has no gust on {gold['days_without_gust']} days and no snow depth "
            f"on {gold['days_without_snow_depth']} days. A missing snow depth usually means no snow on the ground: the "
            "readme says most stations do not report 0, and GHCN-Daily shows snow on the ground on only "
            f"{int(miss.ghcnd_snow_on_ground)} of those days.")
    return steps, gold, resp


@task("q2.closed_stations", "Q2.1",
      "Which public stations in Manhattan had trips in 2015 but none in 2018? List them with their last trip date.",
      {"type": "table", "columns": ["station_id", "name", "last_trip"]}, depends_on=["q1.trip_records"],
      scoring={"metric": "table_match", "compare_columns": [0]})
def closed_stations(e):
    sql = f"""SELECT station_id, name, last_trip FROM stations
WHERE borough = 'Manhattan' AND first_trip <= DATE '2015-12-31' AND last_trip >= DATE '2015-01-01'
  AND last_trip < DATE '2018-01-01' AND NOT {SERVICE.format(c='name')}
ORDER BY station_id"""
    sql2 = f"""SELECT s.station_id, s.name AS old_name, n.station_id AS later_station_id, n.first_seen AS later_first_trip
FROM ({sql}) s JOIN station_names n ON n.name = regexp_replace(s.name, ' [NS]$', '') AND n.station_id <> s.station_id
  AND n.first_seen >= s.last_trip - 31
ORDER BY 1"""
    a, b = e.q(sql), e.q(sql2)
    by_year = a.groupby(pd.to_datetime(a.last_trip).dt.year).size()
    gold = {"rows": [{"station_id": int(r.station_id), "name": r.name, "last_trip": str(r.last_trip)[:10]} for r in a.itertuples()]}
    steps = [
        step("A station appears in the trips table when a ride starts or ends there; service stations have no public rides",
             uses="q1.trip_records"),
        step("Public Manhattan stations with trips in 2015 whose last trip is before 2018 (service stations recognized "
             "by name)", sql=sql, result=tab(a)),
        step("Some were replaced by a station with a new number at the same place", sql=sql2, result=tab(b),
             note="The question asks for the station records, so these stay in the list."),
    ]
    resp = (f"{len(a)} public Manhattan stations had trips in 2015 and none in 2018 "
            f"({', '.join(f'{int(n)} last used in {y}' for y, n in by_year.items())}); the list is in the result. "
            f"{b.station_id.nunique()} of them were replaced by a station with a new number under the same name, so the "
            "place still has a station.")
    return steps, gold, resp


@task("q2.moved_station", "Q2.3",
      "How many trips started at the station E 13 St & Avenue A in 2018?",
      {"type": "json", "schema": {"trips_2018": "int", "station_ids": "list[int]"}}, depends_on=["q1.trip_records"],
      scoring={"metric": "fields", "fields": {"trips_2018": {"exact": True}, "station_ids": {"exact": True}}})
def moved_station(e):
    sql = """SELECT station_id, name, first_seen, last_seen, n_trip_ends FROM station_names
WHERE name ILIKE '%E 13 St & Avenue A%' ORDER BY first_seen"""
    sql2 = """SELECT start_station_id, start_station_name, count(*) AS trips, min(starttime)::DATE AS first, max(starttime)::DATE AS last
FROM trips WHERE year(starttime) = 2018 AND start_station_name ILIKE '%E 13 St & Avenue A%' GROUP BY 1, 2 ORDER BY 4"""
    sql3 = """SELECT name, latitude, longitude FROM stations WHERE station_id IN (433, 3711) ORDER BY station_id"""
    a, b = e.q(sql), e.q(sql2)
    p = e.q(sql3)
    dist = int(hav_km(p.iloc[0][["latitude", "longitude"]].values, p.iloc[1][["latitude", "longitude"]].values) * 1000)
    exact = int(b[b.start_station_name == "E 13 St & Avenue A"].trips.sum())
    gold = {"trips_2018": int(b.trips.sum()), "station_ids": sorted(int(x) for x in b.start_station_id.unique()),
            "trips_with_exact_name": exact}
    steps = [
        step("Search the station name history for the name (any spelling)", sql=sql, result=tab(a),
             note="The name belongs to two station numbers: 433 until it was renamed \"old - ...\" in October 2018, "
                  "and 3711 from October 16, 2018."),
        step("Count 2018 departures (rows of trips) under every variant of the name", sql=sql2, result=tab(b),
             uses="q1.trip_records"),
        step("Check where the two stations are", sql=sql3, result=tab(p),
             note=f"The new station is {dist} m from the old one: it replaced it at the same corner."),
    ]
    resp = (f"{gold['trips_2018']:,} trips. The station was replaced in October 2018: station 433 carried the name until "
            "September 30, was then called \"old - E 13 St & Avenue A\" for two weeks, and the new station 3711, "
            f"{dist} m away, took the name from October 16. Counting only one station number or only the exact name "
            f"({exact:,} trips) misses part of the year.")
    return steps, gold, resp


# ====================================================================== Q3 Describe
def monthly(e, start="2014-01-01", end="2017-12-01"):
    a = e.q("""SELECT date_trunc('month', starttime)::DATE AS month, count(*) AS trips FROM trips
               WHERE starttime >= '2014-01-01' GROUP BY 1 ORDER BY 1""")
    s = pd.Series(a.trips.values.astype(float), index=pd.to_datetime(a.month))
    return s[start:end]


@task("q3.growth_trend", "Q3.1",
      "How did ridership develop from 2014 to 2017? Describe the trend beyond the seasonal pattern.", TEXT,
      depends_on=["q2.monthly_trips"])
def growth_trend(e):
    s = monthly(e)
    yr = s.groupby(s.index.year).sum()
    g = (yr / yr.shift(1) - 1).dropna()
    mk = S.mann_kendall(S.deseasonalize_monthly(s).values)
    sql = """SELECT date_trunc('month', first_trip)::DATE AS month, count(*) AS new_stations
FROM stations WHERE first_trip >= '2014-01-01' AND first_trip < '2018-01-01'
GROUP BY 1 HAVING count(*) >= 15 ORDER BY 1"""
    steps = [
        step("Take the monthly trip counts", uses="q2.monthly_trips"),
        step("Sum by year and compute the growth", source="calculation",
             result={"columns": ["year", "trips", "growth"],
                     "rows": [[int(y), int(n), None if y == 2014 else round(float(g[y]), 3)] for y, n in yr.items()]}),
        step("Remove the month-of-year pattern and test for a trend (Mann-Kendall)", source="calculation",
             result={"tau": round(mk["tau"], 3), "p_value": float(f"{mk['p']:.2g}")}),
        step("Relate the growth to months with many new stations", sql=sql, result=tab(e.q(sql))),
    ]
    gold = {"key_facts": [
        f"Ridership grew every year: {yr[2014] / 1e6:.1f} million trips in 2014, {yr[2015] / 1e6:.1f} million in 2015, "
        f"{yr[2016] / 1e6:.1f} million in 2016 and {yr[2017] / 1e6:.1f} million in 2017 "
        f"({', '.join(f'{x * 100:+.0f}%' for x in g.values)}).",
        f"Without the seasons the monthly series has a strong upward trend (Mann-Kendall tau {mk['tau']:.2f}, p < 0.001).",
        "Growth came in steps that follow the expansions of the service area (summer 2015, summer 2016, fall 2017)."],
        "required": [0, 1]}
    resp = " ".join(gold["key_facts"])
    return steps, gold, resp


@task("q3.daily_profile", "Q3.2",
      "Describe the daily cycle of departures on weekdays and on weekends in 2017. Do members and pass users follow "
      "the same pattern?", TEXT, depends_on=["q1.usertype"])
def daily_profile(e):
    sql = """SELECT usertype, dayofweek(starttime) IN (0, 6) AS weekend, hour(starttime) AS hour, count(*) AS trips
FROM trips WHERE year(starttime) = 2017 AND usertype IS NOT NULL GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"""
    h = e.q(sql)
    mw = h[(h.usertype == "Subscriber") & ~h.weekend].set_index("hour").trips
    am, pm = int(mw.loc[5:11].idxmax()), int(mw.loc[14:21].idxmax())
    we = h[h.weekend].groupby("hour").trips.sum()
    pw = h[(h.usertype == "Customer") & ~h.weekend].set_index("hour").trips
    peaks = pd.DataFrame({"series": ["members, weekdays (morning)", "members, weekdays (evening)", "all, weekends", "pass users, weekdays"],
                          "peak_hour": [am, pm, int(we.idxmax()), int(pw.idxmax())]})
    steps = [step("Separate members and pass users", uses="q1.usertype"),
             step("Count 2017 departures by user type, weekday or weekend, and hour", sql=sql, result=tab(h, limit=24)),
             step("Find the peak hours of each profile", source="calculation", result=tab(peaks))]
    gold = {"key_facts": [
        f"Members on weekdays have two commute peaks, {am}:00 and {pm}:00 (evening {mw[pm] / mw[am]:.2f} times the morning).",
        f"Weekends have one broad early-afternoon peak (highest hour {int(we.idxmax())}:00).",
        f"Pass users have no commute peaks: on weekdays their departures rise to a peak at {int(pw.idxmax())}:00."],
        "required": [0, 1, 2]}
    return steps, gold, " ".join(gold["key_facts"])


@task("q3.duration_distribution", "Q3.3",
      "Describe the distribution of ride times in 2017, overall and for members and pass users.", TEXT,
      depends_on=["q1.trip_records", "q1.usertype"])
def duration(e):
    sql = """SELECT coalesce(usertype, 'all') AS who,
  quantile_cont(tripduration / 60.0, 0.05) AS p05_min, median(tripduration / 60.0) AS median_min,
  quantile_cont(tripduration / 60.0, 0.95) AS p95_min, quantile_cont(tripduration / 60.0, 0.99) AS p99_min, count(*) AS rides
FROM trips WHERE year(starttime) = 2017 GROUP BY GROUPING SETS ((usertype), ()) HAVING usertype IS NOT NULL OR GROUPING(usertype) = 1
ORDER BY 1"""
    a = e.q(sql).set_index("who")
    lg = e.q("SELECT ln(tripduration) AS x FROM trips WHERE year(starttime) = 2017 AND tripduration <= 10800 AND hash(bikeid, starttime) % 50 = 0").x
    sk = float(stats.skew(lg))
    steps = [step("Use seconds-based tripduration; long outliers stay but quantiles are robust to them", uses="q1.trip_records"),
             step("Quantiles of ride time in minutes, overall and for members and pass users", sql=sql,
                  result=tab(a.reset_index()), uses="q1.usertype"),
             step("Check the shape on a log scale (2% sample, rides up to 3 hours)", source="calculation",
                  result={"skewness_of_log_ride_time": round(sk, 3)})]
    al, c, m = a.loc["all"], a.loc["Customer"], a.loc["Subscriber"]
    gold = {"key_facts": [
        f"Ride times are strongly right-skewed: median {al.median_min:.1f} minutes, 5% under {al.p05_min:.1f} and 5% over "
        f"{al.p95_min:.0f} minutes, 1% over {al.p99_min:.0f}.",
        f"Pass users ride much longer than members (median {c.median_min:.1f} against {m.median_min:.1f} minutes).",
        f"On a log scale the distribution is close to symmetric (skewness {sk:.2f}): roughly log-normal."], "required": [0, 1]}
    return steps, gold, " ".join(gold["key_facts"])


@task("q3.temperature", "Q3.4",
      "How does daily ridership relate to temperature (daily maximum in Central Park)? Use dry weekdays of 2016 and 2017.",
      TEXT, depends_on=["q1.weather_units"])
def temperature(e):
    sql = f"""WITH d AS (SELECT CAST(starttime AS DATE) AS dt, count(*) AS n FROM trips
                WHERE starttime >= '2016-01-01' AND starttime < '2018-01-01' AND {PUBLIC} GROUP BY 1),
     w AS {WX.format(t='ghcnd')}
SELECT floor(w.tmax / 3) * 3 AS tmax_band_c, count(*) AS days, avg(d.n) AS avg_trips
FROM d JOIN w ON w.date = d.dt
WHERE dayofweek(d.dt) BETWEEN 1 AND 5 AND d.dt NOT IN (SELECT date FROM holidays) AND w.snwd = 0 AND w.prcp < 2
GROUP BY 1 HAVING count(*) >= 5 ORDER BY 1"""
    a = e.q(sql)
    a["avg_trips"] = a.avg_trips.round()
    p = e.q(sql.replace("SELECT floor(w.tmax / 3) * 3 AS tmax_band_c, count(*) AS days, avg(d.n) AS avg_trips",
                        "SELECT d.n, w.tmax").replace("GROUP BY 1 HAVING count(*) >= 5 ORDER BY 1", "ORDER BY 1, 2"))
    sp = stats.spearmanr(p.tmax[p.tmax < 25], p.n[p.tmax < 25]).correlation
    peak = a.loc[a.avg_trips.idxmax()]
    hot, cold = a.avg_trips[a.tmax_band_c >= 30].mean(), a.avg_trips[a.tmax_band_c < 3].mean()
    steps = [step("Convert TMAX from tenths of a degree to degrees Celsius", uses="q1.weather_units"),
             step("Daily trips on dry weekdays (no snow cover, under 2 mm of rain, no holiday), averaged per 3 C band",
                  sql=sql, result=tab(a)),
             step("Measure the rise below 25 C and compare the ends", source="calculation",
                  result={"spearman_below_25C": round(sp, 3), "peak_band_c": int(peak.tmax_band_c),
                          "avg_of_bands_30C_and_above": int(hot), "avg_of_bands_below_3C": int(cold)})]
    gold = {"key_facts": [
        f"Ridership rises steadily with temperature up to the mid twenties (Spearman {sp:.2f} below 25 C).",
        f"It flattens and turns down at the top: dry weekdays peak at {int(peak.tmax_band_c)}-{int(peak.tmax_band_c) + 3} C "
        f"({int(peak.avg_trips):,} trips) and are lower at 30 C and above ({hot:,.0f}).",
        f"Cold dry weekdays below 3 C average {cold:,.0f} trips."], "required": [0, 1]}
    return steps, gold, " ".join(gold["key_facts"])


@task("q3.borough_share", "Q3.1",
      "How did the share of trips that start outside Manhattan develop from 2014 to 2018? Describe the trend for "
      "Brooklyn and Queens.", TEXT, depends_on=["q2.monthly_trips"])
def borough_share(e):
    sql = f"""SELECT year(t.starttime) AS year, count(*) AS trips,
  round(avg((s.borough = 'Brooklyn')::INT) * 100, 2) AS pct_brooklyn,
  round(avg((s.borough = 'Queens')::INT) * 100, 2) AS pct_queens,
  round(avg((s.borough = 'Manhattan')::INT) * 100, 2) AS pct_manhattan
FROM trips t JOIN stations s ON s.station_id = t.start_station_id
WHERE t.starttime >= '2014-01-01' AND {PUBLIC} GROUP BY 1 ORDER BY 1"""
    sql_m = f"""SELECT date_trunc('month', t.starttime)::DATE AS month, avg((s.borough <> 'Manhattan')::INT) AS outside
FROM trips t JOIN stations s ON s.station_id = t.start_station_id
WHERE t.starttime >= '2014-01-01' AND {PUBLIC} GROUP BY 1 ORDER BY 1"""
    a = e.q(sql).set_index("year")
    m = e.q(sql_m)
    sm = pd.Series(m.outside.values, index=pd.to_datetime(m.month))
    mk = S.mann_kendall(S.deseasonalize_monthly(sm).values)
    sql_s = """SELECT borough, year(first_trip) AS year_opened, count(*) AS stations FROM stations
WHERE borough IN ('Brooklyn', 'Queens') AND first_trip >= '2014-01-01' GROUP BY 1, 2 ORDER BY 1, 2"""
    steps = [
        step("Monthly trips are the base; here they are split by the borough of the start station", uses="q2.monthly_trips"),
        step("Share of trips by borough of the start station, per year (service stations excluded)", sql=sql,
             result=tab(a.reset_index())),
        step("Test the monthly share outside Manhattan for a trend after removing the month-of-year pattern",
             sql=sql_m, result={"months": len(sm), "tau": round(mk["tau"], 3), "p_value": float(f"{mk['p']:.2g}")}),
        step("Stations opened per year in Brooklyn and Queens", sql=sql_s, result=tab(e.q(sql_s))),
    ]
    b, q_ = a.pct_brooklyn, a.pct_queens
    gold = {"key_facts": [
        f"The share of trips starting outside Manhattan rose every year, from {100 - a.pct_manhattan[2014]:.1f}% in 2014 to "
        f"{100 - a.pct_manhattan[2018]:.1f}% in 2018 (Mann-Kendall on the deseasonalized monthly share: tau {mk['tau']:.2f}).",
        f"Brooklyn's share grew from {b[2014]:.1f}% to {b[2018]:.1f}%, fastest in 2016 and 2017.",
        f"Queens starts in 2015 and stays small, but its share jumped in 2018 ({q_[2017]:.1f}% to {q_[2018]:.1f}%).",
        "The rises follow the stations opened in the two boroughs."], "required": [0, 1, 2]}
    return steps, gold, " ".join(gold["key_facts"])


@task("q3.weekly_cycle", "Q3.2",
      "Describe the weekly cycle of trips in 2017 for members and for pass users: which days are busiest, and how much "
      "do weekdays and weekends differ?", TEXT, depends_on=["q1.usertype"])
def weekly_cycle(e):
    sql = """WITH d AS (SELECT CAST(starttime AS DATE) AS day, usertype, count(*) AS trips FROM trips
           WHERE year(starttime) = 2017 AND usertype IS NOT NULL GROUP BY 1, 2)
SELECT usertype, dayofweek(day) AS dow, dayname(day) AS weekday, count(*) AS days, round(avg(trips)) AS avg_trips
FROM d GROUP BY 1, 2, 3 ORDER BY 1, 2"""
    a = e.q(sql)
    m, c = a[a.usertype == "Subscriber"].set_index("weekday"), a[a.usertype == "Customer"].set_index("weekday")
    wd = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    we = ["Saturday", "Sunday"]
    m_ratio = m.avg_trips[we].mean() / m.avg_trips[wd].mean()
    c_ratio = c.avg_trips[we].mean() / c.avg_trips[wd].mean()
    steps = [step("Separate members and pass users", uses="q1.usertype"),
             step("Average trips per day of the week in 2017, by user type (days with no trips are not in the data)",
                  sql=sql, result=tab(a)),
             step("Compare weekend and weekday averages", source="calculation",
                  result={"members_weekend_vs_weekday": round(m_ratio, 3), "pass_weekend_vs_weekday": round(c_ratio, 3),
                          "members_busiest": m.avg_trips.idxmax(), "pass_busiest": c.avg_trips.idxmax()})]
    gold = {"key_facts": [
        f"Members ride mostly on weekdays: their busiest day is {m.avg_trips.idxmax()}, and a weekend day has "
        f"{(1 - m_ratio) * 100:.0f}% fewer member trips than a weekday.",
        f"Pass users ride mostly on weekends: their busiest day is {c.avg_trips.idxmax()}, and a weekend day has "
        f"{c_ratio:.1f} times the pass trips of a weekday.",
        f"Members make most trips on every day of the week ({int(m.avg_trips.min()):,} or more per day against at most "
        f"{int(c.avg_trips.max()):,} pass trips), so the total follows the members' weekday pattern."],
        "required": [0, 1]}
    return steps, gold, " ".join(gold["key_facts"])


DIST = hav_sql("start_station_latitude", "start_station_longitude", "end_station_latitude", "end_station_longitude")


@task("q3.trip_distance", "Q3.3",
      "Describe the distribution of trip distances in 2017 (straight-line distance between the start and the end "
      "station). How many trips return to their start station, and how do members and pass users differ?", TEXT,
      depends_on=["q1.usertype"])
def trip_distance(e):
    sql = f"""SELECT coalesce(usertype, 'all') AS who, count(*) AS trips,
  round(avg((start_station_id = end_station_id)::INT) * 100, 2) AS pct_round_trips,
  round(quantile_cont({DIST} / 1000, 0.25) FILTER (WHERE start_station_id <> end_station_id), 2) AS p25_km,
  round(median({DIST} / 1000) FILTER (WHERE start_station_id <> end_station_id), 2) AS median_km,
  round(quantile_cont({DIST} / 1000, 0.9) FILTER (WHERE start_station_id <> end_station_id), 2) AS p90_km,
  round(quantile_cont({DIST} / 1000, 0.99) FILTER (WHERE start_station_id <> end_station_id), 2) AS p99_km
FROM trips WHERE year(starttime) = 2017 AND {PUBLIC}
GROUP BY GROUPING SETS ((usertype), ()) HAVING usertype IS NOT NULL OR GROUPING(usertype) = 1 ORDER BY 1"""
    sql_h = f"""SELECT floor({DIST} / 500) * 0.5 AS km_from, count(*) AS trips FROM trips
WHERE year(starttime) = 2017 AND start_station_id <> end_station_id AND {PUBLIC} GROUP BY 1 HAVING km_from < 8 ORDER BY 1"""
    a = e.q(sql).set_index("who")
    h = e.q(sql_h)
    mode = float(h.km_from[h.trips.idxmax()])
    steps = [step("Separate members and pass users", uses="q1.usertype"),
             step("Share of round trips and quantiles of the distance of the other trips, overall and by user type",
                  sql=sql, result=tab(a.reset_index())),
             step("Histogram in half-kilometer bins (trips between different stations)", sql=sql_h, result=tab(h))]
    al, c, m = a.loc["all"], a.loc["Customer"], a.loc["Subscriber"]
    gold = {"key_facts": [
        f"Trips are short and right-skewed: between different stations the median distance is {al.median_km} km, the most "
        f"common band {mode:g}-{mode + 0.5:g} km, 10% are longer than {al.p90_km} km and 1% longer than {al.p99_km} km.",
        f"{al.pct_round_trips}% of trips end at their start station (distance 0): {c.pct_round_trips}% of pass user "
        f"trips but only {m.pct_round_trips}% of member trips.",
        f"Pass users ride farther between stations (median {c.median_km} against {m.median_km} km), but far less "
        "farther than their much longer ride times would suggest."],
        "required": [0, 1]}
    return steps, gold, " ".join(gold["key_facts"])


@task("q3.speed_distance", "Q3.4",
      "For members in 2017, how is ride time related to the straight-line distance of the trip? What is the typical "
      "speed, and are longer trips faster?", TEXT,
      depends_on=["q1.trip_records", "q3.trip_distance", "q3.duration_distribution"])
def speed_distance(e):
    sql = f"""SELECT floor({DIST} / 1000) AS km_from, count(*) AS trips,
  round(median(tripduration / 60.0), 1) AS median_min,
  round(median(({DIST} / 1000) / (tripduration / 3600.0)), 1) AS median_kmh
FROM trips WHERE year(starttime) = 2017 AND usertype = 'Subscriber' AND start_station_id <> end_station_id
  AND tripduration <= 10800 AND {PUBLIC} GROUP BY 1 HAVING km_from < 8 ORDER BY 1"""
    sql_r = f"""SELECT {DIST} / 1000 AS km, tripduration / 60.0 AS minutes FROM trips
WHERE year(starttime) = 2017 AND usertype = 'Subscriber' AND start_station_id <> end_station_id AND tripduration <= 10800
  AND {PUBLIC} AND hash(bikeid, starttime) % 100 = 0"""
    a = e.q(sql)
    r = e.q(sql_r)
    rho = float(stats.spearmanr(r.km, r.minutes).correlation)
    fit = np.polyfit(r.km[r.km < 6], r.minutes[r.km < 6], 1)
    steps = [step("tripduration is the ride time in seconds; rides of more than 3 hours are bikes not docked, so leave "
                  "them out", uses="q1.trip_records"),
             step("Use trips between different stations; ride time is right-skewed, so use medians",
                  uses="q3.duration_distribution"),
             step("Median ride time and speed per kilometer of straight-line distance (members, rides up to 3 hours)",
                  sql=sql, result=tab(a), uses="q3.trip_distance"),
             step("Rank correlation and a straight line on a 1% sample (trips under 6 km)", sql=sql_r, source="calculation",
                  result={"sample": len(r), "spearman": round(rho, 3), "minutes_per_km": round(float(fit[0]), 2),
                          "intercept_min": round(float(fit[1]), 2)})]
    gold = {"key_facts": [
        f"Ride time rises almost linearly with distance (Spearman {rho:.2f}): about {fit[0]:.1f} minutes per kilometer of "
        f"straight-line distance plus {fit[1]:.1f} minutes.",
        f"Typical straight-line speed is about {a.median_kmh.iloc[1]:g}-{a.median_kmh.iloc[2]:g} km/h for trips of 1-3 km.",
        f"Longer trips are faster: median speed rises from {a.median_kmh.iloc[0]:g} km/h under 1 km to "
        f"{a.median_kmh.iloc[-1]:g} km/h at {int(a.km_from.iloc[-1])}-{int(a.km_from.iloc[-1]) + 1} km, because the fixed "
        "time to undock, dock and wait at lights weighs less."],
        "required": [0, 2]}
    return steps, gold, " ".join(gold["key_facts"])


@task("q3.age_distribution", "Q3.3",
      "Describe the age distribution of members in 2017 (age = trip year minus birth year): typical age, spread and "
      "tail, and how it differs between women and men. Count each trip once.", TEXT,
      depends_on=["q1.birth_year_1969", "q1.usertype"])
def age_distribution(e):
    clean = "usertype = 'Subscriber' AND birth_year IS NOT NULL AND birth_year <> 1969 AND birth_year >= 1920"
    sql = f"""SELECT coalesce(gender, 'all') AS gender, count(*) AS trips,
  quantile_cont(2017 - birth_year, 0.1) AS p10, quantile_cont(2017 - birth_year, 0.25) AS p25,
  median(2017 - birth_year) AS median, quantile_cont(2017 - birth_year, 0.75) AS p75,
  quantile_cont(2017 - birth_year, 0.9) AS p90, round(avg(2017 - birth_year), 1) AS mean
FROM trips WHERE year(starttime) = 2017 AND {clean} AND gender IN ('female', 'male')
GROUP BY GROUPING SETS ((gender), ()) ORDER BY 1"""
    sql_h = f"""SELECT floor((2017 - birth_year) / 5) * 5 AS age_from, count(*) AS trips,
  round(count(*) * 100.0 / sum(count(*)) OVER (), 1) AS pct
FROM trips WHERE year(starttime) = 2017 AND {clean} GROUP BY 1 ORDER BY 1"""
    a = e.q(sql).set_index("gender")
    h = e.q(sql_h)
    old = float(h[h.age_from >= 60].pct.sum())
    steps = [
        step("Members only, and without the placeholder 1969 and implausible birth years", uses="q1.birth_year_1969"),
        step("Members are annual subscribers (usertype Subscriber); pass users often give no birth year",
             uses="q1.usertype"),
        step("Quantiles of member age by gender (2017 trips)", sql=sql, result=tab(a.reset_index())),
        step("Trips by 5-year age band", sql=sql_h, result=tab(h)),
    ]
    al, f, m = a.loc["all"], a.loc["female"], a.loc["male"]
    peak = h.loc[h.trips.idxmax()]
    gold = {"key_facts": [
        f"Member trips come mostly from riders aged about {al.p25:.0f}-{al.p75:.0f} (middle half); the median age is "
        f"{al['median']:.0f} and the mean {al['mean']}.",
        f"The distribution is right-skewed: the busiest band is {int(peak.age_from)}-{int(peak.age_from) + 4} "
        f"({peak.pct}% of trips), 10% of trips are by riders over {al.p90:.0f} and {old:.1f}% by riders of 60 or more.",
        f"Women make {f.trips / al.trips * 100:.0f}% of member trips with a known gender; their median age "
        f"({f['median']:.0f}) is close to men's ({m['median']:.0f})."], "required": [0, 1, 2]}
    return steps, gold, " ".join(gold["key_facts"])


# ====================================================================== Q4 Discover
@task("q4.january_2016", "Q4.1",
      "Were there any unusual days in daily ridership in January 2016? For each, say what happened.",
      {"type": "json", "schema": {"anomalies": [{"dates": "list[date]", "explanation": "str"}]}},
      depends_on=["q2.daily_jan2016", "q1.weather_units", "q3.weekly_cycle"], scoring={"metric": "event_f1", "date_tol_days": 1})
def january_2016(e):
    flags = [f for f in scan(e.con, pd.Timestamp("2016-01-01"), pd.Timestamp("2016-01-31")) if f["series"] == "total"]
    det = pd.DataFrame(flags)[["date", "count", "expected", "z", "explained_by"]]
    sql = f"""WITH w AS {WX.format(t='ghcnd')}
SELECT date, tmax, snow AS snow_cm, snwd AS snow_depth_cm FROM w WHERE date BETWEEN '2016-01-20' AND '2016-01-28' ORDER BY 1"""
    w = e.q(sql)
    zero = [str(d) for d in det[det["count"] == 0].date]
    gold = {"anomalies": [{"dates": zero, "explanation": "No trips at all: the system was closed for the January 22-24 "
                           f"blizzard ({w.snow_cm.max():.0f} cm of snow in Central Park on January 23) and the clean-up."}]}
    steps = [step("Take the daily counts; four days have no trips", uses="q2.daily_jan2016"),
             step("Weekdays have more trips than weekend days, so the baseline is per day of the week",
                  uses="q3.weekly_cycle"),
             step("Compare each day with a weekday-and-weather baseline (robust z-scores)", source="reference method",
                  result=tab(det), note="Days far below the baseline are flagged; each is checked against the weather."),
             step("Check the weather around the flagged days (converted units)", sql=sql, result=tab(w), uses="q1.weather_units")]
    resp = (f"Yes: {', '.join(zero)} had no trips at all. A blizzard hit on January 22-24 ({w.snow_cm.max():.0f} cm of snow "
            "in Central Park on January 23, with deep snow cover afterwards), and the system was closed during the storm "
            "and the clean-up. These are real zero-ridership days, not missing data.")
    return steps, gold, resp


@task("q4.birth_year_shift", "Q4.2",
      "Did the distribution of riders' birth years in the trip records change between the second half of 2017 and "
      "2018? If so, when, and is it a change in who rides or in how the data are recorded?", TEXT,
      depends_on=["q1.birth_year_1969", "q3.age_distribution", "q2.monthly_trips"])
def birth_year_shift(e):
    sql = """SELECT date_trunc('month', starttime)::DATE AS month, round(avg((birth_year = 1969)::INT) * 100, 2) AS pct_1969,
  round(avg((birth_year IS NULL)::INT) * 100, 2) AS pct_missing, round(avg((usertype = 'Customer')::INT) * 100, 2) AS pct_pass
FROM trips WHERE starttime >= '2017-07-01' GROUP BY 1 ORDER BY 1"""
    a = e.q(sql)
    d17, j18 = a[a.month.astype(str).str.startswith("2017-12")].iloc[0], a[a.month.astype(str).str.startswith("2018-01")].iloc[0]
    tb = pd.DataFrame({"y": [d17.pct_1969, j18.pct_1969]})
    steps = [step("Recall that 1969 is a placeholder from 2018", uses="q1.birth_year_1969"),
             step("The member age distribution of 2017 is smooth, without a peak at 48-49", uses="q3.age_distribution"),
             step("Monthly shares of 1969, of missing birth years and of pass users", sql=sql, result=tab(a)),
             step("Pass users often give no birth year; their share of trips changes little from December to January",
                  uses="q2.monthly_trips"),
             step("Compare December 2017 with January 2018", source="calculation",
                  result={"pct_1969": [d17.pct_1969, j18.pct_1969], "pct_missing": [d17.pct_missing, j18.pct_missing],
                          "pct_pass_users": [d17.pct_pass, j18.pct_pass]})]
    gold = {"key_facts": [
        f"Yes, abruptly in January 2018: missing birth years drop from {d17.pct_missing}% to {j18.pct_missing}% and "
        f"1969 jumps from {d17.pct_1969}% to {j18.pct_1969}% of trips.",
        "It is a change in recording (1969 fills missing values), not in riders: the share of pass users, who give no "
        f"birth year, changes little ({d17.pct_pass}% to {j18.pct_pass}%)."], "required": [0, 1]}
    return steps, gold, " ".join(gold["key_facts"])


@task("q4.january_2017", "Q4.1",
      "Were there unusual days in the departures from Manhattan stations in January 2017? For each, say what happened "
      "and where.", {"type": "json", "schema": {"anomalies": [{"dates": "list[date]", "explanation": "str"}]}},
      depends_on=["q3.daily_profile", "q3.weekly_cycle", "q3.temperature"], scoring={"metric": "event_f1", "date_tol_days": 0})
def january_2017(e):
    flags = [f for f in scan(e.con, pd.Timestamp("2017-01-01"), pd.Timestamp("2017-01-31"))
             if f["series"] in ("total", "dep_manhattan")]
    det = pd.DataFrame(flags)[["date", "series", "count", "expected", "direction", "z", "explained_by"]]
    sql_h = f"""SELECT hour(starttime) AS hour, count(*) FILTER (WHERE CAST(starttime AS DATE) = '2017-01-21') AS jan_21,
  count(*) FILTER (WHERE CAST(starttime AS DATE) = '2017-01-28') AS jan_28
FROM trips WHERE CAST(starttime AS DATE) IN ('2017-01-21', '2017-01-28') AND {PUBLIC} GROUP BY 1 ORDER BY 1"""
    sql_s = f"""SELECT s.station_id, s.name, count(*) FILTER (WHERE CAST(t.starttime AS DATE) = '2017-01-21') AS trip_ends_jan_21,
  count(*) FILTER (WHERE CAST(t.starttime AS DATE) = '2017-01-28') AS trip_ends_jan_28
FROM trips t JOIN stations s ON s.station_id IN (t.start_station_id, t.end_station_id)
WHERE CAST(t.starttime AS DATE) IN ('2017-01-21', '2017-01-28') AND s.borough = 'Manhattan' AND {PUBLIC}
GROUP BY 1, 2 HAVING trip_ends_jan_28 > 0 ORDER BY trip_ends_jan_21 - trip_ends_jan_28 DESC, 1 LIMIT 8"""
    sql_w = f"""WITH w AS {WX.format(t='ghcnd')} SELECT date, tmax, prcp FROM w WHERE date IN ('2017-01-21', '2017-01-28')"""
    h, st = e.q(sql_h), e.q(sql_s)
    up = det[det.direction == "up"]
    day = up.date.iloc[0]
    afternoon = int(h[h.hour.between(13, 18)].jan_21.sum()), int(h[h.hour.between(13, 18)].jan_28.sum())
    gold = {"anomalies": [{"dates": [day], "explanation": "Up: the Women's March in New York City, the day after the "
                           "presidential inauguration; the extra trips start and end around E 39-47 St near 2 Ave and at "
                           "Grand Army Plaza and Central Park South, in the afternoon."}]}
    steps = [
        step("Weekend and weekday profiles differ", uses="q3.daily_profile"),
        step("Saturdays are a pass user day and have fewer member trips: compare each day with the same weekday",
             uses="q3.weekly_cycle"),
        step("Scan daily trips and Manhattan departures against a weekday baseline (robust z-scores)",
             source="reference method", result=tab(det)),
        step("Compare the hours of the flagged Saturday with the next Saturday", sql=sql_h, result=tab(h),
             note=f"1-7 pm: {afternoon[0]:,} departures against {afternoon[1]:,}."),
        step("Stations with the largest increase in trip ends (stations open on both days)", sql=sql_s, result=tab(st),
             note="Around the rally (E 39-47 St near 2 Ave) and the end of the march (Grand Army Plaza, Central Park S)."),
        step("The weather does not explain it: mild and dry, and ridership rises with temperature", sql=sql_w,
             result=tab(e.q(sql_w)), uses="q3.temperature"),
        step("Find the event of that Saturday", doc=quote("womens_march", 0, 1)),
    ]
    up_row = up.iloc[0]
    resp = (f"One unusual day: Saturday {day}, when Manhattan departures were {int(up_row['count']):,} against about "
            f"{int(up_row['expected']):,} expected. The extra trips were in the afternoon and at stations around E 39-47 St "
            f"near 2 Ave and at Grand Army Plaza and Central Park South, the start and end of the Women's March in New "
            f"York City, held the day after the presidential inauguration. The weather was mild and dry.")
    return steps, gold, resp


@task("q4.queens_shift", "Q4.2",
      "Did the distribution of departures across boroughs change between 2017 and 2018? If so, when and where did it "
      "change, and is it riders moving between boroughs or new stations?", TEXT,
      depends_on=["q3.borough_share", "q3.growth_trend"])
def queens_shift(e):
    sql = f"""SELECT date_trunc('month', t.starttime)::DATE AS month,
  round(avg((s.borough = 'Queens')::INT) * 100, 2) AS pct_queens,
  round(avg((s.borough = 'Brooklyn')::INT) * 100, 2) AS pct_brooklyn
FROM trips t JOIN stations s ON s.station_id = t.start_station_id
WHERE t.starttime >= '2017-01-01' AND {PUBLIC} GROUP BY 1 ORDER BY 1"""
    sql_s = """SELECT date_trunc('month', first_trip)::DATE AS opened, count(*) AS stations,
  string_agg(DISTINCT zip_code, ', ' ORDER BY zip_code) AS zip_codes
FROM stations WHERE borough = 'Queens' GROUP BY 1 ORDER BY 1"""
    sql_o = f"""SELECT year(t.starttime) AS year, s.first_trip < DATE '2017-09-01' AS opened_before_sep_2017, count(*) AS trips
FROM trips t JOIN stations s ON s.station_id = t.start_station_id
WHERE s.borough = 'Queens' AND year(t.starttime) IN (2017, 2018) AND {PUBLIC} GROUP BY 1, 2 ORDER BY 1, 2"""
    a, st, o = e.q(sql), e.q(sql_s), e.q(sql_o)
    m = a.set_index(a.month.astype(str).str[:7])
    new = st[st.opened.astype(str).str[:7].isin(["2017-09", "2017-10"])]
    old17 = int(o[(o.year == 2017) & o.opened_before_sep_2017].trips.sum())
    old18 = int(o[(o.year == 2018) & o.opened_before_sep_2017].trips.sum())
    new18 = int(o[(o.year == 2018) & ~o.opened_before_sep_2017].trips.sum())
    steps = [
        step("The share outside Manhattan grows steadily; look for a break", uses="q3.borough_share"),
        step("Monthly share of departures from Queens and Brooklyn stations", sql=sql, result=tab(a),
             note="Queens more than doubles from September to October 2017 and stays at the new level."),
        step("Queens stations by month of opening: the fall 2017 expansion step of the network", sql=sql_s,
             result=tab(st), uses="q3.growth_trend"),
        step("Queens departures from stations opened before and after September 2017", sql=sql_o, result=tab(o)),
    ]
    gold = {"key_facts": [
        f"Yes, abruptly in October 2017: the share of departures from Queens stations went from {m.pct_queens['2017-09']}% "
        f"in September to {m.pct_queens['2017-10']}% in October and reached {m.pct_queens.loc['2018-06':'2018-09'].max()}% "
        f"in summer 2018, while Brooklyn kept its gradual rise.",
        f"It is new stations: {int(new.stations.sum())} Queens stations opened in September-October 2017 in Astoria and "
        "Long Island City (ZIP codes 11101-11106).",
        f"In 2018 they had {new18:,} departures; the older Queens stations had {old18:,} (against {old17:,} in 2017), so "
        "riders did not move from the old stations."], "required": [0, 1]}
    return steps, gold, " ".join(gold["key_facts"])


@task("q4.taxi_and_bikes", "Q4.3",
      "Across the Manhattan taxi zones that have bike stations, how are bike departures per day related to short taxi "
      "trips (under 2 miles) per day in 2017? Which zones depart most from the relation?", TEXT,
      depends_on=["q1.trip_records", "q3.trip_distance"])
def taxi_and_bikes(e):
    sql = f"""WITH b AS (SELECT s.taxi_zone_id AS zone_id, count(*) / 365.0 AS bike_per_day, count(DISTINCT s.station_id) AS stations
           FROM trips t JOIN stations s ON s.station_id = t.start_station_id
           WHERE year(t.starttime) = 2017 AND s.borough = 'Manhattan' AND s.first_trip < DATE '2017-01-01'
             AND s.last_trip >= DATE '2017-12-31' AND {PUBLIC} GROUP BY 1),
     x AS (SELECT zone_id, sum(short_trips) / 365.0 AS short_taxi_per_day FROM taxi_daily WHERE year(date) = 2017 GROUP BY 1)
SELECT z.zone_id, z.zone, b.stations, round(b.bike_per_day) AS bike_per_day, round(x.short_taxi_per_day) AS short_taxi_per_day
FROM b JOIN x USING (zone_id) JOIN taxi_zones z USING (zone_id) WHERE b.bike_per_day >= 20 AND x.short_taxi_per_day >= 1 ORDER BY 1"""
    a = e.q(sql)
    lx, ly = np.log(a.short_taxi_per_day), np.log(a.bike_per_day)
    rho = float(stats.spearmanr(a.short_taxi_per_day, a.bike_per_day).correlation)
    fit = np.polyfit(lx, ly, 1)
    a["ratio_to_fit"] = np.round(np.exp(ly - np.polyval(fit, lx)), 2)
    hi, lo = a.sort_values("ratio_to_fit", ascending=False).head(4), a.sort_values("ratio_to_fit").head(4)
    steps = [
        step("Count departures at public stations, by the taxi zone of the station", uses="q1.trip_records"),
        step("Most bike trips are shorter than 2 miles (3.2 km), so short taxi trips are the comparable demand",
             uses="q3.trip_distance"),
        step("Bike departures and short taxi pickups per day of 2017, by Manhattan zone (stations open all year; zones "
             "with 20 or more bike departures a day)", sql=sql, result=tab(a.drop(columns="ratio_to_fit"))),
        step("Rank correlation and a log-log line; each zone's ratio to the line", source="calculation",
             result={"zones": len(a), "spearman": round(rho, 3), "elasticity": round(float(fit[0]), 3),
                     "most_above": tab(hi[["zone", "ratio_to_fit"]]), "most_below": tab(lo[["zone", "ratio_to_fit"]])}),
    ]
    gold = {"key_facts": [
        f"Zones with more short taxi trips also have more bike departures (Spearman {rho:.2f} across {len(a)} zones), but "
        f"less than proportionally (log-log slope {fit[0]:.2f}).",
        f"Far above the relation: {', '.join(hi.zone.head(3))} ({hi.ratio_to_fit.iloc[2]:.1f}-"
        f"{hi.ratio_to_fit.iloc[0]:.1f} times the bike trips the line predicts).",
        f"Far below: {', '.join(lo.zone.head(3))} ({lo.ratio_to_fit.iloc[0]:.2f}-{lo.ratio_to_fit.iloc[2]:.2f} times): "
        "the Upper East Side takes many short taxi trips for its bike use."], "required": [0, 1, 2]}
    return steps, gold, " ".join(gold["key_facts"])


@task("q4.weather_beyond_temp", "Q4.3",
      "Besides temperature, which daily weather conditions go together with lower or higher ridership? Check rain, "
      "snow on the ground, wind and fog on the weekdays of 2016 and 2017, after removing the effects of temperature "
      "and season.", TEXT, depends_on=["q3.temperature", "q1.weather_units", "q1.gsod_vs_ghcnd"])
def weather_beyond_temp(e):
    sql = f"""WITH d AS (SELECT CAST(starttime AS DATE) AS dt, count(*) AS n FROM trips
           WHERE starttime >= '2016-01-01' AND starttime < '2018-01-01' AND {PUBLIC} GROUP BY 1),
     w AS {WX.format(t='ghcnd')},
     f AS (SELECT date, max(value) AS fog FROM ghcnd WHERE id = 'USW00094728' AND element IN ('WT01', 'WT02') GROUP BY 1)
SELECT d.dt AS date, d.n AS trips, w.tmax, w.prcp, w.snwd, w.awnd, coalesce(f.fog, 0) AS fog
FROM d JOIN w ON w.date = d.dt LEFT JOIN f ON f.date = d.dt
WHERE dayofweek(d.dt) BETWEEN 1 AND 5 AND d.dt NOT IN (SELECT date FROM holidays) ORDER BY 1"""
    a = e.q(sql)
    a["date"] = pd.to_datetime(a.date)
    a["y"] = np.log(a.trips)
    # control: month of the year (season and network growth), weekday, and temperature in 3 C bands
    X = pd.get_dummies(pd.DataFrame({"m": a.date.dt.to_period("M").astype(str), "dow": a.date.dt.dayofweek.astype(str),
                                     "t": (a.tmax // 3).astype(int).astype(str)}), drop_first=True).astype(float)
    X.insert(0, "c", 1.0)
    beta, *_ = np.linalg.lstsq(X.values, a.y.values, rcond=None)
    a["resid"] = a.y - X.values @ beta
    rows = []
    windy = float(a.awnd.quantile(0.9))
    dry = a.prcp < 1
    eff = lambda m, base: round(float(np.exp(a.resid[m & base].mean() - a.resid[~m & base].mean()) - 1) * 100, 1)
    for name, col, cut in [("rain over 5 mm", "prcp", 5), ("snow on the ground", "snwd", 0.1),
                           ("windiest tenth of days", "awnd", windy), ("fog", "fog", 1)]:
        flag = a[col] >= cut
        rho = float(stats.spearmanr(a[col], a.resid, nan_policy="omit").correlation)
        rows.append(dict(condition=name, days=int(flag.sum()), spearman_with_residual=round(rho, 3),
                         effect_pct=eff(flag, a.y == a.y), days_without_rain=int((flag & dry).sum()),
                         effect_pct_without_rain=None if col == "prcp" else eff(flag, dry)))
    r = pd.DataFrame(rows)
    steps = [
        step("Temperature has a strong, curved effect: control it in bands", uses="q3.temperature"),
        step("Take the weather of each calendar day from ghcnd, not gsod (whose days are in GMT)", uses="q1.gsod_vs_ghcnd"),
        step("Daily trips on non-holiday weekdays with Central Park weather (converted units; fog from weather types "
             "WT01 and WT02)", sql=sql, result=tab(a.drop(columns=["y", "resid"]).assign(date=a.date.dt.date.astype(str)), limit=10),
             uses="q1.weather_units"),
        step("Regress log trips on month, weekday and 3 C temperature bands; relate each condition to the residual, "
             "on all days and on days under 1 mm of rain", source="calculation", result=tab(r),
             note=f"Windiest tenth: average wind {windy:.1f} m/s or more."),
    ]
    rr = r.set_index("condition")
    gold = {"key_facts": [
        f"Rain is the strongest: weekdays with more than 5 mm have about {abs(rr.effect_pct['rain over 5 mm']):.0f}% fewer "
        f"trips than other days of the same month and temperature (Spearman with the residual "
        f"{rr.spearman_with_residual['rain over 5 mm']:.2f}).",
        f"Snow on the ground lowers ridership by about {abs(rr.effect_pct['snow on the ground']):.0f}% on top of the cold.",
        f"Fog ({rr.effect_pct['fog']:+.0f}%) and strong wind ({rr.effect_pct['windiest tenth of days']:+.0f}%) look harmful, "
        f"but mostly because they come with rain: on days without rain the differences are "
        f"{rr.effect_pct_without_rain['fog']:+.0f}% and {rr.effect_pct_without_rain['windiest tenth of days']:+.0f}%."],
        "required": [0, 1, 2]}
    return steps, gold, " ".join(gold["key_facts"])


@task("q4.open_2018", "Q4.open",
      "Look at the trip records of 2018 and report the most important things you notice about ridership, riders and "
      "the station network.", TEXT, depends_on=["q3.growth_trend", "q4.queens_shift", "q4.birth_year_shift"])
def open_2018(e):
    sql = f"""SELECT year(starttime) AS year, count(*) AS trips, round(avg((usertype = 'Customer')::INT) * 100, 1) AS pct_pass
FROM trips WHERE year(starttime) BETWEEN 2015 AND 2018 AND {PUBLIC} GROUP BY 1 ORDER BY 1"""
    a = e.q(sql).set_index("year")
    g = a.trips / a.trips.shift(1) - 1
    flags = [f for f in scan(e.con, pd.Timestamp("2018-01-01"), pd.Timestamp("2018-12-31")) if f["series"] == "total"]
    det = pd.DataFrame(flags)[["date", "count", "expected", "z", "explained_by"]]
    why = det.explained_by.str.replace(r" \(.*", "", regex=True).value_counts()
    snow = det[det.explained_by == "snow"]
    steps = [
        step("Growth in earlier years came with service-area expansions", uses="q3.growth_trend"),
        step("Trips per year and the share of pass users", sql=sql, result=tab(a.reset_index()),
             note=f"Growth: {', '.join(f'{y} {x * 100:+.1f}%' for y, x in g.dropna().items())}."),
        step("New Queens stations from October 2017 shift departures to Queens", uses="q4.queens_shift"),
        step("From January 2018 missing birth years are recorded as 1969", uses="q4.birth_year_shift"),
        step("Scan the days of 2018 for unusual ridership (weekday baseline, robust z-scores)", source="reference method",
             result=tab(det), note=f"Explanations: {', '.join(f'{k} {v}' for k, v in why.items())}."),
    ]
    gold = {"key_facts": [
        f"Ridership kept growing but more slowly: {a.trips[2018] / 1e6:.1f} million public trips in 2018, "
        f"{g[2018] * 100:+.1f}% after {g[2017] * 100:+.1f}% in 2017 and {g[2016] * 100:+.1f}% in 2016.",
        "Queens is the new growth area: the stations opened in Astoria and Long Island City in fall 2017 more than "
        "doubled its share of departures.",
        "Birth years are no longer missing from January 2018: 1969 fills missing values, so age statistics of 2018 "
        "cannot be compared with earlier years without cleaning.",
        f"{len(det)} days of 2018 had unusually low ridership, all explained by weather or holidays; the snow storms of "
        f"{', '.join(snow.date)} cut it most."], "required": [0, 1, 2]}
    return steps, gold, " ".join(gold["key_facts"])


@task("q4.open_data_quality", "Q4.open",
      "Before we build a ridership dashboard on the trip records, look for problems in the data and report the most "
      "important ones, with their size.", TEXT,
      depends_on=["q1.trip_records", "q1.usertype", "q1.birth_year_1969", "q2.daily_jan2016", "q2.moved_station",
                  "q3.duration_distribution"])
def open_data_quality(e):
    sql = f"""SELECT count(*) AS trips,
  count(*) FILTER (WHERE tripduration > 86400) AS over_24h,
  count(*) FILTER (WHERE usertype IS NULL) AS no_usertype,
  count(*) FILTER (WHERE birth_year = 1969 AND year(starttime) = 2018) AS birth_1969_in_2018,
  count(*) FILTER (WHERE birth_year < 1920) AS born_before_1920,
  count(*) FILTER (WHERE NOT ({PUBLIC})) AS service_station_trips
FROM trips"""
    sql_d = """WITH d AS (SELECT range::DATE AS day FROM range(DATE '2013-07-01', DATE '2019-01-01', INTERVAL 1 DAY)),
     n AS (SELECT CAST(starttime AS DATE) AS day, count(*) AS trips FROM trips GROUP BY 1)
SELECT d.day FROM d LEFT JOIN n USING (day) WHERE n.trips IS NULL ORDER BY 1"""
    sql_s = """SELECT count(*) FILTER (WHERE names > 1) AS station_ids_with_several_names,
  (SELECT count(*) FROM (SELECT name FROM station_names GROUP BY 1 HAVING count(DISTINCT station_id) > 1)) AS names_on_several_ids
FROM (SELECT station_id, count(*) AS names FROM station_names GROUP BY 1)"""
    a, z, s = e.q(sql).iloc[0], e.q(sql_d), e.q(sql_s).iloc[0]
    zero = [str(x)[:10] for x in z.day]
    steps = [
        step("Ride time and its long outliers", uses="q1.trip_records"),
        step("Ride times are right-skewed: a few rides of days distort averages, not medians", uses="q3.duration_distribution"),
        step("Count the problem records", sql=sql, result=tab(e.q(sql))),
        step("Some trips have no user type", uses="q1.usertype"),
        step("Days without any trip (closures, not missing data)", sql=sql_d, result=tab(z), uses="q2.daily_jan2016"),
        step("Station numbers and names do not match one to one", sql=sql_s, result=tab(e.q(sql_s)), uses="q2.moved_station"),
        step("Birth year 1969 is a placeholder from 2018; user type is missing for some trips", uses="q1.birth_year_1969"),
    ]
    gold = {"key_facts": [
        f"{int(a.over_24h):,} trips last more than 24 hours (bikes not docked or lost): cap or drop them before averaging "
        "ride time.",
        f"From January 2018 missing birth years are recorded as 1969 ({int(a.birth_1969_in_2018):,} trips in 2018), and "
        f"{int(a.born_before_1920):,} trips have birth years before 1920: age needs cleaning.",
        f"Stations change names and numbers: {int(s.station_ids_with_several_names)} station numbers had more than one "
        f"name and {int(s.names_on_several_ids)} names belong to more than one number, so group by station number and "
        "map moved stations.",
        f"{int(a.service_station_trips):,} trips start or end at service stations (depots, workshops, test points); "
        "exclude them from public ridership.",
        f"{len(zero)} days have no trips at all ({', '.join(zero)}): closures for storms, which a dashboard should show as "
        f"zero, not as gaps; {int(a.no_usertype):,} trips have no user type."], "required": [0, 1, 2]}
    return steps, gold, " ".join(gold["key_facts"])


@task("q4.network_steps", "Q4.2",
      "From 2014 to 2017, did the station network grow gradually or in steps? Count the stations with departures in "
      "each month, say when the steps happened and in which boroughs, and how much of the ridership the new stations "
      "carried in the six months after each step.", TEXT, depends_on=["q3.growth_trend", "q2.closed_stations", "q2.moved_station"])
def network_steps(e):
    sql = f"""SELECT date_trunc('month', starttime)::DATE AS month, count(DISTINCT start_station_id) AS active_stations,
  count(*) AS trips
FROM trips WHERE starttime >= '2013-07-01' AND starttime < '2018-01-01' AND {PUBLIC} GROUP BY 1 ORDER BY 1"""
    a = e.q(sql)
    a["month"] = pd.to_datetime(a.month)
    a["change"] = a.active_stations.diff()
    a = a[a.month >= "2014-01-01"].reset_index(drop=True)
    big = a[a.change >= 25]
    # consecutive months of large growth form one step
    steps_, cur = [], []
    for r in big.itertuples():
        if cur and (r.month.year * 12 + r.month.month) - (cur[-1].month.year * 12 + cur[-1].month.month) > 1:
            steps_.append(cur)
            cur = []
        cur.append(r)
    steps_.append(cur)
    sql_o = f"""SELECT date_trunc('month', first_trip)::DATE AS month, borough, count(*) AS stations_opened
FROM stations WHERE first_trip >= '2014-01-01' AND first_trip < '2018-01-01' AND NOT {SERVICE.format(c='name')}
GROUP BY 1, 2 HAVING count(*) >= 10 ORDER BY 1, 2"""
    o = e.q(sql_o)
    sql_r = f"""SELECT date_trunc('month', s.first_trip)::DATE AS month, count(*) AS new_numbers,
  count(*) FILTER (WHERE EXISTS (SELECT 1 FROM station_names n JOIN stations p ON p.station_id = n.station_id
                                 WHERE n.name = s.name AND n.station_id <> s.station_id AND p.last_trip < s.first_trip + 31)) AS replacing_an_old_number
FROM stations s WHERE s.first_trip >= '2014-01-01' AND s.first_trip < '2018-01-01' AND NOT {SERVICE.format(c='s.name')}
GROUP BY 1 HAVING count(*) >= 25 ORDER BY 1"""
    r = e.q(sql_r)
    rows, sql_c = [], None
    for g in steps_:
        start, end = g[0].month, g[-1].month
        a0, a1 = f"{start:%Y-%m-%d}", f"{end + pd.DateOffset(months=1):%Y-%m-%d}"
        b1 = f"{end + pd.DateOffset(months=7):%Y-%m-%d}"
        sql_c = f"""SELECT round(avg((s.first_trip >= DATE '{a0}' AND s.first_trip < DATE '{a1}')::INT) * 100, 1) AS pct_of_trips_at_new_stations
FROM trips t JOIN stations s ON s.station_id = t.start_station_id
WHERE t.starttime >= '{a1}' AND t.starttime < '{b1}' AND {PUBLIC}"""
        rows.append(dict(step=f"{start:%Y-%m} to {end:%Y-%m}", stations_added=int(sum(x.change for x in g)),
                         active_after=int(g[-1].active_stations),
                         pct_of_trips_next_6_months_at_its_stations=float(e.v(sql_c))))
    st = pd.DataFrame(rows)
    flat = a[(a.change.abs() <= 10)]
    steps = [
        step("Ridership grew in steps that seem to follow expansions", uses="q3.growth_trend"),
        step("Stations with at least one departure, per month (service stations excluded), with trips against the "
             "same month a year before", sql=sql, result=tab(a.assign(month=a.month.dt.date.astype(str)))),
        step("Stations also close, so count the active ones rather than the openings", uses="q2.closed_stations"),
        step("A station that gets a new number at the same place is not new: count the new numbers that replace an "
             "old one", sql=sql_r, result=tab(r), uses="q2.moved_station"),
        step("Stations opened per month and borough (5 or more)", sql=sql_o, result=tab(o)),
        step("Group the months that added 25 or more active stations into steps, and take the share of the trips of "
             "the next six months that start at the stations opened in the step", sql=sql_c, result=tab(st),
             note="The query is the one of the last step; the others differ only in their dates."),
    ]
    boro = lambda s, t: ", ".join(sorted(o[(o.month.astype(str) >= s) & (o.month.astype(str) <= t)].borough.unique()))
    gold = {"key_facts": [
        f"In steps: the network was flat between them (month-to-month changes of at most 10 stations in "
        f"{len(flat)} of {len(a) - 1} months) and grew in {len(st)} steps: " + "; ".join(
            f"{x.step} (+{x.stations_added} active stations, to {x.active_after})" for x in st.itertuples()) + ".",
        "The steps were in late summer or fall, in " + "; ".join(
            f"{x.step[:4]}: {boro(x.step[:7] + '-01', x.step[-7:] + '-01')}" for x in st.itertuples()) + ".",
        "The new stations found riders at once: in the six months after each step they carried " + ", ".join(
            f"{x.pct_of_trips_next_6_months_at_its_stations:.0f}% ({x.step[:4]})" for x in st.itertuples())
        + " of all trips.",
        "Only a few of the new station numbers replace an old station at the same place, so the steps are real "
        "additions."], "required": [0, 2]}
    return steps, gold, " ".join(gold["key_facts"])


FLOW_DAYS = """t.starttime >= '{a}' AND t.starttime < '{b}' AND dayofweek(t.starttime) BETWEEN 1 AND 5
  AND CAST(t.starttime AS DATE) NOT IN (SELECT date FROM holidays)"""
SOUTH = "s.borough = 'Manhattan' AND s.latitude < 40.745"


def station_day_flows(e, a="2018-05-01", b="2018-06-01", hours=None):
    """Net gain (arrivals minus departures) per station and weekday; with hours, of the trips starting in them."""
    h = f" AND hour(t.starttime) BETWEEN {hours[0]} AND {hours[1]}" if hours else ""
    sql = f"""WITH x AS (SELECT CAST(t.starttime AS DATE) AS day, t.end_station_id AS sid, 1 AS v FROM trips t
           WHERE {FLOW_DAYS.format(a=a, b=b)}{h} AND {PUBLIC}
           UNION ALL SELECT CAST(t.starttime AS DATE), t.start_station_id, -1 FROM trips t
           WHERE {FLOW_DAYS.format(a=a, b=b)}{h} AND {PUBLIC})
SELECT x.day, x.sid AS station_id, sum(x.v) AS net FROM x JOIN stations s ON s.station_id = x.sid WHERE {SOUTH}
GROUP BY 1, 2"""
    return sql, e.q(sql)


@task("q4.flow_stability", "Q4.3",
      "For the Manhattan stations south of 40.745 N on the weekdays of May 2018 (without Memorial Day): how consistent "
      "is each station's net gain of bikes from one weekday to the next, and how is a station's net gain from the "
      "trips that start in the morning (6-10 am) related to its net gain from the trips that start in the evening "
      "(4-7 pm)?", TEXT, depends_on=["q2.station_net_flow", "q3.daily_profile"])
def flow_stability(e):
    sql, d = station_day_flows(e)
    p = d.pivot(index="day", columns="station_id", values="net").fillna(0)
    c = np.corrcoef(p.values)
    pair = c[np.triu_indices(len(p), 1)]
    mean = p.mean()
    sign = (np.sign(p.values) == np.sign(mean.values)).mean()
    big = mean.abs() >= 10
    sign_big = (np.sign(p.loc[:, big].values) == np.sign(mean[big].values)).mean()
    _, am = station_day_flows(e, hours=(6, 10))
    _, pm = station_day_flows(e, hours=(16, 19))
    x = pd.DataFrame({"am": am.groupby("station_id").net.sum() / len(p), "pm": pm.groupby("station_id").net.sum() / len(p),
                      "day": mean}).fillna(0)
    rho = float(stats.spearmanr(x.am, x.pm).correlation)
    fit = np.polyfit(x.am, x.pm, 1)
    steps = [
        step("The gains and losses per weekday of the stations (May 2018)", uses="q2.station_net_flow"),
        step("Members commute: departures peak in the morning and in the evening", uses="q3.daily_profile"),
        step("Net gain per station and weekday", sql=sql, result=tab(d, limit=10)),
        step("Consistency: correlation of the stations' net gains between every two weekdays, and how often a "
             "station's day has the sign of its average", source="calculation",
             result={"stations": int(p.shape[1]), "weekdays": int(p.shape[0]), "median_correlation_between_days":
                     round(float(np.median(pair)), 3), "min_correlation": round(float(pair.min()), 3),
                     "share_days_with_usual_sign": round(float(sign), 3),
                     "same_for_stations_with_10_or_more_a_day": round(float(sign_big), 3)}),
        step("Morning (6-10 am) and evening (4-7 pm) net gain per station and weekday", sql=station_day_flows(e, hours=(6, 10))[0],
             result=tab(x.sort_values("day").round(1).reset_index(), limit=12)),
        step("Rank correlation and a line between morning and evening net gains", source="calculation",
             result={"spearman": round(rho, 3), "evening_per_morning_bike": round(float(fit[0]), 3)}),
    ]
    gold = {"key_facts": [
        f"The pattern repeats every weekday: the stations' net gains of two weekdays correlate with a median of "
        f"{np.median(pair):.2f} (lowest {pair.min():.2f}), and stations with 10 or more bikes a day on average have "
        f"the sign of their average on {sign_big * 100:.0f}% of weekdays.",
        f"Morning and evening work against each other: stations that gain in the morning lose in the evening "
        f"(Spearman {rho:.2f} across {len(x)} stations), but the evening gives back only about "
        f"{abs(fit[0]) * 100:.0f}% of the morning flow (slope {fit[0]:.2f}).",
        "So a station's daily gain is mostly a morning gain that the evening does not undo, and it is predictable "
        "enough to plan rebalancing in advance."], "required": [0, 1]}
    return steps, gold, " ".join(gold["key_facts"])


# ====================================================================== Q5 Diagnose
@task("q5.growth_2015", "Q5.2",
      "Trips in August and September 2015 were much higher than in August and September 2014. How much of the increase "
      "came from new stations and how much from stations that already existed?",
      {"type": "json", "schema": {"new_stations_pct": "float", "existing_stations_pct": "float"}},
      depends_on=["q3.growth_trend", "q4.network_steps"], scoring={"metric": "fields", "fields": {"new_stations_pct": {"tol": 5}}})
def growth_2015(e):
    sql = f"""SELECT year(t.starttime) AS year, s.first_trip >= DATE '2015-07-15' AS new_station, count(*) AS trips
FROM trips t JOIN stations s ON s.station_id = t.start_station_id
WHERE month(t.starttime) IN (8, 9) AND year(t.starttime) IN (2014, 2015) AND {PUBLIC} GROUP BY 1, 2 ORDER BY 1, 2"""
    a = e.q(sql)
    tot = a.groupby("year").trips.sum()
    new = int(a[(a.year == 2015) & a.new_station].trips.sum())
    o14, o15 = int(a[(a.year == 2014) & ~a.new_station].trips.sum()), int(a[(a.year == 2015) & ~a.new_station].trips.sum())
    gr = int(tot[2015] - tot[2014])
    nst = e.v("SELECT count(*) FROM stations WHERE first_trip BETWEEN '2015-07-15' AND '2015-09-30'")
    gold = {"new_stations_pct": round(new / gr * 100, 1), "existing_stations_pct": round((o15 - o14) / gr * 100, 1),
            "increase_trips": gr, "new_stations": int(nst)}
    steps = [step("The 2015 growth step coincides with the summer 2015 expansion", uses="q3.growth_trend"),
             step("The network grew by over a hundred stations in August-September 2015", uses="q4.network_steps"),
             step("Split August-September trips by whether the start station opened after July 15, 2015", sql=sql, result=tab(a)),
             step("Attribute the increase", source="calculation", result=gold)]
    resp = (f"Trips rose by {gr:,} ({gr / tot[2014] * 100:.0f}%). {gold['new_stations_pct']:.0f}% of the increase came from "
            f"the {nst} stations opened in the summer 2015 expansion ({new:,} trips), and {gold['existing_stations_pct']:.0f}% "
            f"from existing stations ({(o15 / o14 - 1) * 100:+.0f}% there).")
    return steps, gold, resp


@task("q5.ride_time_july_vs_march", "Q5.1",
      "Average ride time (rides up to 3 hours) was much higher in July 2016 than in March 2016. What explains the "
      "increase? Rank the causes.", {"type": "json", "schema": {"causes": [{"cause": "str", "contribution": "str"}]}},
      depends_on=["q3.duration_distribution", "q1.usertype", "q4.open_data_quality"],
      scoring={"metric": "cause_ranking", "judge": "llm"})
def ride_time(e):
    sql = """SELECT month(starttime) AS month, usertype, count(*) AS rides, avg(tripduration) / 60 AS avg_min FROM trips
WHERE year(starttime) = 2016 AND month(starttime) IN (3, 7) AND usertype IS NOT NULL AND tripduration <= 10800
GROUP BY 1, 2 ORDER BY 1, 2"""
    sh = e.q(sql)
    a, b = sh[sh.month == 3].set_index("usertype"), sh[sh.month == 7].set_index("usertype")
    ss = S.shift_share(a.avg_min, a.rides / a.rides.sum(), b.avg_min, b.rides / b.rides.sum())
    mix, rate = ss["mix_total"] / ss["total"], ss["rate_total"] / ss["total"]
    gold = {"change_minutes": round(ss["total"], 2), "causes": sorted([
        dict(cause=f"more pass users, who ride longer (share {a.rides['Customer'] / a.rides.sum() * 100:.1f}% to "
                   f"{b.rides['Customer'] / b.rides.sum() * 100:.1f}%)", contribution=f"{mix * 100:.0f}% (mix effect)", w=mix),
        dict(cause="longer rides within each user type in summer", contribution=f"{rate * 100:.0f}% (rate effect)", w=rate)],
        key=lambda c: -c["w"])}
    for c in gold["causes"]:
        c.pop("w")
    steps = [step("Rides of days are bikes not docked: averages need a cap (here 3 hours)", uses="q4.open_data_quality"),
             step("Pass users ride much longer than members", uses="q3.duration_distribution"),
             step("Rides and average ride time by month and user type", sql=sql, result=tab(sh), uses="q1.usertype"),
             step("Shift-share decomposition: mix effect (more pass users) and rate effect (longer rides)", source="calculation",
                  result={"change_minutes": round(ss["total"], 2), "mix_effect_min": round(ss["mix_total"], 2),
                          "rate_effect_min": round(ss["rate_total"], 2)})]
    resp = (f"Average ride time rose by {ss['total']:.1f} minutes. " + " ".join(
        f"{i + 1}. {c['cause'][0].upper()}{c['cause'][1:]}: {c['contribution']} of the increase." for i, c in enumerate(gold["causes"])))
    return steps, gold, resp


@task("q5.rider_age", "Q5.1",
      "A dashboard shows that the average age of riders, computed from birth_year, rose by almost a year from "
      "January-May 2017 to January-May 2018. Are our riders getting older?",
      {"type": "json", "schema": {"riders_older": "bool", "explanation": "str"}},
      depends_on=["q4.birth_year_shift", "q3.age_distribution", "q2.monthly_trips"], control="decoy",
      scoring={"metric": "fields+explanation", "fields": ["riders_older"], "judge": "llm"})
def rider_age(e):
    sql = """SELECT year(starttime) AS year,
  avg(year(starttime) - birth_year) FILTER (WHERE birth_year IS NOT NULL) AS avg_age_dashboard,
  avg(year(starttime) - birth_year) FILTER (WHERE birth_year IS NOT NULL AND birth_year <> 1969) AS avg_age_without_1969,
  avg(year(starttime) - birth_year) FILTER (WHERE usertype = 'Subscriber' AND birth_year <> 1969 AND birth_year >= 1920) AS avg_age_members_clean,
  round(avg((usertype = 'Customer')::INT) * 100, 1) AS pct_pass_trips
FROM trips WHERE month(starttime) BETWEEN 1 AND 5 AND year(starttime) IN (2017, 2018) GROUP BY 1 ORDER BY 1"""
    a = e.q(sql).set_index("year")
    rise = a.avg_age_dashboard[2018] - a.avg_age_dashboard[2017]
    rise_wo = a.avg_age_without_1969[2018] - a.avg_age_without_1969[2017]
    gold = {"riders_older": False, "avg_age_dashboard": {int(k): round(x, 2) for k, x in a.avg_age_dashboard.items()},
            "avg_age_members_clean": {int(k): round(x, 2) for k, x in a.avg_age_members_clean.items()}}
    steps = [step("Recall the recording change of January 2018 (1969 fills missing birth years)", uses="q4.birth_year_shift"),
             step("Age is computed for members, without 1969 and implausible years", uses="q3.age_distribution"),
             step("Average age as the dashboard computes it, without 1969 only, and for members without placeholders, "
                  "with the share of pass trips", sql=sql, result=tab(a.reset_index())),
             step("A change in the rider mix is not the cause: the share of pass trips (who rarely give a birth year) is "
                  "similar in both periods", uses="q2.monthly_trips"),
             step("How much of the dashboard's rise the placeholder explains", source="calculation",
                  result={"dashboard_rise_years": round(float(rise), 2), "rise_without_1969": round(float(rise_wo), 2)})]
    resp = (f"No. The dashboard's age rose from {a.avg_age_dashboard[2017]:.1f} to {a.avg_age_dashboard[2018]:.1f} because "
            "from January 2018 missing birth years are recorded as 1969, which adds many riders aged 48-49; without the "
            f"1969 values the change is {rise_wo:+.1f} years. The share of pass trips is about the same in both periods "
            f"({a.pct_pass_trips[2017]}% and {a.pct_pass_trips[2018]}%), so the rider mix did not change. For members, "
            f"without the 1969 placeholder and implausible years, the average age is {a.avg_age_members_clean[2017]:.1f} in "
            f"2017 and {a.avg_age_members_clean[2018]:.1f} in 2018.")
    return steps, gold, resp


@task("q5.growth_2018", "Q5.2",
      "Trips rose from 2017 to 2018. How much of the increase came from stations in Manhattan, Brooklyn and Queens, and "
      "how much of it from stations that opened after the start of 2017?",
      {"type": "json", "schema": {"manhattan_pct": "float", "brooklyn_pct": "float", "queens_pct": "float",
                                  "new_stations_pct": "float"}},
      depends_on=["q3.borough_share", "q4.queens_shift", "q4.network_steps"],
      scoring={"metric": "fields", "fields": {"manhattan_pct": {"tol": 5}, "brooklyn_pct": {"tol": 5},
                                              "queens_pct": {"tol": 5}, "new_stations_pct": {"tol": 5}}})
def growth_2018(e):
    sql = f"""SELECT s.borough, s.first_trip >= DATE '2017-01-01' AS opened_2017_or_later,
  count(*) FILTER (WHERE year(t.starttime) = 2017) AS trips_2017, count(*) FILTER (WHERE year(t.starttime) = 2018) AS trips_2018
FROM trips t JOIN stations s ON s.station_id = t.start_station_id
WHERE year(t.starttime) IN (2017, 2018) AND s.borough IN ('Manhattan', 'Brooklyn', 'Queens') AND {PUBLIC}
GROUP BY 1, 2 ORDER BY 1, 2"""
    a = e.q(sql)
    a["change"] = a.trips_2018 - a.trips_2017
    tot = int(a.change.sum())
    by = a.groupby("borough").change.sum() / tot * 100
    new = float(a[a.opened_2017_or_later].change.sum() / tot * 100)
    gold = {"manhattan_pct": round(float(by["Manhattan"]), 1), "brooklyn_pct": round(float(by["Brooklyn"]), 1),
            "queens_pct": round(float(by["Queens"]), 1), "new_stations_pct": round(new, 1), "increase_trips": tot}
    steps = [
        step("Brooklyn and Queens grow faster than Manhattan", uses="q3.borough_share"),
        step("The fall 2017 step added stations, many of them in Queens", uses="q4.network_steps"),
        step("The new Queens stations brought new riders rather than taking them from older stations",
             uses="q4.queens_shift"),
        step("Trips of 2017 and 2018 by borough of the start station and by whether the station opened in 2017 or "
             "later", sql=sql, result=tab(a)),
        step("Each part's share of the increase", source="calculation", result=gold),
    ]
    old_m = a[(a.borough == "Manhattan") & ~a.opened_2017_or_later].iloc[0]
    old = a[~a.opened_2017_or_later].sum()
    resp = (f"Public trips rose by {tot:,}. Manhattan stations brought {gold['manhattan_pct']:.0f}% of the increase, "
            f"Brooklyn {gold['brooklyn_pct']:.0f}% and Queens {gold['queens_pct']:.0f}%. Stations opened in 2017 or "
            f"later gained {new:.0f}% of the increase, more than all of it, because the stations open before 2017 lost "
            f"trips ({int(old.change):+,}; Manhattan ones {(old_m.trips_2018 / old_m.trips_2017 - 1) * 100:+.1f}%). Many "
            "new stations were open for only part of 2017 (the Queens stations from fall 2017).")
    return steps, gold, resp


@task("q5.weather_losses", "Q5.2",
      "Weather costs trips. On the non-holiday weekdays of 2017, how many trips did rain, snow on the ground, cold "
      "(daily maximum under 5 C) and heat (30 C or more) cost, compared with otherwise similar days of the same month "
      "and weekday? Which condition costs most?",
      {"type": "json", "schema": {"lost_trips": "dict[str, int]", "pct_of_weekday_trips": "dict[str, float]"}},
      depends_on=["q4.weather_beyond_temp", "q3.temperature", "q2.trace_days"],
      scoring={"metric": "fields", "fields": {"pct_of_weekday_trips": {"abs_tol": 1.0}}})
def weather_losses(e):
    sql = f"""WITH d AS (SELECT CAST(starttime AS DATE) AS dt, count(*) AS n FROM trips
           WHERE starttime >= '2016-01-01' AND starttime < '2018-01-01' AND {PUBLIC} GROUP BY 1),
     w AS {WX.format(t='ghcnd')}
SELECT d.dt AS date, d.n AS trips, w.tmax, w.prcp, w.snwd FROM d JOIN w ON w.date = d.dt
WHERE dayofweek(d.dt) BETWEEN 1 AND 5 AND d.dt NOT IN (SELECT date FROM holidays) ORDER BY 1"""
    a = e.q(sql)
    a["date"] = pd.to_datetime(a.date)
    cond = pd.DataFrame({"rain 1-5 mm": a.prcp.between(1, 5, inclusive="left"), "rain 5-15 mm": a.prcp.between(5, 15, inclusive="left"),
                         "rain 15 mm or more": a.prcp >= 15, "snow on the ground": a.snwd > 0,
                         "cold (under 5 C)": a.tmax < 5, "heat (30 C or more)": a.tmax >= 30}).astype(float)
    X = pd.get_dummies(pd.DataFrame({"m": a.date.dt.to_period("M").astype(str), "dow": a.date.dt.dayofweek.astype(str)}),
                       drop_first=True).astype(float)
    X = pd.concat([pd.Series(1.0, index=a.index, name="const"), X, cond], axis=1)
    y = np.log(a.trips.values)
    beta = pd.Series(np.linalg.lstsq(X.values, y, rcond=None)[0], index=X.columns)
    r2 = 1 - ((y - X.values @ beta.values) ** 2).sum() / ((y - y.mean()) ** 2).sum()
    y17 = a.date.dt.year == 2017
    tot = int(a.trips[y17].sum())
    rows = []
    for c in cond.columns:
        m = (cond[c] == 1) & y17
        lost = float((a.trips[m] * (np.exp(-beta[c]) - 1)).sum())
        rows.append(dict(condition=c, days_2017=int(m.sum()), effect_pct=round(float(np.exp(beta[c]) - 1) * 100, 1),
                         lost_trips=int(round(lost)), pct_of_weekday_trips=round(lost / tot * 100, 2)))
    r = pd.DataFrame(rows).set_index("condition")
    rain = r.loc[["rain 1-5 mm", "rain 5-15 mm", "rain 15 mm or more"]]
    group = {"rain": int(rain.lost_trips.sum()), "snow on the ground": int(r.lost_trips["snow on the ground"]),
             "cold": int(r.lost_trips["cold (under 5 C)"]), "heat": int(r.lost_trips["heat (30 C or more)"])}
    gold = {"lost_trips": group, "pct_of_weekday_trips": {k: round(v / tot * 100, 1) for k, v in group.items()},
            "weekday_trips_2017": tot}
    steps = [
        step("Rain and snow lower ridership beyond temperature", uses="q4.weather_beyond_temp"),
        step("Ridership falls in the cold and seems lower above 30 C", uses="q3.temperature"),
        step("A trace of rain is recorded as 0 mm, so trace days count as dry", uses="q2.trace_days"),
        step("Daily trips on non-holiday weekdays of 2016-2017 with Central Park weather", sql=sql,
             result=tab(a.assign(date=a.date.dt.date.astype(str)), limit=10)),
        step("Regress log trips on month, weekday and the six conditions; R2 of the fit", source="calculation",
             result={"r2": round(float(r2), 3), "effects_pct": r.effect_pct.to_dict()}),
        step("Trips each condition cost in 2017: on its days, recorded trips times (1 / effect - 1)",
             source="calculation", result=tab(r.reset_index())),
    ]
    hot = r.loc["heat (30 C or more)"]
    resp = (f"Rain costs by far the most: about {group['rain']:,} weekday trips in 2017 "
            f"({gold['pct_of_weekday_trips']['rain']:.1f}% of the {tot:,} weekday trips); a day with 15 mm or more has "
            f"{abs(r.effect_pct['rain 15 mm or more']):.0f}% fewer trips than a similar dry day. Cold days cost about "
            f"{group['cold']:,} trips ({gold['pct_of_weekday_trips']['cold']:.1f}%) and snow on the ground "
            f"{group['snow on the ground']:,} ({gold['pct_of_weekday_trips']['snow on the ground']:.1f}%). Heat costs "
            f"nothing: days of 30 C or more have {hot.effect_pct:+.0f}% trips against other days of the same month, so "
            "the low ridership of hot days in the temperature curve comes from the season (late summer), not from the "
            "heat itself.")
    return steps, gold, resp


@task("q5.flow_reasons", "Q5.1",
      "The stations of lower Manhattan that gain bikes on weekdays (May 2018 net flows) gain them almost every weekday. "
      "Why? Rank the causes, and say whether the same happens on weekends.",
      {"type": "json", "schema": {"causes": [{"cause": "str", "contribution": "str"}]}},
      depends_on=["q4.flow_stability", "q3.weekly_cycle", "q2.station_net_flow"],
      scoring={"metric": "cause_ranking", "judge": "llm"})
def flow_reasons(e):
    _, nf, _ = net_flow(e)
    g = ",".join(map(str, nf["gains"]))
    sql = f"""WITH x AS (SELECT hour(t.starttime) AS h, t.usertype, t.end_station_id AS sid, 1 AS v FROM trips t
           WHERE {FLOW_DAYS.format(a='2018-05-01', b='2018-06-01')} AND {PUBLIC}
           UNION ALL SELECT hour(t.starttime), t.usertype, t.start_station_id, -1 FROM trips t
           WHERE {FLOW_DAYS.format(a='2018-05-01', b='2018-06-01')} AND {PUBLIC})
SELECT CASE WHEN h BETWEEN 6 AND 10 THEN '1: 6-10 am' WHEN h BETWEEN 11 AND 15 THEN '2: 11 am-3 pm'
            WHEN h BETWEEN 16 AND 19 THEN '3: 4-7 pm' ELSE '4: night' END AS part_of_day, usertype,
  round(sum(v) / 22.0, 1) AS net_gain_per_weekday
FROM x WHERE sid IN ({g}) GROUP BY 1, 2 ORDER BY 1, 2"""
    sql_w = f"""WITH x AS (SELECT CAST(t.starttime AS DATE) AS day, t.end_station_id AS sid, 1 AS v FROM trips t
           WHERE t.starttime >= '2018-05-01' AND t.starttime < '2018-06-01' AND {PUBLIC}
           UNION ALL SELECT CAST(t.starttime AS DATE), t.start_station_id, -1 FROM trips t
           WHERE t.starttime >= '2018-05-01' AND t.starttime < '2018-06-01' AND {PUBLIC})
SELECT dayofweek(day) IN (0, 6) AS weekend, count(DISTINCT day) AS days, round(sum(v) / count(DISTINCT day), 1) AS net_gain_per_day
FROM x WHERE sid IN ({g}) AND day NOT IN (SELECT date FROM holidays) GROUP BY 1 ORDER BY 1"""
    a, w = e.q(sql), e.q(sql_w).set_index("weekend")
    m = a[a.usertype == "Subscriber"].set_index("part_of_day").net_gain_per_weekday
    tot = float(a.net_gain_per_weekday.sum())
    member = float(m.sum())
    passu = float(a[a.usertype == "Customer"].net_gain_per_weekday.sum())
    am, pm = float(m["1: 6-10 am"]), float(m["3: 4-7 pm"])
    rest = member - am - pm
    gold = {"causes": [
        dict(cause="members commuting in: their morning trips (6-10 am) bring bikes to the 15 stations",
             contribution=f"{am:+.0f} bikes a weekday"),
        dict(cause="members' evening trips (4-7 pm) take back less than half of them: many commuters do not ride home",
             contribution=f"{pm:+.0f} bikes a weekday"),
        dict(cause="midday and night trips of members", contribution=f"{rest:+.0f} bikes"),
        dict(cause="pass users", contribution=f"{passu:+.0f} bikes (small)")],
        "net_gain_per_weekday": round(tot, 1), "weekend_net_gain_per_day": float(w.net_gain_per_day[True])}
    steps = [
        step("Gains are consistent across weekdays and are made in the morning", uses="q4.flow_stability"),
        step("Members ride on weekdays, pass users on weekends", uses="q3.weekly_cycle"),
        step("The 15 gaining stations of May 2018", uses="q2.station_net_flow"),
        step("Their net gain per weekday by part of the day (start of the trip) and user type", sql=sql, result=tab(a)),
        step("The same stations on weekdays and on weekends", sql=sql_w, result=tab(w.reset_index())),
    ]
    resp = (f"The 15 stations gain {tot:.0f} bikes a weekday. 1. Members commuting to work: their trips of 6-10 am "
            f"bring {am:+.0f} bikes a day. 2. Their evening trips take back only {abs(pm):.0f}: many commuters do not "
            f"ride home. 3. Member trips at other hours add {rest:+.0f}. 4. Pass users matter little ({passu:+.0f}). On "
            f"weekends, when few members commute, the same stations change by {w.net_gain_per_day[True]:+.0f} bikes a "
            f"day against {w.net_gain_per_day[False]:+.0f} on weekdays: the gain is a weekday commuting effect.")
    return steps, gold, resp


# ====================================================================== Q6 Predict
@task("q6.june_trips", "Q6.1", "Using the trips up to May 2018 only, forecast the number of trips in June 2018. How close "
      "is the forecast to the recorded number?", {"type": "number"},
      depends_on=["q2.monthly_trips", "q3.growth_trend", "q5.growth_2015", "q4.network_steps"],
      scoring={"metric": "ape", "bands": [0.05, 0.1, 0.2]})
def june_trips(e):
    s = monthly(e, "2014-01-01", "2018-05-01")
    ratio = s["2018-03-01":"2018-05-01"].sum() / s["2017-03-01":"2017-05-01"].sum()
    ref = ratio * s["2017-06-01"]
    sql = """SELECT count(*) AS trips_june_2018 FROM trips WHERE year(starttime) = 2018 AND month(starttime) = 6"""
    actual = int(e.v(sql))
    steps = [step("Monthly trips up to May 2018", uses="q2.monthly_trips",
                  result={"2017-06": int(s["2017-06-01"]), "mar_may_2017": int(s["2017-03-01":"2017-05-01"].sum()),
                          "mar_may_2018": int(s["2018-03-01":"2018-05-01"].sum())}),
             step("Most growth comes with new stations, which arrive in steps in late summer or fall",
                  uses="q5.growth_2015"),
             step("The last step was September-October 2017, so March-May 2018 already has those stations and June 2018 "
                  "will have no others", uses="q4.network_steps",
                  sql=f"""SELECT count(*) AS stations_opened_jan_may_2018 FROM stations WHERE first_trip >= '2018-01-01'
  AND first_trip < '2018-06-01' AND NOT {SERVICE.format(c='name')}""",
                  result={"stations_opened_jan_may_2018": int(e.v(f"""SELECT count(*) FROM stations WHERE first_trip >= '2018-01-01'
  AND first_trip < '2018-06-01' AND NOT {SERVICE.format(c='name')}"""))}),
             step("Growth continues year over year: scale June 2017 by the March-May growth", uses="q3.growth_trend",
                  source="calculation", result={"growth_mar_may": round(ratio - 1, 4), "forecast": int(ref)}),
             step("Compare with the trips recorded in June 2018", sql=sql, result={"actual": actual, "error_of_this_forecast": round(ref / actual - 1, 4)})]
    gold = {"value": actual}
    resp = (f"About {ref / 1e6:.2f} million trips: June 2017 had {int(s['2017-06-01']):,} and March-May 2018 ran "
            f"{(ratio - 1) * 100:+.1f}% against 2017. (Recorded: {actual:,}.)")
    return steps, gold, resp


@task("q6.heat_wave", "Q6.2",
      "Saturday June 30 to Tuesday July 3, 2018 was a dry heat wave with maximum temperatures of 33-36 C. From earlier "
      "summers, how much lower should ridership be on such days than on a normal dry day of the same weekday? How large "
      "was the change on each of the four days?", {"type": "json", "schema": {"change_pct_by_day": "dict[date, float]"}},
      depends_on=["q3.temperature", "q5.weather_losses", "q3.weekly_cycle"],
      scoring={"metric": "fields", "fields": {"change_pct_by_day": {"abs_tol": 10.0}}})
def heat_wave(e):
    sql = f"""WITH d AS (SELECT CAST(starttime AS DATE) AS dt, count(*) AS n FROM trips
               WHERE starttime >= '2018-06-01' AND starttime < '2018-07-04' AND {PUBLIC} GROUP BY 1)
SELECT d.dt AS date, dayofweek(d.dt) AS dow, d.n AS trips, w.tmax, w.prcp
FROM d JOIN {WX.format(t='ghcnd')} w ON w.date = d.dt ORDER BY 1"""
    hw = e.q(sql)
    hw["date"] = pd.to_datetime(hw.date)
    heat = hw[(hw.date >= "2018-06-30") & (hw.date <= "2018-07-03")]
    base = hw[(hw.date < "2018-06-30") & (hw.prcp < 2) & (hw.tmax < 32)].groupby("dow").trips.mean()
    rel = heat.trips.values / base.reindex(heat.dow).values - 1
    gold = {"change_pct_by_day": {str(d.date()): round(float(x) * 100, 1) for d, x in zip(heat.date, rel)}}
    hist_sql = f"""WITH d AS (SELECT CAST(starttime AS DATE) AS dt, count(*) AS n FROM trips
               WHERE starttime >= '2016-05-01' AND starttime < '2017-10-01' AND month(starttime) BETWEEN 5 AND 9 AND {PUBLIC} GROUP BY 1),
     w AS {WX.format(t='ghcnd')},
     x AS (SELECT dayofweek(d.dt) AS dow, d.n, w.tmax FROM d JOIN w ON w.date = d.dt
           WHERE w.prcp < 2 AND d.dt NOT IN (SELECT date FROM holidays))
SELECT dow, avg(n) FILTER (WHERE tmax >= 33) AS hot_days_avg, avg(n) FILTER (WHERE tmax BETWEEN 24 AND 31.9) AS warm_days_avg,
  count(*) FILTER (WHERE tmax >= 33) AS hot_days
FROM x GROUP BY 1 ORDER BY 1"""
    hh = e.q(hist_sql)
    hh["change_pct"] = (hh.hot_days_avg / hh.warm_days_avg - 1) * 100
    est = float((hh.change_pct * hh.hot_days).sum() / hh.hot_days.sum())
    steps = [step("Ridership peaks around 27-30 C and falls above 30 C", uses="q3.temperature"),
             step("But days of 30 C or more have about the trips of other days of the same month: the drop in the curve is "
                  "the season, so the heat itself should cost little", uses="q5.weather_losses"),
             step("Weekdays and weekends differ: compare each day with the same weekday", uses="q3.weekly_cycle"),
             step("Expected effect from history: dry summer days at 33 C and above against dry days at 24-32 C, same weekday "
                  "(May-September 2016-2017)", sql=hist_sql, result=tab(hh.round(1)),
                  note=f"Weighted by the number of hot days, about {est:+.0f}% per day."),
             step("Trips recorded in June and early July 2018, with the weather",
                  sql=sql, result=tab(hw.assign(date=hw.date.dt.date.astype(str)))),
             step("Change per day", source="calculation", result=gold["change_pct_by_day"])]
    resp = ("Expect a small drop at most: within the same month, days of 30 C or more have about the trips of other "
            f"days, and comparing hot with warm summer days of the same weekday gives {est:+.0f}%, which also mixes in "
            "the season. Recorded change against a normal dry June day: " + ", ".join(f"{k} {v:+.0f}%" for k, v in gold["change_pct_by_day"].items())
            + ". The larger drops on July 2-3 fall in the Independence Day week, when many commuters are away.")
    return steps, gold, resp


@task("q6.q4_2018", "Q6.1", "Using the trips up to September 2018 only, forecast the number of trips in October to "
      "December 2018. How close is the forecast to the recorded number?", {"type": "number"},
      depends_on=["q2.monthly_trips", "q5.growth_2015", "q4.network_steps"],
      scoring={"metric": "ape", "bands": [0.05, 0.1, 0.2]})
def q4_2018(e):
    s = monthly(e, "2014-01-01", "2018-12-01")
    ratio = s["2018-07-01":"2018-09-01"].sum() / s["2017-07-01":"2017-09-01"].sum()
    naive = ratio * s["2017-10-01":"2017-12-01"].sum()
    sql_o = f"""SELECT year(t.starttime) AS year, count(*) AS trips_jul_sep_at_stations_open_before_jul_2017
FROM trips t JOIN stations s ON s.station_id = t.start_station_id
WHERE month(t.starttime) BETWEEN 7 AND 9 AND year(t.starttime) IN (2017, 2018) AND s.first_trip < DATE '2017-07-01' AND {PUBLIC}
GROUP BY 1 ORDER BY 1"""
    o = e.q(sql_o).set_index("year").iloc[:, 0]
    g_old = o[2018] / o[2017]
    ref, low = naive, g_old * s["2017-10-01":"2017-12-01"].sum()
    sql = """SELECT count(*) AS trips_oct_dec_2018 FROM trips WHERE starttime >= '2018-10-01' AND starttime < '2019-01-01'"""
    actual = int(e.v(sql))
    steps = [step("Monthly trips up to September 2018 (the monthly counts, extended to September)", uses="q2.monthly_trips",
                  sql="""SELECT date_trunc('month', starttime)::DATE AS month, count(*) AS trips FROM trips
WHERE starttime >= '2017-07-01' AND starttime < '2018-10-01' GROUP BY 1 ORDER BY 1""",
                  result={"oct_dec_2017": int(s["2017-10-01":"2017-12-01"].sum()),
                          "jul_sep_2017": int(s["2017-07-01":"2017-09-01"].sum()),
                          "jul_sep_2018": int(s["2018-07-01":"2018-09-01"].sum())}),
             step("Growth comes with new stations", uses="q5.growth_2015"),
             step("Forecast: scale October-December 2017 by the July-September growth", source="calculation",
                  result={"growth_jul_sep": round(ratio - 1, 4), "forecast": int(ref)}),
             step("The fall 2017 step opened stations in September-October 2017: they are in October-December 2017 but not "
                  "in July-September 2017, so part of the July-September growth is already in the base", uses="q4.network_steps"),
             step("Growth of July-September at the stations open before July 2017 gives a lower bound", sql=sql_o,
                  result={**tab(o.reset_index()), "growth_old_stations": round(g_old - 1, 4), "lower_bound": int(low)}),
             step("Compare with the trips recorded in October-December 2018", sql=sql,
                  result={"actual": actual, "error_of_this_forecast": round(ref / actual - 1, 4)})]
    resp = (f"At most about {ref / 1e6:.2f} million trips: October-December 2017 had {int(s['2017-10-01':'2017-12-01'].sum()):,} "
            f"and July-September 2018 ran {(ratio - 1) * 100:+.1f}% against 2017. Part of that growth comes from the stations "
            "of the fall 2017 expansion, which are already in the October-December base; the stations open before July "
            f"2017 grew {(g_old - 1) * 100:+.1f}%, which gives {low / 1e6:.2f} million as a lower bound. (Recorded: {actual:,}.)")
    return steps, {"value": actual}, resp


@task("q6.snowstorm_2018", "Q6.2",
      "A storm with about 20 cm of snow hit New York on Wednesday March 21, 2018. From the snow storms of earlier "
      "winters, how much lower should ridership be that day than on a normal weekday of the month? How large was the "
      "change?", {"type": "json", "schema": {"expected_change_pct": "float", "change_pct": "float"}},
      depends_on=["q4.january_2016", "q4.weather_beyond_temp", "q5.weather_losses"],
      scoring={"metric": "fields", "fields": {"change_pct": {"abs_tol": 10.0}}})
def snowstorm_2018(e):
    sql_d = f"""WITH d AS (SELECT CAST(starttime AS DATE) AS dt, count(*) AS n FROM trips WHERE {PUBLIC} GROUP BY 1),
     cal AS (SELECT range::DATE AS dt FROM range(DATE '2013-07-01', DATE '2019-01-01', INTERVAL 1 DAY)),
     w AS {WX.format(t='ghcnd')}
SELECT cal.dt AS date, coalesce(d.n, 0) AS trips, w.snow, w.snwd, w.prcp FROM cal LEFT JOIN d USING (dt) JOIN w ON w.date = cal.dt
WHERE dayofweek(cal.dt) BETWEEN 1 AND 5 AND cal.dt NOT IN (SELECT date FROM holidays) AND month(cal.dt) IN (12, 1, 2, 3)"""
    a = e.q(sql_d)
    a["date"] = pd.to_datetime(a.date)
    a["ym"] = a.date.dt.to_period("M")
    normal = a[(a.snow == 0) & (a.snwd == 0) & (a.prcp < 2)].groupby("ym").trips.median()
    a["change_pct"] = (a.trips / a.ym.map(normal) - 1) * 100
    hist = a[(a.snow >= 10) & (a.date < "2018-03-21")].copy()
    exp_ = float(hist.change_pct.median())
    day = a[a.date == "2018-03-21"].iloc[0]
    gold = {"expected_change_pct": round(exp_, 1), "change_pct": round(float(day.change_pct), 1)}
    steps = [
        step("Storms can close the system entirely (no trips on the days of the January 2016 blizzard)",
             uses="q4.january_2016"),
        step("Snow lowers ridership beyond the cold", uses="q4.weather_beyond_temp"),
        step("Snow on the ground alone costs about 40% of a weekday's trips", uses="q5.weather_losses"),
        step("Earlier winter weekdays with 10 cm of snow or more, against the median dry weekday without snow of the "
             "same month", sql=sql_d,
             result=tab(hist[["date", "trips", "snow", "change_pct"]].assign(date=hist.date.dt.date.astype(str)).round(1)),
             note=f"Median change {exp_:+.0f}% over {len(hist)} storm days; several are closures (-100%)."),
        step("The recorded change on March 21, 2018", source="calculation",
             result={"trips": int(day.trips), "normal_weekday_march_2018": int(normal[pd.Period('2018-03', 'M')]),
                     "change_pct": gold["change_pct"]}),
    ]
    resp = (f"Earlier snow storms of 10 cm or more cut weekday ridership by a median of {abs(exp_):.0f}% "
            f"({len(hist)} days, some with the system closed), so expect a drop of that size. On March 21, 2018 "
            f"{int(day.trips):,} trips were recorded, {gold['change_pct']:+.0f}% against a normal March 2018 weekday.")
    return steps, gold, resp


@task("q6.frozen_network", "Q6.3",
      "What if no station had opened after 2015? How many of the 2018 trips started and ended at stations that "
      "already existed at the end of 2015, and what share of all 2018 trips is that? Is it a good estimate of the "
      "2018 ridership without the expansion?",
      {"type": "json", "schema": {"trips_old_network": "int", "share_pct": "float"}},
      depends_on=["q5.growth_2015", "q5.growth_2018", "q4.network_steps"],
      scoring={"metric": "fields", "fields": {"share_pct": {"tol": 1.0}}})
def frozen_network(e):
    sql = f"""SELECT count(*) AS trips_2018,
  count(*) FILTER (WHERE s1.first_trip < DATE '2016-01-01' AND s2.first_trip < DATE '2016-01-01') AS both_ends_old,
  count(*) FILTER (WHERE (s1.first_trip < DATE '2016-01-01') <> (s2.first_trip < DATE '2016-01-01')) AS one_end_new,
  count(*) FILTER (WHERE s1.first_trip >= DATE '2016-01-01' AND s2.first_trip >= DATE '2016-01-01') AS both_ends_new
FROM trips t JOIN stations s1 ON s1.station_id = t.start_station_id JOIN stations s2 ON s2.station_id = t.end_station_id
WHERE year(t.starttime) = 2018 AND {PUBLIC}"""
    sql_o = f"""SELECT year(t.starttime) AS year, count(*) AS trips_between_old_stations
FROM trips t JOIN stations s1 ON s1.station_id = t.start_station_id JOIN stations s2 ON s2.station_id = t.end_station_id
WHERE year(t.starttime) IN (2015, 2018) AND s1.first_trip < DATE '2016-01-01' AND s2.first_trip < DATE '2016-01-01' AND {PUBLIC}
GROUP BY 1 ORDER BY 1"""
    a, o = e.q(sql).iloc[0], e.q(sql_o).set_index("year").trips_between_old_stations
    share = float(a.both_ends_old / a.trips_2018 * 100)
    gold = {"trips_old_network": int(a.both_ends_old), "share_pct": round(share, 1)}
    steps = [
        step("Expansions added trips at new stations and at the stations they connect to", uses="q5.growth_2015"),
        step("The stations opened in 2017 or later carried all of the growth of 2018", uses="q5.growth_2018"),
        step("The network of the end of 2015 is the one before the steps of 2016 and 2017", uses="q4.network_steps"),
        step("2018 trips by whether their two ends are stations that existed before 2016", sql=sql, result=tab(e.q(sql))),
        step("Trips between the old stations in 2015 and in 2018", sql=sql_o, result=tab(o.reset_index()),
             note="Ridership between the old stations also grew."),
    ]
    resp = (f"{gold['trips_old_network']:,} trips of 2018 ({share:.1f}%) started and ended at stations that existed at "
            f"the end of 2015; {int(a.one_end_new):,} had one end and {int(a.both_ends_new):,} both ends at newer stations. "
            "It is only a rough estimate: some riders of the new stations would have walked to an old one (so it is too "
            "low), while people who became members because a station opened near them also ride between old stations (so "
            f"it is too high). Trips between the old stations grew from {int(o[2015]):,} in 2015 (all trips of that year) "
            f"to {int(o[2018]):,} in 2018.")
    return steps, gold, resp


@task("q6.dry_2017", "Q6.3",
      "What if no weekday of 2017 had had rain? If each rainy weekday (2 mm or more) had had the average trips of the "
      "dry weekdays of its month, how many more trips would 2017 have had, and what share of the year's trips is that?",
      {"type": "json", "schema": {"extra_trips": "int", "pct_of_2017": "float"}},
      depends_on=["q4.weather_beyond_temp", "q5.weather_losses", "q2.trace_days"],
      scoring={"metric": "fields", "fields": {"pct_of_2017": {"tol": 0.5}}})
def dry_2017(e):
    sql = f"""WITH d AS (SELECT CAST(starttime AS DATE) AS dt, count(*) AS n FROM trips WHERE year(starttime) = 2017 GROUP BY 1),
     w AS {WX.format(t='ghcnd')}
SELECT month(d.dt) AS month, count(*) FILTER (WHERE w.prcp >= 2) AS rainy_days,
  round(avg(d.n) FILTER (WHERE w.prcp < 2)) AS dry_avg, round(avg(d.n) FILTER (WHERE w.prcp >= 2)) AS rainy_avg,
  sum(d.n) FILTER (WHERE w.prcp >= 2) AS rainy_trips
FROM d JOIN w ON w.date = d.dt
WHERE dayofweek(d.dt) BETWEEN 1 AND 5 AND d.dt NOT IN (SELECT date FROM holidays) AND w.snwd = 0
GROUP BY 1 ORDER BY 1"""
    a = e.q(sql)
    a["extra"] = a.rainy_days * a.dry_avg - a.rainy_trips
    tot = int(e.v("SELECT count(*) FROM trips WHERE year(starttime) = 2017"))
    extra = int(a.extra.sum())
    gold = {"extra_trips": extra, "pct_of_2017": round(extra / tot * 100, 2)}
    steps = [
        step("Rain is the weather condition with the largest effect", uses="q4.weather_beyond_temp"),
        step("A trace of rain is 0 mm, so trace days are dry days here", uses="q2.trace_days"),
        step("Per month of 2017: rainy and dry non-holiday weekdays without snow on the ground", sql=sql, result=tab(a)),
        step("Replace each rainy weekday by the dry average of its month", source="calculation",
             result={**gold, "trips_2017": tot}),
        step("Check against the regression estimate of the trips rain cost on 2017 weekdays", uses="q5.weather_losses"),
    ]
    resp = (f"About {extra:,} more trips ({gold['pct_of_2017']:.1f}% of the {tot:,} trips of 2017) from {int(a.rainy_days.sum())} "
            "rainy weekdays. The estimate assumes that riders who stayed home on rainy days did not ride on other days "
            "instead, and leaves weekends and snow days as they were.")
    return steps, gold, resp


@task("q6.flow_june", "Q6.1",
      "Crews refill the 15 stations that lose the most bikes on weekdays (May 2018 net flows). Using May 2018 only, "
      "forecast how many bikes these 15 stations will lose in total per weekday in June 2018 (weekdays without "
      "holidays; a station that gains counts as zero). How close is the forecast to what happened?",
      {"type": "number"}, depends_on=["q2.station_net_flow", "q4.flow_stability", "q5.flow_reasons"],
      scoring={"metric": "ape", "bands": [0.1, 0.2, 0.3]})
def flow_june(e):
    _, nf, _ = net_flow(e)
    loss = list(nf["losses"])
    _, may = station_day_flows(e)
    sql, jun = station_day_flows(e, "2018-06-01", "2018-07-01")
    per = lambda d: d[d.station_id.isin(loss)].groupby("station_id").net.sum() / d.day.nunique()
    pm, pj = per(may), per(jun)
    fc = float((-pm).clip(lower=0).sum())
    actual = float((-pj).clip(lower=0).sum())
    t = pd.DataFrame({"may": pm.round(1), "june": pj.round(1)}).reset_index()
    steps = [
        step("The 15 losing stations and their losses in May", uses="q2.station_net_flow",
             result={"stations": len(loss), "may_loss_rounded": int(sum(nf["losses"].values()))}),
        step("The daily pattern repeats from one weekday to the next", uses="q4.flow_stability"),
        step("It comes from commuting, which does not change from May to June", uses="q5.flow_reasons"),
        step("Forecast: the May weekday average of each station, losses only", source="calculation",
             result={"forecast_bikes_per_weekday": round(fc)}),
        step("Recorded June 2018 net gain per station and weekday", sql=sql, result=tab(t)),
        step("Compare", source="calculation",
             result={"actual": round(actual, 1), "error_of_this_forecast": round(fc / actual - 1, 3)}),
    ]
    resp = (f"About {fc:.0f} bikes per weekday, the May average of the 15 stations: their losses come from commuting, "
            f"which repeats every weekday. (Recorded in June 2018: {actual:.0f} bikes per weekday.)")
    return steps, {"value": round(actual, 1)}, resp


@task("q6.pass_trips_2018", "Q6.1",
      "Using the trips up to May 2018 only, forecast the number of trips by pass users in June to September 2018. How "
      "close is the forecast to the recorded number?", {"type": "number"},
      depends_on=["q2.monthly_trips", "q3.weekly_cycle", "q5.ride_time_july_vs_march"],
      scoring={"metric": "ape", "bands": [0.05, 0.1, 0.2]})
def pass_trips_2018(e):
    sql = """SELECT date_trunc('month', starttime)::DATE AS month, count(*) FILTER (WHERE usertype = 'Customer') AS pass_trips,
  count(*) AS trips FROM trips WHERE starttime >= '2016-03-01' AND starttime < '2018-06-01' GROUP BY 1 ORDER BY 1"""
    a = e.q(sql)
    s = pd.Series(a.pass_trips.values.astype(float), index=pd.to_datetime(a.month))
    share = (a.pass_trips / a.trips).values
    sh = pd.Series(share, index=s.index)
    g17 = s["2017-03-01":"2017-05-01"].sum() / s["2016-03-01":"2016-05-01"].sum()
    g18 = s["2018-03-01":"2018-05-01"].sum() / s["2017-03-01":"2017-05-01"].sum()
    base = s["2017-06-01":"2017-09-01"].sum()
    fc = float(base * g18)
    sql_a = "SELECT count(*) AS pass_trips_jun_sep_2018 FROM trips WHERE usertype = 'Customer' AND starttime >= '2018-06-01' AND starttime < '2018-10-01'"
    actual = int(e.v(sql_a))
    steps = [
        step("Monthly trips by user type", uses="q2.monthly_trips", sql=sql, result=tab(a)),
        step("Pass users ride for leisure, mostly on weekends and in summer", uses="q3.weekly_cycle",
             source="calculation", result={"pass_share_2017": {m: round(float(sh[f"2017-{m:02d}-01"]), 3) for m in (3, 5, 7, 9)}}),
        step("Their share rises from spring to summer, so carry the summer of last year forward, not the spring",
             uses="q5.ride_time_july_vs_march"),
        step("Forecast: June-September 2017 pass trips times the March-May growth of 2018 over 2017", source="calculation",
             result={"jun_sep_2017": int(base), "growth_mar_may_2018": round(g18 - 1, 3),
                     "growth_mar_may_2017": round(g17 - 1, 3), "forecast": int(fc)}),
        step("Compare with the recorded pass trips", sql=sql_a,
             result={"actual": actual, "error_of_this_forecast": round(fc / actual - 1, 3)}),
    ]
    resp = (f"About {fc / 1e6:.2f} million pass trips: June-September 2017 had {int(base):,}, and pass trips of "
            f"March-May 2018 ran {(g18 - 1) * 100:+.0f}% against 2017. (Recorded: {actual:,}.)")
    return steps, {"value": actual}, resp


# ====================================================================== Q7 Prescribe
@task("q7.rebalancing_plan", "Q7.2",
      "Overnight, crews move bikes from the stations that gain bikes to the stations that lose bikes (use the 15 and 15 "
      "stations of the May 2018 net flows). Plan how many bikes to move from each gaining station to each losing station "
      "so that every losing station gets back its average loss, no gaining station gives more than its average gain, and "
      "the total distance carried (bike-kilometers, great-circle distance) is as small as possible.",
      {"type": "json", "schema": {"bike_km": "float", "moves": [{"from_station": "int", "to_station": "int", "bikes": "int"}]}},
      depends_on=["q2.station_net_flow", "q6.flow_june", "q5.flow_reasons"],
      scoring={"metric": "optimality_gap", "field": "bike_km", "tol": 0.02})
def rebalancing(e):
    _, nf, _ = net_flow(e)
    sup, dem = pd.Series(nf["gains"]), pd.Series(nf["losses"])
    st = e.q(f"SELECT station_id, name, latitude, longitude FROM stations WHERE station_id IN ({','.join(map(str, list(sup.index) + list(dem.index)))})").set_index("station_id")
    D = np.array([[hav_km(st.loc[i, ["latitude", "longitude"]].values, st.loc[j, ["latitude", "longitude"]].values)
                   for j in dem.index] for i in sup.index])
    r = solve_transport(sup.values.astype(float), dem.values.astype(float), D)
    flows = r.x.reshape(D.shape)
    moves = [dict(from_station=int(sup.index[i]), to_station=int(dem.index[j]), bikes=int(round(flows[i, j])))
             for i in range(D.shape[0]) for j in range(D.shape[1]) if flows[i, j] > 0.5]
    g = greedy_transport(sup.values.astype(float), dem.values.astype(float), D)
    gold = {"bike_km": round(float(r.fun), 2), "moves": moves, "greedy_bike_km": round(float(g), 2)}
    steps = [step("The gains are made by morning commuters, every weekday: move the bikes back overnight",
                  uses="q5.flow_reasons"),
             step("May flows forecast the next month within about a fifth, so a plan from May holds", uses="q6.flow_june"),
             step("Supply (gains) and demand (losses) per station", uses="q2.station_net_flow",
                  result={"supply_bikes": int(sup.sum()), "demand_bikes": int(dem.sum())}),
             step("Great-circle distances between the 15 x 15 stations", source="calculation",
                  result={"min_km": round(float(D.min()), 2), "max_km": round(float(D.max()), 2)}),
             step("Solve the transportation problem exactly (linear program)", source="calculation",
                  result={"bike_km": gold["bike_km"], "moves": len(moves)}),
             step("Compare with the nearest-station heuristic", source="calculation", result={"greedy_bike_km": gold["greedy_bike_km"]},
                  note="Feasible but longer: the question tests real optimization."),
             step("The optimal moves", source="calculation", result=tab(pd.DataFrame(moves)))]
    big = sorted(moves, key=lambda m: -m["bikes"])[:2]
    resp = (f"Move {int(dem.sum())} bikes with {gold['bike_km']} bike-km in {len(moves)} moves, "
            f"{g - r.fun:.1f} bike-km less than taking each station's bikes from its nearest gaining station "
            f"({gold['greedy_bike_km']}). Largest moves: " + "; ".join(
                f"{m['bikes']} bikes from {st.name[m['from_station']]} to {st.name[m['to_station']]}" for m in big) + ".")
    return steps, gold, resp


@task("q7.new_station_zones", "Q7.2",
      "We can build 20 new stations in taxi zones of Manhattan, Brooklyn and Queens that have no station today (airports "
      "excluded). A zone needs one station per 0.35 square kilometers, rounded up. Choose the zones that maximize the "
      "short taxi trips (under 2 miles) per day that start there (2017 average), with at least one zone in Brooklyn and "
      "one in Queens.", {"type": "json", "schema": {"zone_ids": "list[int]", "short_trips_per_day": "float"}},
      depends_on=["q4.taxi_and_bikes", "q6.frozen_network", "q4.queens_shift"],
      scoring={"metric": "optimality_gap", "field": "short_trips_per_day", "tol": 0.01})
def new_zones(e):
    from scipy.optimize import Bounds, LinearConstraint, milp
    sql = """WITH sz AS (SELECT DISTINCT taxi_zone_id FROM stations WHERE last_trip >= '2018-05-01'),
     tx AS (SELECT zone_id, sum(short_trips) / 365.0 AS short_per_day FROM taxi_daily WHERE year(date) = 2017 GROUP BY 1)
SELECT z.zone_id, z.zone, z.borough, tx.short_per_day,
  ST_Area(ST_Transform(ST_GeomFromText(z.zone_geom), 'EPSG:4326', 'EPSG:2263', true)) * 0.09290304 / 1e6 AS km2
FROM taxi_zones z JOIN tx USING (zone_id)
WHERE z.zone_id NOT IN (SELECT taxi_zone_id FROM sz WHERE taxi_zone_id IS NOT NULL)
  AND z.borough IN ('Manhattan', 'Brooklyn', 'Queens') AND z.service_zone <> 'Airports' AND tx.short_per_day >= 20
ORDER BY short_per_day DESC"""
    z = e.q(sql)
    z["stations_needed"] = np.maximum(1, np.ceil(z.km2 / 0.35)).astype(int)
    cons = [LinearConstraint(z.stations_needed.values[None, :], 0, 20)]
    for b in ("Brooklyn", "Queens"):
        cons.append(LinearConstraint((z.borough == b).astype(float).values[None, :], 1, np.inf))
    res = milp(-z.short_per_day.values, constraints=cons, integrality=np.ones(len(z)), bounds=Bounds(0, 1))
    pick = z[res.x > 0.5]
    gold = {"zone_ids": sorted(int(x) for x in pick.zone_id), "short_trips_per_day": round(float(pick.short_per_day.sum()), 1),
            "stations_used": int(pick.stations_needed.sum())}
    steps = [step("Bike departures rise with short taxi trips across zones: short taxi trips measure demand",
                  uses="q4.taxi_and_bikes"),
             step("Without the stations opened after 2015, 2018 would have lost a large part of its trips: new stations "
                  "bring new riders", uses="q6.frozen_network"),
             step("The Queens stations of 2017 found riders without taking them from older stations", uses="q4.queens_shift"),
             step("Candidate zones without a station, with 2017 short taxi trips per day and area", sql=sql,
                  result=tab(z.round(2), limit=15)),
             step("Stations each zone needs (one per 0.35 km2, rounded up)", source="calculation",
                  result={"candidates": len(z), "budget": 20}),
             step("Choose zones with a 0-1 integer program (knapsack with borough constraints)", source="calculation",
                  result=tab(pick[["zone_id", "zone", "borough", "short_per_day", "stations_needed"]].round(1)))]
    resp = (f"Build in {len(pick)} zones using {gold['stations_used']} stations: "
            + ", ".join(f"{r.zone} ({r.borough})" for r in pick.itertuples())
            + f". Together they start {gold['short_trips_per_day']:,.0f} short taxi trips a day.")
    return steps, gold, resp


@task("q7.member_conversion", "Q7.1",
      "We want more pass users to become annual members. Where and when do pass users ride, and what should we do? "
      "Support each suggestion with numbers.", TEXT,
      depends_on=["q3.weekly_cycle", "q3.duration_distribution", "q6.pass_trips_2018"])
def member_conversion(e):
    sql = f"""SELECT count(*) AS pass_trips,
  round(avg((dayofweek(starttime) IN (0, 6))::INT) * 100, 1) AS pct_weekend,
  round(avg((month(starttime) BETWEEN 6 AND 9)::INT) * 100, 1) AS pct_jun_sep,
  round(avg((start_station_id = end_station_id)::INT) * 100, 1) AS pct_round_trips,
  round(median(tripduration) / 60, 1) AS median_min
FROM trips WHERE year(starttime) = 2017 AND usertype = 'Customer' AND {PUBLIC}"""
    sql_s = f"""SELECT s.name, s.borough, count(*) FILTER (WHERE t.usertype = 'Customer') AS pass_trips,
  round(avg((t.usertype = 'Customer')::INT) * 100, 1) AS pct_pass
FROM trips t JOIN stations s ON s.station_id = t.start_station_id
WHERE year(t.starttime) = 2017 AND {PUBLIC} GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10"""
    sql_c = f"""WITH x AS (SELECT s.station_id, count(*) FILTER (WHERE t.usertype = 'Customer') AS p
           FROM trips t JOIN stations s ON s.station_id = t.start_station_id
           WHERE year(t.starttime) = 2017 AND {PUBLIC} GROUP BY 1)
SELECT count(*) AS stations, round(sum(p) FILTER (WHERE rk <= 30) / sum(p) * 100, 1) AS pct_pass_at_top_30
FROM (SELECT p, row_number() OVER (ORDER BY p DESC) AS rk FROM x)"""
    a, st, c = e.q(sql).iloc[0], e.q(sql_s), e.q(sql_c).iloc[0]
    steps = [
        step("Pass users ride on weekends", uses="q3.weekly_cycle"),
        step("Pass users ride much longer than members", uses="q3.duration_distribution"),
        step("About a million pass trips are expected in June-September", uses="q6.pass_trips_2018"),
        step("When and how pass users ride (2017)", sql=sql, result=tab(e.q(sql))),
        step("Stations with the most pass user departures", sql=sql_s, result=tab(st)),
        step("How concentrated pass use is", sql=sql_c, result=tab(e.q(sql_c))),
    ]
    top = ", ".join(st.name.head(4))
    gold = {"key_facts": [
        f"Target where pass users are: the 30 busiest pass stations of {int(c.stations)} take {c.pct_pass_at_top_30}% of "
        f"pass departures, led by {top}.",
        f"Time offers to weekends and summer: {a.pct_weekend}% of pass trips are on weekends and {a.pct_jun_sep}% in "
        "June-September.",
        f"Pass users ride for leisure (median {a.median_min} minutes, {a.pct_round_trips}% round trips); a membership "
        "offer should stress weekend and leisure use, not only commuting.",
        "Many pass users are visitors who cannot become members; measure conversion among repeat pass users, which the "
        "trip records cannot identify."], "required": [0, 1, 2]}
    return steps, gold, " ".join(gold["key_facts"])


@task("q7.winter_ridership", "Q7.1",
      "Ridership falls sharply in winter. What should the operator do in December to March? Support each suggestion "
      "with numbers from the records.", TEXT,
      depends_on=["q4.weather_beyond_temp", "q3.weekly_cycle", "q6.snowstorm_2018", "q5.weather_losses"])
def winter_ridership(e):
    sql = f"""WITH d AS (SELECT CAST(starttime AS DATE) AS dt, usertype, count(*) AS n FROM trips
           WHERE starttime >= '2016-12-01' AND starttime < '2017-10-01' AND usertype IS NOT NULL AND {PUBLIC} GROUP BY 1, 2)
SELECT usertype, round(avg(n) FILTER (WHERE month(dt) IN (12, 1, 2))) AS per_day_dec_feb,
  round(avg(n) FILTER (WHERE month(dt) IN (6, 7, 8))) AS per_day_jun_aug
FROM d GROUP BY 1 ORDER BY 1"""
    sql_r = f"""WITH d AS (SELECT CAST(starttime AS DATE) AS dt, count(*) AS n FROM trips
           WHERE starttime >= '2017-03-01' AND starttime < '2017-04-01' AND {PUBLIC} GROUP BY 1),
     cal AS (SELECT range::DATE AS dt FROM range(DATE '2017-03-09', DATE '2017-03-24', INTERVAL 1 DAY)),
     w AS {WX.format(t='ghcnd')}
SELECT cal.dt AS date, dayname(cal.dt) AS weekday, coalesce(d.n, 0) AS trips, w.snow, w.snwd
FROM cal LEFT JOIN d USING (dt) JOIN w ON w.date = cal.dt ORDER BY 1"""
    a = e.q(sql).set_index("usertype")
    r = e.q(sql_r)
    keep = a.per_day_dec_feb / a.per_day_jun_aug
    sh = a.per_day_dec_feb["Subscriber"] / a.per_day_dec_feb.sum()
    tot_ratio = a.per_day_jun_aug.sum() / a.per_day_dec_feb.sum()
    steps = [
        step("Snow and rain cut ridership beyond the cold", uses="q4.weather_beyond_temp"),
        step("Cold days and snow on the ground cost weekday trips; rain costs more", uses="q5.weather_losses"),
        step("A 20 cm storm cuts a weekday's trips far below normal, as earlier storms predicted", uses="q6.snowstorm_2018"),
        step("Trips per day in winter (December 2016-February 2017) and summer 2017, by user type", sql=sql,
             result=tab(a.reset_index())),
        step("Ridership around the March 14, 2017 snow storm", sql=sql_r, result=tab(r),
             note="Three days without trips, then several days far below normal while snow stays on the ground."),
        step("Members commute on weekdays", uses="q3.weekly_cycle"),
    ]
    gold = {"key_facts": [
        f"Winter ridership is members: they keep {keep['Subscriber'] * 100:.0f}% of their summer trips per day, pass "
        f"users only {keep['Customer'] * 100:.0f}%, so members make {sh * 100:.0f}% of winter trips; keep the commuter "
        "network (weekday stations near offices and transit) fully served and target winter offers at members.",
        "Clear snow at stations quickly and reopen fast: after the March 14, 2017 storm the system had no trips for "
        "three days and stayed far below normal for days after.",
        f"Use the low season for maintenance and reduce rebalancing: a summer day has {tot_ratio:.1f} times the trips "
        "of a winter day.",
        "Pass-user promotions in winter will have little effect; focus them on spring."], "required": [0, 1]}
    return steps, gold, " ".join(gold["key_facts"])


# ====================================================================== build
TABLES = ["trips", "stations", "station_names", "ghcnd", "gsod", "zip_codes", "zip_neighborhoods", "taxi_zones",
          "taxi_daily", "holidays", "weather_stations"]


def provenance(steps):
    used = set()
    for s in steps:
        for t in TABLES:
            if s.get("sql") and re.search(rf"\b{t}\b", s["sql"]):
                used.add(t)
    out = [dict(table=t, **SOURCES[t]) for t in sorted(used) if t in SOURCES]
    for s in steps:
        if s.get("doc"):
            out.append(dict(document=s["doc"]["document"], url=s["doc"]["url"]))
        if s.get("source") == "benchmark":
            out.append(dict(benchmark="Spider 2.0", instance=s["result"].get("spider_instance") if isinstance(s["result"], dict) else None))
    return out


def build(build=OUT):
    e = Env(build)
    out = []
    for t in TASKS:
        steps, gold, resp = t["fn"](e)
        lv = t["task_type"].split(".")[0]
        out.append(dict(id=t["id"], level=lv, level_name=LEVEL_NAMES[lv], task_type=t["task_type"],
                        task_type_name=TASK_TYPES[t["task_type"]], question=t["question"], answer_format=t["answer_format"],
                        depends_on=t["depends_on"], control=t["control"], planted=False, steps=steps, gold=gold,
                        target_response=resp, scoring=t["scoring"], provenance=provenance(steps)))
    byid = {x["id"]: x for x in out}
    for x in out:                       # a task also rests on the sources of the tasks it builds on
        seen, stack = [], list(x["depends_on"])
        while stack:
            d = stack.pop()
            if d not in seen:
                seen.append(d)
                stack += byid[d]["depends_on"]
        have = {json.dumps(p, sort_keys=True) for p in x["provenance"]}
        for d in seen:
            for p in byid[d]["provenance"]:
                k = json.dumps(p, sort_keys=True)
                if k not in have:
                    have.add(k)
                    x["provenance"].append(p)
    ids = {x["id"] for x in out}
    for x in out:
        assert set(x["depends_on"]) <= ids, x["id"]
        x["used_by"] = [y["id"] for y in out if x["id"] in y["depends_on"]]
    spectrum.check(out)
    (build / "tasks.json").write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str) + "\n")
    return out


if __name__ == "__main__":
    items = build()
    for x in items:
        print(f"{x['id']:28s} {x['task_type']:6s} steps={len(x['steps'])} deps={x['depends_on']}")
