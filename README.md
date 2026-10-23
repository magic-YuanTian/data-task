# DataTaskBench

DataTaskBench is a benchmark of data analysis tasks on real data. The tasks span seven levels, from what a field
means to what decision to make, and 21 task types within them. Every task comes with its ground-truth reasoning path:
the steps of the analysis, the query or source passage behind each step, and the result it gives on the data.

| Level | Task types |
|---|---|
| Q1 Definition | meaning of fields and terms, document understanding |
| Q2 Retrieve | filter, aggregation and ranking, exact value search, document information retrieval |
| Q3 Describe | trend, cycles, distribution, relationship |
| Q4 Discover | anomalies and spikes, distributional shifts, correlations, open discovery |
| Q5 Diagnose | find the reasons, quantify contributions |
| Q6 Predict | value forecasting, consequences, what-if simulation |
| Q7 Prescribe | open-ended suggestions, optimization under constraints |

## Data environments

A data environment is what an analyst would work with: a database, a data dictionary with certified metric
definitions, and the documents of the data publishers. Every table comes from a public source or from the data of
an existing benchmark (Spider 2.0); nothing is simulated and no record is planted. See
[DATA_SOURCES.md](DATA_SOURCES.md) for the source of every table and the terms that apply.

| Environment | Data | Tasks |
|---|---|---|
| NYC bike share (`nyc_bikeshare`) | 70.8M real bike share trips in New York City, July 2013 to December 2018, with NOAA weather, Census ZIP areas, NYC taxi zones and daily taxi pickups | 23 |
| Brazilian e-commerce (`br_ecommerce`) | 99,441 real orders of a Brazilian online marketplace, September 2016 to October 2018, with items, payments, reviews, sellers, 1M geolocation points and the marketing funnel; IBGE states, municipalities, population and municipal GDP; Banco Central exchange rates and inflation; holiday calendar | 32 |

## Tasks

`out/<env>/tasks.json` holds the tasks of an environment. Each task has:

- the question and its answer format;
- the ground-truth reasoning path: steps, each with its query or source passage and its result on the data;
- the lower-level tasks it builds on (`depends_on`) and the steps that reuse their answers (`uses`);
- the provenance of the data it uses;
- a target response, the gold answer and how it is scored.

Eight tasks are Spider 2.0 tasks (two on the bike share trips, six on the marketplace) and are checked against the
Spider gold results. Forecast tasks state the period they
may use and are scored against the recorded values. A task that needs a planted event would be marked `planted`.

## Reproduce

```
git clone https://github.com/magic-YuanTian/data-task.git && cd data-task
scripts/reproduce.sh
```

The script creates a Python 3.11 environment with the pinned packages (`requirements.txt`), downloads the source
files that are not in the repository (bike share trips, Census ZIP areas, the Spider 2.0-lite marketplace database,
IBGE meshes and GDP; each checked against its SHA-256), converts the trips, builds the environments and their tasks,
and compares the result with the published builds (`reproduce/expected.json`: every table, the catalog and every
task). It needs `curl`, a network connection and about 16 GB of free disk; `--env br_ecommerce` builds one
environment (about 1 GB). If `python3.11` is not on the path: `PYTHON=/path/to/python3.11 scripts/reproduce.sh`.

```
.venv/bin/python -m envs.<env>.build            # out/<env>/world.duckdb and catalog/
.venv/bin/python -m envs.<env>.tasks            # out/<env>/tasks.json
.venv/bin/python scripts/verify.py              # compare with the published builds
```

## Explorer

```
.venv/bin/python ui/server.py              # http://127.0.0.1:8765
```

A local web app with three parts: Taxonomy (the levels and task types), Data (tables with column profiles, charts, map,
sources and documents, data dictionary, SQL) and Tasks (dependency graph, level filter, and for each task its
reasoning path with editable queries, its target response and gold answer). See [ui/README.md](ui/README.md).

[docs/DataTaskBench.html](docs/DataTaskBench.html) is the same explorer as one file (3 MB) that opens in a browser
without a server: download it and open it. It holds the first 100 rows of every table and the results of the
queries shown on its pages. `ui/export_static.py` rebuilds it.

## Layout

```
datataskbench/           levels and task types, statistics, anomaly scan, deterministic queries, task kit, Spider check
envs/nyc_bikeshare/      config, ingest scripts, build (database and catalog), tasks, data dictionary
envs/br_ecommerce/       config, build (database and catalog), tasks, data dictionary
sources/nyc/             small source files and their provenance (README.md)
sources/br_ecommerce/    small source files and their provenance (README.md)
third_party/spider2/     Spider 2.0 gold results and documents used by the tasks (MIT license)
scripts/                 reproduce.sh, fetch_sources.py, verify.py
reproduce/expected.json  hashes of the published build
ui/                      explorer web app
docs/DataTaskBench.html  the explorer as one offline HTML file
```

## Licenses

The code does not have an open-source license yet. The terms of the source data are in
[DATA_SOURCES.md](DATA_SOURCES.md). The bike share trips may not be redistributed as a stand-alone dataset and the
marketplace records are under CC BY-NC-SA 4.0, so this repository ships the scripts that download them; the offline explorer holds only the first 100 rows of each table. Spider 2.0 files are under the MIT license. The vendored front-end
libraries keep their licenses in `ui/static/vendor/`.
