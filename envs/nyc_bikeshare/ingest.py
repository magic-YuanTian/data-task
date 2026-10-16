"""Convert the NYC bike share trip archives to monthly parquet files.

Trips: the NYC bike share system data (monthly CSVs, Lyft Bikes and Scooters, LLC), July 2013 - December 2018.
Weather: NOAA GHCN-Daily and Global Summary of the Day for Central Park, LaGuardia and JFK.
Columns of the trip table follow the BigQuery public table new_york_citibike.citibike_trips, so that Spider 2.0
queries run unchanged. Usage: python -m envs.nyc_bikeshare.ingest [--keep-zip]"""
from __future__ import annotations

import re
import subprocess
import sys
import zipfile
from pathlib import Path

import duckdb

SRC = Path(__file__).resolve().parents[2] / "sources" / "nyc"
RAW, TRIPS, TMP = SRC / "raw", SRC / "trips", SRC / "tmp"
YEARS = range(2013, 2019)
S3 = "https://s3.amazonaws.com/tripdata/{y}-citibike-tripdata.zip"
COLS = ["tripduration", "starttime", "stoptime", "start_station_id", "start_station_name", "start_station_latitude",
        "start_station_longitude", "end_station_id", "end_station_name", "end_station_latitude",
        "end_station_longitude", "bikeid", "usertype", "birth_year", "gender"]
TS = ("coalesce(try_strptime({c}, '%Y-%m-%d %H:%M:%S.%f'), try_strptime({c}, '%Y-%m-%d %H:%M:%S'), "
      "try_strptime({c}, '%m/%d/%Y %H:%M:%S'), try_strptime({c}, '%m/%d/%Y %H:%M'))")
INT = "try_cast(try_cast(nullif(nullif(trim({c}), '\\N'), 'NULL') AS DOUBLE) AS BIGINT)"
DBL = "try_cast(nullif(nullif(trim({c}), '\\N'), 'NULL') AS DOUBLE)"
STR = "nullif(nullif(trim({c}), '\\N'), 'NULL')"


def month_files(z: zipfile.ZipFile, y: int) -> dict[int, list[str]]:
    """The monthly CSVs in month folders (the archives also hold older duplicates at the root)."""
    out: dict[int, list[str]] = {}
    for n in z.namelist():
        m = re.match(rf"{y}-citibike-tripdata/(\d+)_[A-Za-z]+/{y}(\d\d)-citibike-tripdata.*\.csv$", n)
        if m and "__MACOSX" not in n:
            out.setdefault(int(m.group(2)), []).append(n)
    return out


def select_sql(path: str) -> str:
    raw = [f"column{i:02d}" for i in range(15)]
    exprs = [
        INT.format(c=raw[0]), TS.format(c=raw[1]), TS.format(c=raw[2]), INT.format(c=raw[3]), STR.format(c=raw[4]),
        DBL.format(c=raw[5]), DBL.format(c=raw[6]), INT.format(c=raw[7]), STR.format(c=raw[8]),
        DBL.format(c=raw[9]), DBL.format(c=raw[10]), INT.format(c=raw[11]), STR.format(c=raw[12]),
        INT.format(c=raw[13]),
        f"CASE trim({raw[14]}) WHEN '1' THEN 'male' WHEN '2' THEN 'female' ELSE 'unknown' END",
    ]
    sel = ", ".join(f"{e} AS {c}" for e, c in zip(exprs, COLS))
    return (f"SELECT {sel} FROM read_csv('{path}', header=true, all_varchar=true, quote='\"', "
            f"columns={{{', '.join(repr(r) + ': ' + repr('VARCHAR') for r in raw)}}}, skip=0)")


def ingest_year(con, y: int, keep_zip: bool):
    zp = RAW / f"{y}-citibike-tripdata.zip"
    if not zp.exists():
        subprocess.run(["curl", "-sS", "--retry", "3", "-o", str(zp), S3.format(y=y)], check=True)
    with zipfile.ZipFile(zp) as z:
        files = month_files(z, y)
        for mo, names in sorted(files.items()):
            if (y, mo) < (2013, 7):
                continue
            out = TRIPS / f"{y}{mo:02d}.parquet"
            if out.exists():
                continue
            TMP.mkdir(parents=True, exist_ok=True)
            parts = []
            for n in sorted(names):
                z.extract(n, TMP)
                parts.append(str(TMP / n))
            union = " UNION ALL ".join(select_sql(p) for p in parts)
            con.execute(f"COPY ({union}) TO '{out}' (FORMAT parquet, COMPRESSION zstd)")
            bad = con.execute(f"SELECT count(*) FILTER (WHERE starttime IS NULL OR stoptime IS NULL), count(*) "
                              f"FROM '{out}'").fetchone()
            print(y, mo, len(parts), "files", bad[1], "rows", bad[0], "bad timestamps", flush=True)
            for p in parts:
                Path(p).unlink()
    if not keep_zip:
        zp.unlink()


def main():
    TRIPS.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET threads=16")
    for y in YEARS:
        ingest_year(con, y, keep_zip="--keep-zip" in sys.argv)


if __name__ == "__main__":
    main()
