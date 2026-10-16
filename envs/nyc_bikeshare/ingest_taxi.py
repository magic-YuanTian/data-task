"""Daily taxi pickups by TLC taxi zone, 2014-2018, from the NYC TLC trip record files (public).

Only daily aggregates per pickup zone are kept: trips, trips under 2 miles, mean distance and fare (yellow, green)
and trips (for-hire vehicles, which report no distance). Usage: python -m envs.nyc_bikeshare.ingest_taxi"""
from __future__ import annotations

from pathlib import Path

import duckdb

SRC = Path(__file__).resolve().parents[2] / "sources" / "nyc"
OUT = SRC / "taxi"
BASE = "https://d37ci6vzurychx.cloudfront.net/trip-data/{kind}_tripdata_{y}-{m:02d}.parquet"
PICK = {"yellow": "tpep_pickup_datetime", "green": "lpep_pickup_datetime", "fhv": "pickup_datetime"}


def month(con, kind, y, m):
    out = OUT / f"{kind}_{y}{m:02d}.parquet"
    if out.exists():
        return
    url = BASE.format(kind=kind, y=y, m=m)
    t = PICK[kind]
    if kind == "fhv":
        cols = con.execute(f"DESCRIBE SELECT * FROM '{url}'").df().column_name.tolist()
        pu = next(c for c in cols if c.lower() == "pulocationid")
        sql = (f"SELECT CAST({t} AS DATE) AS day, '{kind}' AS service, CAST({pu} AS INTEGER) AS zone_id, "
               f"count(*) AS trips, NULL::BIGINT AS short_trips, NULL::DOUBLE AS mean_distance, "
               f"NULL::DOUBLE AS mean_fare FROM '{url}' GROUP BY ALL")
    else:
        sql = (f"SELECT CAST({t} AS DATE) AS day, '{kind}' AS service, CAST(PULocationID AS INTEGER) AS zone_id, "
               f"count(*) AS trips, count(*) FILTER (WHERE trip_distance > 0 AND trip_distance < 2) AS short_trips, "
               f"avg(trip_distance) FILTER (WHERE trip_distance > 0 AND trip_distance < 100) AS mean_distance, "
               f"avg(fare_amount) FILTER (WHERE fare_amount > 0 AND fare_amount < 500) AS mean_fare "
               f"FROM '{url}' GROUP BY ALL")
    con.execute(f"COPY ({sql}) TO '{out}' (FORMAT parquet)")
    print(kind, y, m, con.execute(f"SELECT sum(trips) FROM '{out}'").fetchone()[0], flush=True)


def main(kinds=("yellow", "green")):
    """Usage: python -m envs.nyc_bikeshare.ingest_taxi [yellow,green]. The TLC CDN throttles bursts, so requests are
    spaced out; for-hire vehicle files are not used by the world."""
    import sys
    import time
    if len(sys.argv) > 1:
        kinds = sys.argv[1].split(",")
    OUT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; SET threads=8")
    for kind in kinds:
        for y in range(2014, 2019):
            if kind == "fhv" and y < 2015:
                continue
            for m in range(1, 13):
                try:
                    month(con, kind, y, m)
                    time.sleep(2)
                except Exception as e:  # noqa: BLE001
                    print("FAILED", kind, y, m, str(e)[:200], flush=True)


if __name__ == "__main__":
    main()
