"""Build the NYC bike share environment from real records only.

    python -m envs.nyc_bikeshare.build        # writes out/nyc_bikeshare/world.duckdb and catalog/

Every table comes from a public source (SOURCES). The only change to the trip records is the operator name in station
names, which the data license requires, and the gender code written as text; both are listed in SOURCES.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import duckdb
import pandas as pd
import yaml

from datataskbench.paths import OUT as OUT_ROOT
from datataskbench.paths import repo_path

from .common import d, federal_holidays, hav_sql

HERE = Path(__file__).parent
CFG = yaml.safe_load(open(HERE / "config.yaml"))
OUT = OUT_ROOT / "nyc_bikeshare"
TRIP_COLS = ["tripduration", "starttime", "stoptime", "start_station_id", "start_station_name",
             "start_station_latitude", "start_station_longitude", "end_station_id", "end_station_name",
             "end_station_latitude", "end_station_longitude", "bikeid", "usertype", "birth_year", "gender"]
SOURCES = {
    "trips": dict(source="NYC bike share trip histories (Lyft Bikes and Scooters, LLC), monthly files 2013-07 to 2018-12",
                  url="https://s3.amazonaws.com/tripdata/index.html",
                  benchmark="Spider 2.0: its NYC bike share table holds the same records through May 2018 (bq202, sf_bq050, sf_bq358, sf_bq426)",
                  modified="operator name in station names replaced (license); gender coded as text"),
    "stations": dict(source="derived from the trip records; ZIP code area and taxi zone by spatial join; docks from the public station feed",
                     url="https://gbfs.citibikenyc.com/gbfs/en/station_information.json",
                     modified="dock counts are those of the 2026 feed, matched by name and position (the 2018 counts are not public)"),
    "station_names": dict(source="derived from the trip records"),
    "ghcnd": dict(source="NOAA GHCN-Daily, stations Central Park, LaGuardia, JFK",
                  url="https://www.ncei.noaa.gov/data/global-historical-climatology-network-daily/access/"),
    "gsod": dict(source="NOAA Global Summary of the Day", url="https://www.ncei.noaa.gov/data/global-summary-of-the-day/access/",
                 benchmark="Spider 2.0 tasks sf_bq050, sf_bq358, sf_bq426"),
    "weather_stations": dict(source="NOAA station metadata (from the GHCN-Daily files)"),
    "zip_codes": dict(source="U.S. Census TIGER/Line 2019 ZIP Code Tabulation Areas", url="https://www2.census.gov/geo/tiger/TIGER2019/ZCTA5/",
                      benchmark="Spider 2.0 tasks sf_bq050, sf_bq358, sf_bq426"),
    "zip_neighborhoods": dict(source="NYS Department of Health ZIP code definitions of NYC neighborhoods (42 UHF neighborhoods)",
                              benchmark="Spider 2.0 task sf_bq050",
                              modified="reconstructed (the source page is offline); seven ZIP codes inferred from the Spider 2.0 gold of sf_bq050"),
    "taxi_zones": dict(source="NYC Taxi and Limousine Commission taxi zones", url="https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page"),
    "taxi_daily": dict(source="NYC TLC yellow and green taxi trip records", url="https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page",
                       modified="aggregated to daily pickups by zone"),
    "holidays": dict(source="U.S. federal holiday calendar (5 U.S.C. 6103)"),
}
DOCUMENTS = {
    "publisher": dict(document="NYC bike share system data page (trip history data)", url="https://citibikenyc.com/system-data",
                      text=["User Type (Customer = 24-hour pass or 3-day pass user; Subscriber = Annual Member)",
                            "Gender (Zero=unknown; 1=male; 2=female)", "Year of Birth", "Trip Duration (seconds)",
                            "This data has been processed to remove trips that are taken by staff as they service and inspect "
                            "the system, trips that are taken to/from any of our 'test' stations (which we were using more in "
                            "June and July 2013), and any trips that were below 60 seconds in length (potentially false starts "
                            "or users trying to re-dock a bike to ensure it's secure)."]),
    "ghcn": dict(document="NOAA GHCN-Daily readme", url="https://www.ncei.noaa.gov/pub/data/ghcn/daily/readme.txt",
                 text=["PRCP = Precipitation (tenths of mm)", "SNOW = Snowfall (mm)", "SNWD = Snow depth (mm)",
                       "TMAX = Maximum temperature (tenths of degrees C)", "TMIN = Minimum temperature (tenths of degrees C)"]),
}


def load_dimensions(con, cfg):
    raw = repo_path(cfg["source"]["raw"])
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute(f"""CREATE TABLE zip_codes AS SELECT ZCTA5CE10 AS zip_code, FUNCSTAT10 AS functional_status,
        ALAND10 AS area_land_meters, AWATER10 AS area_water_meters,
        CAST(INTPTLAT10 AS DOUBLE) AS internal_point_lat, CAST(INTPTLON10 AS DOUBLE) AS internal_point_lon,
        ST_AsText(geom) AS zip_code_geom
        FROM ST_Read('/vsizip/{raw}/tl_2019_us_zcta510.zip/tl_2019_us_zcta510.shp')
        WHERE ST_Intersects(geom, ST_MakeEnvelope(-74.30, 40.45, -73.65, 40.95)) ORDER BY 1""")
    con.execute(f"""CREATE TABLE zip_neighborhoods AS SELECT CAST(zip AS VARCHAR) AS zip_code, borough, neighborhood
        FROM read_csv('{raw}/nyc_zip_neighborhoods_spider.csv') ORDER BY 1""")
    con.execute(f"""CREATE TABLE taxi_zones AS
        WITH g AS (SELECT LocationID AS zone_id, ST_Union_Agg(ST_Transform(geom, 'EPSG:2263', 'EPSG:4326', true)) AS geom
                   FROM ST_Read('/vsizip/{raw}/taxi_zones.zip/taxi_zones/taxi_zones.shp') GROUP BY 1)
        SELECT l.LocationID AS zone_id, l.Borough AS borough, l.Zone AS zone, l.service_zone,
          ST_AsText(g.geom) AS zone_geom
        FROM read_csv('{raw}/taxi_zone_lookup.csv') l JOIN g ON g.zone_id = l.LocationID ORDER BY 1""")
    ws = pd.DataFrame(cfg["source"]["weather_stations"], columns=["ghcnd_id", "usaf_wban", "name"])
    con.register("_ws", ws)
    con.execute(f"""CREATE TABLE weather_stations AS SELECT w.ghcnd_id, w.usaf_wban, w.name,
        g.lat AS latitude, g.lon AS longitude FROM _ws w JOIN (
          SELECT DISTINCT STATION AS id, CAST(LATITUDE AS DOUBLE) AS lat, CAST(LONGITUDE AS DOUBLE) AS lon
          FROM read_csv('{raw}/ghcnd_*.csv', all_varchar=true, union_by_name=true)
          WHERE DATE = '2018-01-01') g ON g.id = w.ghcnd_id""")
    ids = ", ".join(f"'{x}'" for x in ws.ghcnd_id)
    elems = ["PRCP", "SNOW", "SNWD", "TMAX", "TMIN", "TAVG", "AWND", "WSF2", "WT01", "WT02", "WT03", "WT08"]
    union = " UNION ALL ".join(
        f"SELECT STATION AS id, CAST(DATE AS DATE) AS date, '{e}' AS element, "
        f"try_cast(trim({e}) AS INTEGER) AS value, nullif(split_part({e}_ATTRIBUTES, ',', 1), '') AS mflag, "
        f"nullif(split_part({e}_ATTRIBUTES, ',', 2), '') AS qflag, "
        f"nullif(split_part({e}_ATTRIBUTES, ',', 3), '') AS sflag FROM _g WHERE {e} IS NOT NULL AND trim({e}) <> ''"
        for e in elems)
    con.execute(f"""CREATE TEMP TABLE _g AS SELECT * FROM read_csv('{raw}/ghcnd_*.csv', all_varchar=true,
        union_by_name=true) WHERE STATION IN ({ids}) AND DATE >= '2013-01-01' AND DATE <= '{d(cfg['end'])}'""")
    con.execute(f"CREATE TABLE ghcnd AS SELECT * FROM ({union}) ORDER BY date, id, element")
    num = lambda c, miss: f"CASE WHEN try_cast(trim({c}) AS DOUBLE) >= {miss} THEN NULL ELSE try_cast(trim({c}) AS DOUBLE) END"
    con.execute(f"""CREATE TABLE gsod AS SELECT substr(STATION, 1, 6) AS stn, substr(STATION, 7, 5) AS wban,
        CAST(DATE AS DATE) AS date, year(CAST(DATE AS DATE)) AS year, month(CAST(DATE AS DATE)) AS mo,
        day(CAST(DATE AS DATE)) AS da, {num('TEMP', 9999)} AS "temp", {num('DEWP', 9999)} AS dewp,
        {num('SLP', 9999)} AS slp, {num('VISIB', 999)} AS visib, {num('WDSP', 999)} AS wdsp,
        {num('MXSPD', 999)} AS mxspd, {num('GUST', 999)} AS gust, {num('"MAX"', 9999)} AS "max",
        {num('"MIN"', 9999)} AS "min", {num('PRCP', 99)} AS prcp, {num('SNDP', 999)} AS sndp,
        substr(FRSHTT, 1, 1) = '1' AS fog, substr(FRSHTT, 2, 1) = '1' AS rain_drizzle,
        substr(FRSHTT, 3, 1) = '1' AS snow_ice_pellets, substr(FRSHTT, 4, 1) = '1' AS hail,
        substr(FRSHTT, 5, 1) = '1' AS thunder, substr(FRSHTT, 6, 1) = '1' AS tornado_funnel_cloud
        FROM read_csv('{raw}/gsod_*.csv', all_varchar=true, union_by_name=true) ORDER BY date, stn""")
    con.execute(f"""CREATE TABLE taxi_daily AS SELECT day AS date, service, zone_id, trips, short_trips,
        round(mean_distance, 3) AS mean_distance, round(mean_fare, 2) AS mean_fare
        FROM '{repo_path(cfg['source']['taxi'])}/*.parquet' WHERE day BETWEEN '2014-01-01' AND '{d(cfg['end'])}'
        AND zone_id BETWEEN 1 AND 263 ORDER BY date, service, zone_id""")
    con.register("_hol", federal_holidays(range(2013, 2019)))
    con.execute("CREATE TABLE holidays AS SELECT CAST(date AS DATE) AS date, holiday FROM _hol")


def load_trips(con, cfg):
    old, new = cfg["rename_operator"]["old"], cfg["rename_operator"]["new"]
    end = d(pd.Timestamp(cfg["end"]) + pd.Timedelta(days=1))
    cols = ", ".join(f"replace({c}, '{old}', '{new}') AS {c}" if c.endswith("station_name") else c for c in TRIP_COLS)
    con.execute(f"""CREATE TABLE trips AS SELECT {cols} FROM '{repo_path(cfg["source"]["trips"])}/*.parquet'
        WHERE starttime < TIMESTAMP '{end}' ORDER BY starttime, bikeid""")


def load_stations(con):
    """Stations from the trip records: most used position, ZIP code area and taxi zone, docks from the station feed."""
    con.execute("""CREATE TEMP TABLE _st AS
        WITH e AS (SELECT start_station_id AS id, start_station_latitude AS lat, start_station_longitude AS lon FROM trips
                   UNION ALL SELECT end_station_id, end_station_latitude, end_station_longitude FROM trips),
        s AS (SELECT id, lat, lon, count(*) AS n FROM e
              WHERE lat BETWEEN 40.5 AND 40.95 AND lon BETWEEN -74.1 AND -73.7 GROUP BY 1, 2, 3),
        best AS (SELECT id, arg_max(lat, (n, lat, lon)) AS lat, arg_max(lon, (n, lat, lon)) AS lon FROM s GROUP BY 1)
        SELECT b.*, z.zone_id, CASE WHEN z.zone_id IS NULL AND b.lon < -74.02 THEN 'New Jersey' ELSE z.borough END AS borough
        FROM best b LEFT JOIN taxi_zones z ON ST_Within(ST_Point(b.lon, b.lat), ST_GeomFromText(z.zone_geom))""")
    feed = pd.DataFrame(json.load(open(repo_path(CFG["source"]["raw"]) / "gbfs_station_information_2026.json"))["data"]["stations"])
    feed = feed[feed.capacity > 0][["name", "lat", "lon", "capacity"]]
    con.register("_feed", feed)
    con.execute(f"""CREATE TEMP TABLE _docks AS
        WITH nm AS (SELECT start_station_id AS station_id, arg_max(start_station_name, (starttime, start_station_name)) AS name
                    FROM trips GROUP BY 1)
        SELECT nm.station_id, arg_min(f.capacity, ({hav_sql('s.lat', 's.lon', 'f.lat', 'f.lon')}, f.capacity)) AS docks
        FROM nm JOIN _st s ON s.id = nm.station_id JOIN _feed f ON lower(f.name) = lower(nm.name)
        WHERE {hav_sql('s.lat', 's.lon', 'f.lat', 'f.lon')} < 150 GROUP BY 1""")
    con.execute("""CREATE TABLE station_names AS
        WITH e AS (SELECT start_station_id AS station_id, start_station_name AS name, starttime AS t FROM trips
                   UNION ALL SELECT end_station_id, end_station_name, starttime FROM trips)
        SELECT station_id, name, CAST(min(t) AS DATE) AS first_seen, CAST(max(t) AS DATE) AS last_seen, count(*) AS n_trip_ends
        FROM e WHERE station_id IS NOT NULL GROUP BY 1, 2 ORDER BY 1, 3""")
    con.execute("""CREATE TABLE stations AS
        WITH e AS (SELECT start_station_id AS id, start_station_name AS nm, starttime AS t FROM trips
                   UNION ALL SELECT end_station_id, end_station_name, starttime FROM trips),
        g AS (SELECT id, arg_max(nm, (t, nm)) AS name, CAST(min(t) AS DATE) AS first_trip, CAST(max(t) AS DATE) AS last_trip
              FROM e WHERE id IS NOT NULL GROUP BY 1)
        SELECT g.id AS station_id, g.name, s.lat AS latitude, s.lon AS longitude, z.zip_code, s.zone_id AS taxi_zone_id,
          s.borough, g.first_trip, g.last_trip, c.docks
        FROM g LEFT JOIN _st s ON s.id = g.id
        LEFT JOIN zip_codes z ON s.lat IS NOT NULL AND ST_Within(ST_Point(s.lon, s.lat), ST_GeomFromText(z.zip_code_geom))
        LEFT JOIN _docks c ON c.station_id = g.id ORDER BY 1""")


def write_catalog(out: Path):
    cat = out / "catalog"
    if cat.exists():
        shutil.rmtree(cat)
    shutil.copytree(HERE / "catalog", cat)
    (cat / "sources.json").write_text(json.dumps(SOURCES, indent=1) + "\n")
    (cat / "documents.json").write_text(json.dumps(DOCUMENTS, indent=1) + "\n")


def build(out: Path = OUT):
    out.mkdir(parents=True, exist_ok=True)
    path = out / "world.duckdb"
    if path.exists():
        path.unlink()
    con = duckdb.connect(str(path))
    con.execute("SET threads=24")
    load_dimensions(con, CFG)
    load_trips(con, CFG)
    load_stations(con)
    con.execute("CHECKPOINT")
    con.close()
    write_catalog(out)


if __name__ == "__main__":
    build()
    print(f"wrote {OUT}")
