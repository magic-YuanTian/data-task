# Data dictionary - NYC bike share (world.duckdb)

All times are New York local time. The database covers trips that started from July 1, 2013 through December 31, 2018.
Spatial columns hold WKT text (longitude latitude, WGS 84); load the DuckDB `spatial` extension and use
`ST_GeomFromText` to work with them.

## trips
One row per customer ride, as published in the monthly trip histories. The publisher removed staff trips, trips to or
from test stations and trips under 60 seconds (system data page, see catalog/sources.json).

| Column | Type | Description |
|---|---|---|
| tripduration | BIGINT | Ride time in seconds, from unlocking to return of the bike. |
| starttime | TIMESTAMP | Time the bike was unlocked. |
| stoptime | TIMESTAMP | Time the bike was returned. |
| start_station_id | BIGINT | Number of the station where the ride started. |
| start_station_name | VARCHAR | Name of that station at the time of the ride. |
| start_station_latitude | DOUBLE | Latitude of that station at the time of the ride. |
| start_station_longitude | DOUBLE | Longitude of that station at the time of the ride. |
| end_station_id | BIGINT | Number of the station where the ride ended. |
| end_station_name | VARCHAR | Name of that station at the time of the ride. |
| end_station_latitude | DOUBLE | Latitude of that station at the time of the ride. |
| end_station_longitude | DOUBLE | Longitude of that station at the time of the ride. |
| bikeid | BIGINT | Bike number. |
| usertype | VARCHAR | Subscriber (annual member) or Customer (24-hour or 3-day pass user), as defined by the publisher. |
| birth_year | BIGINT | Rider's year of birth. |
| gender | VARCHAR | male, female or unknown. |

## stations
One row per station number that appears in `trips`.

| Column | Type | Description |
|---|---|---|
| station_id | BIGINT | Station number. |
| name | VARCHAR | Most recent name in the trip records. |
| latitude, longitude | DOUBLE | Usual position of the station; NULL when the station has no valid position. |
| zip_code | VARCHAR | ZIP code area (Census ZCTA) that contains the station; NULL when the position is in no ZIP code area. |
| taxi_zone_id | INTEGER | NYC TLC taxi zone that contains the station. |
| borough | VARCHAR | Borough of the taxi zone; 'New Jersey' for stations across the Hudson. |
| first_trip, last_trip | DATE | First and last day with a trip record at the station. |
| docks | INTEGER | Number of docks, where known. |

## station_names
Every name under which a station number appears in `trips`, with the first and last day it was used and the
number of trip records (starts plus ends) under that name.

## Weather
- `weather_stations`: the three NOAA stations used (Central Park, LaGuardia Airport, JFK Airport), with GHCN-Daily
  id and GSOD USAF-WBAN number.
- `ghcnd`: NOAA GHCN-Daily records in long format (id, date, element, value, mflag, qflag, sflag), as published
  by NOAA. Elements include PRCP, SNOW, SNWD, TMAX, TMIN, TAVG, AWND, WSF2 and weather-type flags WT01-WT08.
- `gsod`: NOAA Global Summary of the Day (stn, wban, date, year, mo, da, temp, dewp, slp, visib, wdsp, mxspd, gust,
  max, min, prcp, sndp and weather indicators), with missing values as NULL.

## Geography
- `zip_codes`: Census ZIP Code Tabulation Areas (TIGER/Line 2019) in and around New York City, with polygons.
- `zip_neighborhoods`: neighborhood and borough of NYC ZIP codes.
- `taxi_zones`: NYC TLC taxi zones with borough, zone name, service zone and polygon.

## taxi_daily
Daily taxi pickups by TLC taxi zone and service (yellow or green taxi): date, service, zone_id, trips, short_trips
(under 2 miles), mean_distance in miles, mean_fare in dollars. From the public TLC trip records, from January 2014.

## holidays
US federal holidays (observed dates).
