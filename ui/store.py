"""Access to the built environments: tasks, databases, catalog, sources, profiles, charts and maps."""
from __future__ import annotations

import datetime as dt
import decimal
import hashlib
import json
import math
import re
import threading
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path

import duckdb
import yaml

ROOT = Path(__file__).resolve().parents[1]          # bench/
OUT = ROOT / "out"
UI = ROOT / "ui"
CONTENT = UI / "content"
CACHE = UI / ".cache"

import sys
sys.path.insert(0, str(ROOT))
from datataskbench.levels import LEVEL_NAMES, TASK_TYPES  # noqa: E402

LEVEL_ORDER = list(LEVEL_NAMES)
NUMERIC = re.compile(r"^(TINYINT|SMALLINT|INTEGER|BIGINT|HUGEINT|UTINYINT|USMALLINT|UINTEGER|UBIGINT|FLOAT|DOUBLE|"
                     r"REAL|DECIMAL.*)$")
TEMPORAL = re.compile(r"^(DATE|TIMESTAMP.*|TIME)$")


def _load_yaml(path: Path, default=None):
    return yaml.safe_load(open(path)) if path.exists() else default


def jsonable(v):
    """Convert a database value to JSON."""
    if v is None or isinstance(v, (str, bool, int)):
        return v
    if isinstance(v, float):
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat(sep=" ") if isinstance(v, dt.datetime) else v.isoformat()
    if isinstance(v, dt.timedelta):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(v))} bytes>"
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    return str(v)


class _Cached:
    """Reload a file-derived value when the file changes."""

    def __init__(self):
        self._c = {}
        self._lock = threading.Lock()

    def get(self, key, paths, fn):
        stamp = tuple(p.stat().st_mtime if p.exists() else 0 for p in paths)
        with self._lock:
            hit = self._c.get(key)
            if hit and hit[0] == stamp:
                return hit[1]
        val = fn()
        with self._lock:
            self._c[key] = (stamp, val)
        return val


def parse_dictionary(md: str) -> dict:
    """Table and column descriptions from a data dictionary in markdown."""
    note, tables, cur = [], {}, None
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("## "):
            names = [n.strip(" `") for n in re.split(r",| and ", s[3:])]
            cur = [n for n in names if n]
            for n in cur:
                tables.setdefault(n, {"description": "", "columns": {}})
        elif s.startswith("# ") or not s:
            continue
        elif cur is None:
            note.append(s)
        elif s.startswith("|"):
            parts = [p.strip() for p in s.strip("|").split("|")]
            if set(s) <= set("|-: ") or parts[0].lower() in ("column", "field", "columns"):
                continue
            desc = parts[-1] if len(parts) >= 2 else ""
            for n in re.split(r",\s*", parts[0]):
                for t in cur:
                    tables[t]["columns"][n.strip(" `")] = desc
        else:
            m = re.match(r"^- `([a-z_0-9]+)`:\s*(.*)", s)
            if m:
                tables.setdefault(m.group(1), {"description": "", "columns": {}})
                tables[m.group(1)]["description"] += m.group(2) + " "
            else:
                for t in cur:
                    tables[t]["description"] += s + " "
    for t in tables.values():
        t["description"] = t["description"].strip()
    return {"note": " ".join(note), "tables": tables}


