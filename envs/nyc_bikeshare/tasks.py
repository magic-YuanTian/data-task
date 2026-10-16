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

from datataskbench import stats as S
from datataskbench.db import ordered
from datataskbench.levels import LEVEL_NAMES, TASK_TYPES
from datataskbench.paths import repo_path

from .build import CFG, DOCUMENTS, OUT, SOURCES
from .common import PUBLIC, WX, greedy_transport, hav_km, scan, solve_transport, spider_match

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
    src = source or ("sub-task" if uses else "db" if sql else "document" if doc else "calculation")
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
      "How many trips were made each month from January 2014 to December 2018, in total and by user type?",
      {"type": "table", "columns": ["month", "trips", "members", "pass_users"]}, depends_on=["q1.usertype"],
      scoring={"metric": "table_match", "compare_columns": [0, 1, 2, 3]})
def monthly_trips(e):
    sql = """SELECT date_trunc('month', starttime)::DATE AS month, count(*) AS trips,
  count(*) FILTER (WHERE usertype = 'Subscriber') AS members,
  count(*) FILTER (WHERE usertype = 'Customer') AS pass_users
FROM trips WHERE starttime >= '2014-01-01' GROUP BY 1 ORDER BY 1"""
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
    steps = [step("List the rides of bike 25536 on September 27, 2016", sql=sql1, result=tab(a),
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
WHERE s.borough = 'Manhattan' AND s.latitude < 40.745 AND t.starttime >= '2018-05-01'
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
                AND dayofweek(starttime) BETWEEN 1 AND 5 AND CAST(starttime AS DATE) NOT IN (SELECT date FROM holidays)""")
    steps = [
        step("Count the weekdays of May 2018 without the holiday", source="db", result={"weekdays": int(nd)},
             sql="""SELECT count(DISTINCT CAST(starttime AS DATE)) AS weekdays FROM trips WHERE starttime >= '2018-05-01'
  AND dayofweek(starttime) BETWEEN 1 AND 5 AND CAST(starttime AS DATE) NOT IN (SELECT date FROM holidays)"""),
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
             step("Quantiles of ride time in minutes, overall and by user type", sql=sql, result=tab(a.reset_index())),
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


# ====================================================================== Q4 Discover
@task("q4.january_2016", "Q4.1",
      "Were there any unusual days in daily ridership in January 2016? For each, say what happened.",
      {"type": "json", "schema": {"anomalies": [{"dates": "list[date]", "explanation": "str"}]}},
      depends_on=["q2.daily_jan2016", "q1.weather_units"], scoring={"metric": "event_f1", "date_tol_days": 1})
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
      depends_on=["q1.birth_year_1969"])
def birth_year_shift(e):
    sql = """SELECT date_trunc('month', starttime)::DATE AS month, round(avg((birth_year = 1969)::INT) * 100, 2) AS pct_1969,
  round(avg((birth_year IS NULL)::INT) * 100, 2) AS pct_missing, round(avg((usertype = 'Customer')::INT) * 100, 2) AS pct_pass
FROM trips WHERE starttime >= '2017-07-01' GROUP BY 1 ORDER BY 1"""
    a = e.q(sql)
    d17, j18 = a[a.month.astype(str).str.startswith("2017-12")].iloc[0], a[a.month.astype(str).str.startswith("2018-01")].iloc[0]
    tb = pd.DataFrame({"y": [d17.pct_1969, j18.pct_1969]})
    steps = [step("Recall that 1969 is a placeholder from 2018", uses="q1.birth_year_1969"),
             step("Monthly shares of 1969, of missing birth years and of pass users", sql=sql, result=tab(a)),
             step("Compare December 2017 with January 2018", source="calculation",
                  result={"pct_1969": [d17.pct_1969, j18.pct_1969], "pct_missing": [d17.pct_missing, j18.pct_missing],
                          "pct_pass_users": [d17.pct_pass, j18.pct_pass]})]
    gold = {"key_facts": [
        f"Yes, abruptly in January 2018: missing birth years drop from {d17.pct_missing}% to {j18.pct_missing}% and "
        f"1969 jumps from {d17.pct_1969}% to {j18.pct_1969}% of trips.",
        "It is a change in recording (1969 fills missing values), not in riders: the share of pass users, who give no "
        f"birth year, changes little ({d17.pct_pass}% to {j18.pct_pass}%)."], "required": [0, 1]}
    return steps, gold, " ".join(gold["key_facts"])


# ====================================================================== Q5 Diagnose
@task("q5.growth_2015", "Q5.2",
      "Trips in August and September 2015 were much higher than in August and September 2014. How much of the increase "
      "came from new stations and how much from stations that already existed?",
      {"type": "json", "schema": {"new_stations_pct": "float", "existing_stations_pct": "float"}},
      depends_on=["q3.growth_trend"], scoring={"metric": "fields", "fields": {"new_stations_pct": {"tol": 5}}})
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
             step("Split August-September trips by whether the start station opened after July 15, 2015", sql=sql, result=tab(a)),
             step("Attribute the increase", source="calculation", result=gold)]
    resp = (f"Trips rose by {gr:,} ({gr / tot[2014] * 100:.0f}%). {gold['new_stations_pct']:.0f}% of the increase came from "
            f"the {nst} stations opened in the summer 2015 expansion ({new:,} trips), and {gold['existing_stations_pct']:.0f}% "
            f"from existing stations ({(o15 / o14 - 1) * 100:+.0f}% there).")
    return steps, gold, resp


@task("q5.ride_time_july_vs_march", "Q5.1",
      "Average ride time (rides up to 3 hours) was much higher in July 2016 than in March 2016. What explains the "
      "increase? Rank the causes.", {"type": "json", "schema": {"causes": [{"cause": "str", "contribution": "str"}]}},
      depends_on=["q3.duration_distribution", "q1.usertype"], scoring={"metric": "cause_ranking", "judge": "llm"})
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
    steps = [step("Pass users ride much longer than members", uses="q3.duration_distribution"),
             step("Rides and average ride time by month and user type", sql=sql, result=tab(sh)),
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
      depends_on=["q4.birth_year_shift"], control="decoy",
      scoring={"metric": "fields+explanation", "fields": ["riders_older"], "judge": "llm"})
def rider_age(e):
    sql = """SELECT year(starttime) AS year,
  avg(year(starttime) - birth_year) FILTER (WHERE birth_year IS NOT NULL) AS avg_age_dashboard,
  avg(year(starttime) - birth_year) FILTER (WHERE usertype = 'Subscriber' AND birth_year <> 1969 AND birth_year >= 1920) AS avg_age_members_clean
FROM trips WHERE month(starttime) BETWEEN 1 AND 5 AND year(starttime) IN (2017, 2018) GROUP BY 1 ORDER BY 1"""
    a = e.q(sql).set_index("year")
    gold = {"riders_older": False, "avg_age_dashboard": {int(k): round(x, 2) for k, x in a.avg_age_dashboard.items()},
            "avg_age_members_clean": {int(k): round(x, 2) for k, x in a.avg_age_members_clean.items()}}
    steps = [step("Recall the recording change of January 2018 (1969 fills missing birth years)", uses="q4.birth_year_shift"),
             step("Average age as the dashboard computes it, and for members without placeholders", sql=sql,
                  result=tab(a.reset_index()))]
    resp = (f"No. The dashboard's age rose from {a.avg_age_dashboard[2017]:.1f} to {a.avg_age_dashboard[2018]:.1f} because "
            "from January 2018 missing birth years are recorded as 1969, which adds many riders aged 48-49. For members, "
            f"without the 1969 placeholder and implausible years, the average age is {a.avg_age_members_clean[2017]:.1f} in "
            f"2017 and {a.avg_age_members_clean[2018]:.1f} in 2018.")
    return steps, gold, resp


# ====================================================================== Q6 Predict
@task("q6.june_trips", "Q6.1", "Using the trips up to May 2018 only, forecast the number of trips in June 2018. How close "
      "is the forecast to the recorded number?", {"type": "number"},
      depends_on=["q2.monthly_trips", "q3.growth_trend"], scoring={"metric": "ape", "bands": [0.05, 0.1, 0.2]})
def june_trips(e):
    s = monthly(e, "2014-01-01", "2018-05-01")
    ratio = s["2018-03-01":"2018-05-01"].sum() / s["2017-03-01":"2017-05-01"].sum()
    ref = ratio * s["2017-06-01"]
    sql = """SELECT count(*) AS trips_june_2018 FROM trips WHERE year(starttime) = 2018 AND month(starttime) = 6"""
    actual = int(e.v(sql))
    steps = [step("Monthly trips up to May 2018", uses="q2.monthly_trips",
                  result={"2017-06": int(s["2017-06-01"]), "mar_may_2017": int(s["2017-03-01":"2017-05-01"].sum()),
                          "mar_may_2018": int(s["2018-03-01":"2018-05-01"].sum())}),
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
      depends_on=["q3.temperature"], scoring={"metric": "fields", "fields": {"change_pct_by_day": {"abs_tol": 10.0}}})
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
             step("Expected effect from history: dry summer days at 33 C and above against dry days at 24-32 C, same weekday "
                  "(May-September 2016-2017)", sql=hist_sql, result=tab(hh.round(1)),
                  note=f"Weighted by the number of hot days, about {est:+.0f}% per day."),
             step("Trips recorded in June and early July 2018, with the weather",
                  sql=sql, result=tab(hw.assign(date=hw.date.dt.date.astype(str)))),
             step("Change per day", source="calculation", result=gold["change_pct_by_day"])]
    resp = (f"Earlier summers suggest about {est:+.0f}% on such hot days. Recorded change against a normal dry June day: " + ", ".join(f"{k} {v:+.0f}%" for k, v in gold["change_pct_by_day"].items())
            + ". Heat above 30 C lowers ridership, and July 2-3 also fall in the Independence Day week.")
    return steps, gold, resp


# ====================================================================== Q7 Prescribe
@task("q7.rebalancing_plan", "Q7.2",
      "Overnight, crews move bikes from the stations that gain bikes to the stations that lose bikes (use the 15 and 15 "
      "stations of the May 2018 net flows). Plan how many bikes to move from each gaining station to each losing station "
      "so that every losing station gets back its average loss, no gaining station gives more than its average gain, and "
      "the total distance carried (bike-kilometers, great-circle distance) is as small as possible.",
      {"type": "json", "schema": {"bike_km": "float", "moves": [{"from_station": "int", "to_station": "int", "bikes": "int"}]}},
      depends_on=["q2.station_net_flow"], scoring={"metric": "optimality_gap", "field": "bike_km", "tol": 0.02})
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
    steps = [step("Supply (gains) and demand (losses) per station", uses="q2.station_net_flow",
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
    steps = [step("Candidate zones without a station, with 2017 short taxi trips per day and area", sql=sql,
                  result=tab(z.round(2), limit=15)),
             step("Stations each zone needs (one per 0.35 km2, rounded up)", source="calculation",
                  result={"candidates": len(z), "budget": 20}),
             step("Choose zones with a 0-1 integer program (knapsack with borough constraints)", source="calculation",
                  result=tab(pick[["zone_id", "zone", "borough", "short_per_day", "stations_needed"]].round(1)))]
    resp = (f"Build in {len(pick)} zones using {gold['stations_used']} stations: "
            + ", ".join(f"{r.zone} ({r.borough})" for r in pick.itertuples())
            + f". Together they start {gold['short_trips_per_day']:,.0f} short taxi trips a day.")
    return steps, gold, resp


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
    (build / "tasks.json").write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str) + "\n")
    return out


if __name__ == "__main__":
    items = build()
    for x in items:
        print(f"{x['id']:28s} {x['task_type']:6s} steps={len(x['steps'])} deps={x['depends_on']}")
