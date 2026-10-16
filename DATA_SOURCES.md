# Data sources

Every table of a DataTaskBench environment comes from a public source or from the data of an existing benchmark.
Nothing is simulated and no record is planted. If a task ever needs a planted event, the task is marked `planted`
and the plant is described with it. This file lists where each table comes from, what was changed and which terms
apply. The same information ships with the environment (`out/<env>/catalog/sources.json`) and is shown on the Data
page of the explorer.

## NYC bike share (`nyc_bikeshare`)

The trips are the public trip histories of the New York City bike share system, July 2013 to December 2018. Up to
May 2018 they are the same records as the BigQuery bike share table that Spider 2.0 uses: Spider 2.0-lite tasks run
on this copy reproduce the Spider gold answers. Details and URLs are in [sources/nyc/README.md](sources/nyc/README.md).

| Table | Source | Changes | Terms | In this repository |
|---|---|---|---|---|
| `trips` | monthly trip histories published by Lyft Bikes and Scooters, LLC | operator name in station names replaced, as the license requires; gender written as text | Lyft data license | downloaded by the scripts |
| `stations`, `station_names` | derived from the trips; ZIP code area and taxi zone by spatial join; dock counts from the public station feed (October 2026), matched by name and position, because the historical counts are not published | dock counts are a stand-in | Lyft data license | names, positions and dock counts of the feed |
| `ghcnd`, `gsod`, `weather_stations` | NOAA GHCN-Daily and Global Summary of the Day, Central Park, LaGuardia and JFK | none | public domain | 2013-2018 extracts |
| `zip_codes` | U.S. Census TIGER/Line 2019 ZIP Code Tabulation Areas | none | public domain | downloaded by the scripts |
| `zip_neighborhoods` | NY State Department of Health neighborhood definitions of NYC ZIP codes | reconstructed (the page is offline); seven ZIP codes inferred from the Spider 2.0 gold of `sf_bq050` | public information | yes |
| `taxi_zones`, `taxi_daily` | NYC Taxi and Limousine Commission zones and trip records | trips aggregated to zone and day | NYC Open Data terms | zone files and daily aggregates |
| `holidays` | U.S. federal holiday calendar | none | none | generated |

Source documents quoted by the tasks: the publisher's system data page (field definitions and processing,
https://citibikenyc.com/system-data) and the NOAA GHCN-Daily readme
(https://www.ncei.noaa.gov/pub/data/ghcn/daily/readme.txt).

Spider 2.0 gold results used by two tasks are in `third_party/spider2` (MIT license).

### The trip data license

The license of Lyft Bikes and Scooters, LLC lets anyone use, modify and distribute the data in a product for any
lawful purpose. It does not allow publishing the data as a stand-alone dataset, except as source material in
non-commercial analyses and studies, and it does not allow the CITI BIKE marks. This repository therefore ships the
scripts that download the trips, not the trips.
