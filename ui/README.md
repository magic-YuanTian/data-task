# DataTaskBench explorer

A local web app over the environments built in `out/`.

```
.venv/bin/python ui/server.py                    # http://127.0.0.1:8765
.venv/bin/python ui/server.py --warm             # precompute column profiles, charts and maps, then exit
.venv/bin/python ui/server.py --public --port 3550 --embed-origin https://example.org   # for visitors, framed
```

No build step and no extra Python packages: the server uses the standard library, DuckDB and PyYAML, and the front
end loads vendored libraries from `static/vendor/` (Preact with htm, Observable Plot with d3, CodeMirror 5, marked).

| Page | What it shows |
|---|---|
| Taxonomy | the benchmark, the seven levels and 21 task types; clicking one explains it |
| Data | an overview of the environment; tables with column profiles (the default tab), charts, map, sources and documents, data dictionary and certified metrics, and a SQL workbench |
| Tasks | the tasks with a dependency graph and a level filter; a task shows its question, the tasks it builds on and is used by, the sources of its data, its reasoning path (each query is editable and runs in place), its target response and gold answer |

The data environment is chosen in the top bar. A new environment needs no explorer code: build it into
`out/<env>/` (`world.duckdb`, `tasks.json`, `catalog/`), add it to `content/worlds.yaml` and, optionally, charts to
`content/views.yaml`.

`ui/export_static.py` writes the explorer as one HTML file (`docs/DataTaskBench.html`, about 3 MB) that opens in a
browser without a server, for example as an email attachment. It holds every page, the first 100 rows of every table,
and the results of every query shown on the pages; an edited or new query points to the live explorer (`--live-url`).
It bundles the JavaScript with esbuild, which npx fetches on first use.

## Safety

- Databases are opened read-only with file access disabled (`enable_external_access = false`, configuration
  locked), so queries cannot read or write files, attach databases or install extensions. Queries stop after
  120 s (30 s with `--public`) and return at most 5,000 rows.
- `--public` bounds what visitors can use: each database gets 4 GB of memory and 4 threads, at most three visitor
  queries run at a time (others wait up to 20 s), each visitor may run 30 queries a minute (the address comes from
  `CF-Connecting-IP` behind Cloudflare), and server errors do not show details.
- `--embed-origin` names the pages that may show the explorer in a frame (`Content-Security-Policy:
  frame-ancestors`); in such a frame the explorer posts its route to the page (`{type: "datataskbench:route", hash}`).
- The server binds to 127.0.0.1 by default.
