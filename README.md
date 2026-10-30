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
| NYC bike share (`nyc_bikeshare`) | 70.8M real bike share trips in New York City, July 2013 to December 2018, with NOAA weather, Census ZIP areas, NYC taxi zones and daily taxi pickups | 52 |
| Brazilian e-commerce (`br_ecommerce`) | 99,441 real orders of a Brazilian online marketplace, September 2016 to October 2018, with items, payments, reviews, sellers, 1M geolocation points and the marketing funnel; IBGE states, municipalities, population and municipal GDP; Banco Central exchange rates and inflation; holiday calendar | 54 |

## Tasks

`out/<env>/tasks.json` holds the tasks of an environment. Each task has:

- the question and its answer format;
- the ground-truth reasoning path: steps, each with its query or source passage and its result on the data;
- the lower-level tasks it builds on (`depends_on`) and the steps that reuse their answers (`uses`);
- its horizon (`horizon`): all the tasks it builds on, directly or through them, the length of its longest chain of
  dependencies, and the number of steps of the whole analysis;
- the provenance of the data it uses;
- a target response, the gold answer and how it is scored.

### Levels build on each other

A higher-level task rests on the answers of lower-level tasks, so the levels form a spectrum of growing horizon. The
builds enforce these rules (`datataskbench/spectrum.py`) and stop when a task breaks one:

- Q1 and Q2 are the foundation: what the data mean and how to get values out of them;
- from Q3 on, a task depends on at least one task of the level just below (a discovery on a description, a diagnosis
  on a discovery, a prediction on a diagnosis, a prescription on a prediction), so the shortest chain of dependencies
  grows by one with each level;
- the minimum number of direct dependencies (Q3: 1, Q4: 2, Q5: 2, Q6: 3, Q7: 3) and of all prerequisites (Q3: 1,
  Q4: 3, Q5: 4, Q6: 6, Q7: 8) grows with the level;
- every dependency is used by a step of the reasoning path, and a task never depends on a higher level;
- the answers of a forecast task's prerequisites do not contain the values it forecasts (the monthly series of Q2
  end before every forecast period).

`scripts/spectrum.py` prints the result for each built environment; the means per level:

| Level | NYC bike share: tasks, prerequisites, depth, total steps | Brazilian e-commerce: tasks, prerequisites, depth, total steps |
|---|---|---|
| Q1 Definition | 5, 0.4, 1.4, 4.4 | 4, 0.0, 1.0, 3.0 |
| Q2 Retrieve | 10, 0.9, 1.9, 4.9 | 13, 0.5, 1.5, 4.0 |
| Q3 Describe | 9, 1.8, 2.4, 6.8 | 10, 1.4, 2.3, 5.5 |
| Q4 Discover | 10, 4.8, 3.5, 14.7 | 9, 5.1, 3.3, 14.4 |
| Q5 Diagnose | 6, 6.5, 4.5, 18.8 | 7, 5.7, 4.3, 17.1 |
| Q6 Predict | 8, 8.6, 5.4, 24.5 | 7, 7.7, 5.1, 22.4 |
| Q7 Prescribe | 4, 11.2, 6.2, 30.5 | 4, 13.5, 6.2, 36.8 |

### Spider 2.0 tasks

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

No source is on a personal cloud drive. On a network with an allow list, the script needs these hosts:

| Host | For |
|---|---|
| `pypi.org`, `files.pythonhosted.org` | the Python packages |
| `extensions.duckdb.org` | the DuckDB `spatial` and `sqlite` extensions |
| `s3.amazonaws.com` (bucket `tripdata`) | the bike share trips (operator's public bucket) |
| `www2.census.gov` | Census ZIP code areas |
| `huggingface.co` and its download CDN (`*.hf.co`) | the Spider 2.0 marketplace database (dataset `xlangai/spider2-localdb`) |
| `geoftp.ibge.gov.br`, `ftp.ibge.gov.br` | IBGE meshes and municipal GDP |

`python scripts/fetch_sources.py --check` lists the source files that are present and checks their SHA-256.

### Running a model on the tasks

Give the model the environment (`out/<env>/world.duckdb` and `out/<env>/catalog/`: data dictionary, metric
definitions, sources and documents) and,
for each task, only `question` and `answer_format`; keep `steps`, `gold`, `target_response`, `provenance` and
`horizon` for scoring and analysis. To test long-horizon analysis without help, ask the question alone; to test one
step at a time, also give the answers of the tasks in `depends_on`.

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
