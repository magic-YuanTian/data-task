# Sources of the NYC bike share environment

All files are public, and nothing here comes from a paid service. Small files are kept in this directory. The
trip archives and the ZIP code areas are downloaded by `scripts/fetch_sources.py` (with SHA-256 checks), and the
trips are normalized by `envs/nyc_bikeshare/ingest.py`. The daily taxi pickups in `taxi/` were computed by
`envs/nyc_bikeshare/ingest_taxi.py` from the TLC trip record files, which are too large to keep.

| Kept here | Downloaded by the scripts |
|---|---|
| `raw/ghcnd_*.csv` (2013-2018 rows of the three station files), `raw/gsod_*.csv`, `raw/taxi_zones.zip`, `raw/taxi_zone_lookup.csv`, `raw/nyc_zip_neighborhoods*.csv`, `raw/gbfs_station_information_2026.json` (name, position and dock count of each station), `taxi/*.parquet` | `raw/{2013..2018}-citibike-tripdata.zip` (converted to `trips/` and then deleted), `raw/tl_2019_us_zcta510.zip` |

| File | Source | Use | License or terms |
|---|---|---|---|
| `trips/YYYYMM.parquet` | NYC bike share system data, yearly archives `https://s3.amazonaws.com/tripdata/{2013..2018}-citibike-tripdata.zip` (month folders; the root CSVs are older duplicates) | trips, July 2013 - December 2018 | Data license of Lyft Bikes and Scooters, LLC: use, modify and distribute in a product for any lawful purpose; not to be published as a stand-alone dataset (allowed as source material in non-commercial analyses and studies); no use of the CITI BIKE marks |
| `raw/ghcnd_*.csv` | NOAA GHCN-Daily station files, `https://www.ncei.noaa.gov/data/global-historical-climatology-network-daily/access/{id}.csv` | daily weather (Central Park, LaGuardia, JFK) | U.S. government, public domain |
| `raw/gsod_{year}_{usaf}{wban}.csv` | NOAA Global Summary of the Day, `https://www.ncei.noaa.gov/data/global-summary-of-the-day/access/{year}/` | daily weather in GSOD units | public domain |
| `raw/tl_2019_us_zcta510.zip` | U.S. Census TIGER/Line 2019 ZCTAs, `https://www2.census.gov/geo/tiger/TIGER2019/ZCTA5/` | ZIP code areas (identical to BigQuery `geo_us_boundaries.zip_codes`: areas match the 2019 Census gazetteer) | public domain |
| `raw/taxi_zones.zip`, `raw/taxi_zone_lookup.csv` | NYC TLC, `https://d37ci6vzurychx.cloudfront.net/misc/` | taxi zones, borough of stations | NYC Open Data terms |
| `taxi/{yellow,green}_YYYYMM.parquet` | NYC TLC trip record files, `https://d37ci6vzurychx.cloudfront.net/trip-data/` | daily pickups by zone (aggregated here; trip-level files are not kept) | NYC Open Data terms |
| `raw/nyc_zip_neighborhoods.csv` | NYS Department of Health ZIP code definitions of NYC neighborhoods (42 UHF neighborhoods); the page is offline, so the table was reconstructed | neighborhoods | public information |
| `raw/nyc_zip_neighborhoods_spider.csv` | the table above plus seven ZIP codes inferred from the Spider 2.0 gold of `sf_bq050` (10282 Tribeca; 10199, 10103, 10110 Chelsea and Clinton; 10167, 10168 Gramercy Park and Murray Hill; 10278 Lower Manhattan) | `zip_neighborhoods` | - |
| `raw/gbfs_station_information_2026.json` | public station feed, `https://gbfs.citibikenyc.com/gbfs/en/station_information.json` (fetched October 2026) | dock counts, matched to 2013-2018 stations by name and position (a stand-in: the 2018 counts are not public); only name, position and dock count are kept | Lyft data license, as for the trips |

## Checks against Spider 2.0

On these records the Spider 2.0-lite tasks reproduce their gold:
`bq202` (count 1943), `sf_bq426` (exact to all printed digits), `sf_bq358`, `sf_bq050` (all 121 neighborhood pairs;
one rounding edge and one tied month). This confirms that the trips equal BigQuery `new_york_citibike.citibike_trips`
(58,937,715 rows before June 2018), that the ZIP polygons are the 2019 ZCTAs and that missing GSOD values were
NULL in Spider's copy. Not reproducible: `bq051` (no GHCN station gives the gold split), `bq038` (the group-ride
definition could not be recovered), `bq021` (needs taxi coordinates that the current TLC files no longer have).

## Disk use

About 1.4 GB for the monthly trip files and 2.5 GB for the built environment. The yearly archives (3.2 GB) are deleted after conversion; `ingest.py --keep-zip` keeps them.
