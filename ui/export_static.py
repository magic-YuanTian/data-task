"""Write the explorer as one self-contained HTML file that opens without a server, to send by email.

    .venv/bin/python ui/export_static.py --live-url https://yuan-tian.com/data-task   # docs/DataTaskBench.html
    .venv/bin/python ui/export_static.py --worlds br_ecommerce -o /tmp/br_ecommerce.html

The file holds every page of the explorer: the taxonomy, the data of each environment (column profiles, the first 100
rows of every table, charts, maps, sources, the data dictionary) and every task with its reasoning path and recorded
results. Queries that the pages offer (step queries, chart and metric queries, examples) carry their results; a new
query needs the live explorer. The JavaScript is bundled with esbuild (fetched by npx on first use).
"""
from __future__ import annotations

import argparse
import base64
import gzip
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from server import meta as meta_route, world as world_route  # noqa: E402
from store import ROOT, UI, Store  # noqa: E402

STATIC = UI / "static"
ESBUILD = "esbuild@0.24.0"
VENDOR_JS = ["vendor/d3.min.js", "vendor/plot.umd.min.js", "vendor/codemirror/codemirror.js", "vendor/codemirror/sql.js",
             "vendor/codemirror/matchbrackets.js", "vendor/codemirror/show-hint.js", "vendor/codemirror/sql-hint.js"]
CSS = ["vendor/codemirror/codemirror.css", "vendor/codemirror/show-hint.css", "css/app.css"]
TABLE_LIST = "SELECT table_name, estimated_size AS approx_rows, column_count\nFROM duckdb_tables()\nORDER BY table_name;"
START = "SELECT table_name, estimated_size AS approx_rows\nFROM duckdb_tables()\nORDER BY approx_rows DESC;"


def sql_key(world, sql):
    """The same key as sqlKey in static/js/lib.js."""
    s = sql.strip()
    s = s[:-1].rstrip() if s.endswith(";") else s
    return world + "\n" + " ".join(s.split())


def clip(v, n=300):
    """Geometries in WKT run to megabytes; the copy keeps their start."""
    if isinstance(v, str) and len(v) > n:
        return f"{v[:n]}… ({len(v):,} characters)"
    return v


def collect(store: Store, live_url: str) -> dict:
    get, sql = {}, {}

    def run(w, text, limit=5000, same_as=None):
        k = sql_key(w, text)
        if same_as:                         # the same query written differently: a pointer to its key
            sql[k] = sql_key(w, same_as)
        elif k not in sql:
            r = store.run_sql(w, text.strip(), limit=limit, timeout=600)
            r["rows"] = [[clip(v) for v in row] for row in r["rows"]]
            sql[k] = r

    m = meta_route(store, {}, {})
    m["mode"] = "offline"
    get["/api/meta"] = m
    for w in store.builds():
        print(f"== {w}", flush=True)
        wd = world_route(store, {}, {}, w)
        get[f"/api/world/{w}"] = wd
        get[f"/api/tasks/{w}"] = store.tasks(w)
        for t in wd["tables"]:
            n = t["name"]
            get[f"/api/world/{w}/profile/{n}"] = store.profile(w, n)
            run(w, f'SELECT * FROM "{n}" LIMIT 100', limit=2000)      # Preview tab
            run(w, f"SELECT *\nFROM {n}\nLIMIT 100;", same_as=f'SELECT * FROM "{n}" LIMIT 100')   # Query button
        for k, mt in wd["metrics"].items():
            get[f"/api/world/{w}/view/metric:{k}"] = store.view_data(w, f"metric:{k}")
            run(w, mt["sql"])
        for v in wd["views"]:
            if v.get("sql"):
                get[f"/api/world/{w}/view/{v['id']}"] = store.view_data(w, v["id"])
                run(w, v["sql"])
            for layer in v.get("layers", []):
                get[f"/api/world/{w}/geo/{layer}"] = store.geo(w, layer)
        run(w, TABLE_LIST)
        run(w, START)
        for t in store.tasks(w):
            for s in t["steps"]:
                if s.get("sql"):
                    run(w, s["sql"])
        print(f"   {len(wd['tables'])} tables, {len(store.tasks(w))} tasks", flush=True)
    note = "This copy holds the results of the queries shown on its pages. Edited or new queries run on the live explorer"
    note += f": {live_url}" if live_url else "."
    return {"get": get, "sql": sql, "sql_note": note}


def bundle() -> str:
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "app.js"
        subprocess.run(["npx", "--yes", ESBUILD, str(STATIC / "js" / "app.js"), "--bundle", "--format=iife",
                        "--minify", "--legal-comments=eof", f"--outfile={out}", "--log-level=warning"], check=True)
        return out.read_text()


def script(text: str) -> str:
    return text.replace("</script", "<\\/script").replace("<!--", "<\\!--")


def page(data: dict, app_js: str) -> str:
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str).encode()
    packed = base64.b64encode(gzip.compress(raw, 9, mtime=0)).decode()
    index = (STATIC / "index.html").read_text()
    icon = next(line.strip() for line in index.splitlines() if 'rel="icon"' in line)
    css = "\n".join((STATIC / f).read_text() for f in CSS)
    vendor = "\n".join(f"<script>{script((STATIC / f).read_text())}</script>" for f in VENDOR_JS)
    # the recorded responses are gzipped; the app starts once they are unpacked
    boot = """(async () => {
  const bin = Uint8Array.from(atob(document.getElementById('dtb-data').textContent), (c) => c.charCodeAt(0));
  const text = await new Response(new Blob([bin]).stream().pipeThrough(new DecompressionStream('gzip'))).text();
  window.DTB_OFFLINE = JSON.parse(text);
  const s = document.createElement('script');
  s.textContent = document.getElementById('dtb-app').textContent;
  document.body.appendChild(s);
})().catch((e) => { document.getElementById('app').textContent = 'This browser cannot open the file: ' + e; });"""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DataTaskBench</title>
  {icon}
  <style>{css}</style>
  {vendor}
</head>
<body>
  <div id="app"></div>
  <script id="dtb-data" type="text/plain">{packed}</script>
  <script id="dtb-app" type="text/plain">{script(app_js)}</script>
  <script>{boot}</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", type=Path, default=ROOT / "docs" / "DataTaskBench.html")
    ap.add_argument("--worlds", default="", help="only these environments (comma-separated)")
    ap.add_argument("--live-url", default="", help="the live explorer, named where a query has no recorded result")
    a = ap.parse_args()
    store = Store(worlds=[w for w in a.worlds.split(",") if w])
    data = collect(store, a.live_url)
    html = page(data, bundle())
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(html)
    print(f"wrote {a.out} ({len(html.encode()) / 1e6:.1f} MB, {len(data['get'])} responses, {len(data['sql'])} queries)")


if __name__ == "__main__":
    main()