class Store:
    def __init__(self, public: bool = False, worlds: list[str] | None = None):
        self.public = public                    # visitor limits on queries
        self.only = set(worlds or [])           # serve only these environments (all when empty)
        self.cache = _Cached()
        self.worlds_meta = _load_yaml(CONTENT / "worlds.yaml", {})
        self._dbs: dict[tuple, duckdb.DuckDBPyConnection] = {}
        self._db_lock = threading.Lock()
        CACHE.mkdir(exist_ok=True)

    # ---------- discovery ----------
    def builds(self) -> list[str]:
        """Built environments: out/<env>/ with world.duckdb and tasks.json, in the order of worlds.yaml."""
        found = [d.name for d in OUT.iterdir() if (d / "world.duckdb").exists() and (d / "tasks.json").exists()
                 and (not self.only or d.name in self.only)] if OUT.exists() else []
        return [w for w in self.worlds_meta if w in found] + sorted(set(found) - set(self.worlds_meta))

    def out_dir(self, world: str) -> Path:
        if world not in self.builds():
            raise KeyError(f"no environment {world}")
        return OUT / world

    def db_path(self, world) -> Path:
        return OUT / world / "world.duckdb"

    # ---------- database ----------
    def db(self, world) -> duckdb.DuckDBPyConnection:
        path = self.out_dir(world) / "world.duckdb"
        st = path.stat()
        # a rebuild writes a new file: the stamp makes a long-running server open the new database
        key = (world, st.st_ino, st.st_mtime_ns)
        with self._db_lock:
            con = self._dbs.get(key)
            if con is None:
                for k in [k for k in self._dbs if k[0] == world]:
                    del self._dbs[k]
                # public mode serves strangers: keep each database small enough to share the machine
                limits = {"memory_limit": "4GB", "threads": 4} if self.public else {"memory_limit": "24GB", "threads": 8}
                con = duckdb.connect(str(path), read_only=True, config=limits)
                try:
                    con.execute("LOAD spatial")
                except duckdb.Error:
                    pass
                con.execute("SET enable_external_access = false")
                con.execute("SET lock_configuration = true")
                self._dbs[key] = con
        return con

    def run_sql(self, world, sql, limit=2000, timeout=60.0) -> dict:
        cur = self.db(world).cursor()
        timer = threading.Timer(timeout, cur.interrupt)
        t0 = time.time()
        timer.start()
        try:
            rel = cur.sql(sql)
            if rel is None:
                return {"columns": [], "types": [], "rows": [], "truncated": False, "ms": int((time.time() - t0) * 1000)}
            cols, types = rel.columns, [str(t) for t in rel.types]
            rows = rel.fetchmany(limit + 1)
        except duckdb.InterruptException:
            raise TimeoutError(f"query stopped after {timeout:.0f} s")
        finally:
            timer.cancel()
        ms = int((time.time() - t0) * 1000)
        cur.close()
        return {"columns": cols, "types": types, "rows": [[jsonable(v) for v in r] for r in rows[:limit]],
                "truncated": len(rows) > limit, "ms": ms}

    def _cached_json(self, world, name, fn):
        """Cache an expensive result on disk, keyed by the database file."""
        db = self.db_path(world)
        path = CACHE / world / f"{name}.json"
        stamp = db.stat().st_mtime if db.exists() else 0
        if path.exists():
            hit = json.load(open(path))
            if hit.get("_stamp") == stamp:
                return hit["value"]
        val = fn()
        path.parent.mkdir(parents=True, exist_ok=True)
        json.dump({"_stamp": stamp, "value": val}, open(path, "w"), default=str)
        return val

    # ---------- environment description ----------
    def dictionary(self, world) -> dict:
        p = self.out_dir(world) / "catalog" / "data_dictionary.md"
        return self.cache.get(("dict", world), [p], lambda: parse_dictionary(p.read_text()) if p.exists()
                              else {"note": "", "tables": {}})

    def dictionary_md(self, world) -> str:
        p = self.out_dir(world) / "catalog" / "data_dictionary.md"
        return p.read_text() if p.exists() else ""

    def metrics(self, world) -> dict:
        p = self.out_dir(world) / "catalog" / "semantic_layer.yaml"
        return self.cache.get(("sem", world), [p], lambda: (_load_yaml(p, {}) or {}).get("metrics", {}))

    def documents(self, world) -> dict:
        """Public source documents and the provenance of every table."""
        c = self.out_dir(world) / "catalog"
        load = lambda f: json.load(open(c / f)) if (c / f).exists() else {}
        return {"documents": load("documents.json"), "sources": load("sources.json")}

    def tasks(self, world) -> list[dict]:
        """Tasks with their ground-truth reasoning paths and dependencies."""
        p = self.out_dir(world) / "tasks.json"
        return self.cache.get(("tasks", world), [p], lambda: json.load(open(p)))

    def world_summary(self, world) -> dict:
        meta = self.worlds_meta.get(world, {})
        tabs = self.tables(world)
        return {"world": world, "title": meta.get("title", world), "company": meta.get("company", ""),
                "domain": meta.get("domain", ""), "source": meta.get("source", ""), "summary": meta.get("summary", ""),
                "period": meta.get("period", ""), "n_tables": len(tabs), "n_rows": sum(t["rows"] for t in tabs),
                "n_tasks": len(self.tasks(world)),
                # kept for the front end, which can address seeds and several databases
                "seeds": [1], "default_seed": 1, "seeds_with_db": [1], "seeds_with_full": [], "has_all": True}

    def tables(self, world) -> list[dict]:
        if not self.db_path(world).exists():
            return []

        def fn():
            con = self.db(world).cursor()
            out = []
            for (t,) in con.execute("SELECT table_name FROM duckdb_tables() ORDER BY table_name").fetchall():
                n = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
                cols = con.execute("SELECT column_name, data_type FROM duckdb_columns() WHERE table_name = ? "
                                   "ORDER BY column_index", [t]).fetchall()
                out.append({"name": t, "rows": n, "columns": [{"name": c, "type": ty} for c, ty in cols]})
            return out
        tabs = self._cached_json(world, "tables", fn)
        dic = self.dictionary(world)["tables"]
        for t in tabs:
            d = dic.get(t["name"], {})
            t["description"] = d.get("description", "")
            for c in t["columns"]:
                c["description"] = d.get("columns", {}).get(c["name"])
        return tabs

    def profile(self, world, table) -> dict:
        """Column profiles: nulls, distinct values, range, and a histogram, monthly series or top values."""
        names = {t["name"]: t for t in self.tables(world)}
        if table not in names:
            raise KeyError(table)

        def fn():
            con = self.db(world).cursor()
            n = names[table]["rows"]
            cols = [(c["name"], c["type"]) for c in names[table]["columns"]]
            big = n > 2_000_000
            src = f'(SELECT * FROM "{table}" USING SAMPLE 5% (system))' if big else f'"{table}"'
            parts = []
            for c, ty in cols:
                q = f'"{c}"'
                mm = f"min({q})::VARCHAR, max({q})::VARCHAR" if ty != "BOOLEAN" else "NULL, NULL"
                avg = f"avg({q})" if NUMERIC.match(ty) else "NULL"
                parts.append(f"count({q}), approx_count_distinct({q}), {mm}, {avg}")
            row = con.execute(f'SELECT {", ".join(parts)} FROM "{table}"').fetchone() if cols else []
            out = []
            for i, (c, ty) in enumerate(cols):
                nn, nd, lo, hi, mean = row[i * 5:(i + 1) * 5]
                q = f'"{c}"'
                p = {"name": c, "type": ty, "null_pct": round(100 * (1 - nn / n), 3) if n else 0, "distinct": nd,
                     "min": (lo or "")[:60] if lo is not None else None, "max": (hi or "")[:60] if hi is not None else None,
                     "mean": jsonable(mean)}
                try:
                    if NUMERIC.match(ty) and nd and nd > 12:
                        lo_, hi_ = con.execute(f"SELECT approx_quantile({q}, 0.01), approx_quantile({q}, 0.99) FROM {src}").fetchone()
                        if lo_ is not None and hi_ is not None and hi_ > lo_:
                            w_ = (hi_ - lo_) / 24
                            h = con.execute(f"SELECT least(floor(({q} - {lo_}) / {w_}), 23) AS b, count(*) FROM {src} "
                                            f"WHERE {q} BETWEEN {lo_} AND {hi_} GROUP BY 1 ORDER BY 1").fetchall()
                            p["hist"] = {"lo": jsonable(lo_), "hi": jsonable(hi_), "sampled": big,
                                         "bins": [[float(lo_ + b * w_), int(k)] for b, k in h]}
                    elif TEMPORAL.match(ty) and nn:
                        span = (dt.date.fromisoformat(hi[:10]) - dt.date.fromisoformat(lo[:10])).days
                        unit = "day" if span <= 120 else ("month" if span <= 3700 else "year")
                        h = con.execute(f"SELECT date_trunc('{unit}', {q})::DATE, count(*) FROM \"{table}\" "
                                        f"WHERE {q} IS NOT NULL GROUP BY 1 ORDER BY 1").fetchall()
                        p["series"] = {"unit": unit, "points": [[jsonable(a), int(b)] for a, b in h]}
                    elif nn and (ty in ("VARCHAR", "BOOLEAN") or NUMERIC.match(ty)):
                        if ty == "VARCHAR" and max(len(lo or ""), len(hi or "")) > 120 or c.endswith("_geom"):
                            p["long_text"] = True
                        else:
                            top = con.execute(f"SELECT {q}::VARCHAR, count(*) FROM {src} GROUP BY 1 ORDER BY 2 DESC, 1 "
                                              f"LIMIT 10").fetchall()
                            tot = con.execute(f"SELECT count(*) FROM {src}").fetchone()[0]
                            p["top"] = {"values": [[a, int(b)] for a, b in top], "total": tot, "sampled": big}
                except (duckdb.Error, ValueError) as e:
                    p["error"] = str(e)[:200]
                out.append(p)
            return {"table": table, "rows": n, "sampled": big, "columns": out}
        return self._cached_json(world, f"profile_{table}", fn)

    # ---------- charts and maps ----------
    def views(self, world) -> list[dict]:
        p = CONTENT / "views.yaml"
        return self.cache.get(("views",), [p], lambda: _load_yaml(p, {}) or {}).get(world, [])

    def view_data(self, world, view_id) -> dict:
        if view_id.startswith("metric:"):
            m = self.metrics(world).get(view_id[7:])
            if not m:
                raise KeyError(view_id)
            sql = m["sql"]
        else:
            v = next((v for v in self.views(world) if v["id"] == view_id), None)
            if not v:
                raise KeyError(view_id)
            sql = v["sql"]
        key = "view_" + re.sub(r"[^a-zA-Z0-9_]", "_", view_id) + "_" + hashlib.md5(sql.encode()).hexdigest()[:8]
        return self._cached_json(world, key, lambda: self.run_sql(world, sql, limit=200000, timeout=300))

    def geo(self, world, layer) -> dict:
        layers = {
            "zip_codes": "SELECT zip_code AS id, zip_code AS name, ST_AsGeoJSON(ST_SimplifyPreserveTopology("
                         "ST_GeomFromText(zip_code_geom), 0.0004)) AS g FROM zip_codes",
            "taxi_zones": "SELECT zone_id::VARCHAR AS id, zone || ' (' || borough || ')' AS name, borough AS grp, "
                          "ST_AsGeoJSON(ST_SimplifyPreserveTopology(ST_GeomFromText(zone_geom), 0.0004)) AS g "
                          "FROM taxi_zones",
            "states": "SELECT uf AS id, name, region AS grp, ST_AsGeoJSON(ST_SimplifyPreserveTopology("
                      "ST_GeomFromText(state_geom), 0.01)) AS g FROM states",
        }
        if layer not in layers:
            raise KeyError(layer)

        def fn():
            res = self.run_sql(world, layers[layer], limit=10000, timeout=120)
            cols = res["columns"]
            feats = []
            for r in res["rows"]:
                rec = dict(zip(cols, r))
                if rec["g"]:
                    feats.append({"type": "Feature", "id": rec["id"], "geometry": json.loads(rec["g"]),
                                  "properties": {k: v for k, v in rec.items() if k != "g"}})
            return {"type": "FeatureCollection", "features": feats}
        return self._cached_json(world, f"geo_{layer}", fn)
