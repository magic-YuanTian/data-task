"""Shared parts of an environment's task file: result tables, reasoning steps, the task registry, provenance and the
tasks.json writer. Each environment defines its tasks with TaskSet.task and writes them with TaskSet.build."""
from __future__ import annotations

import json
import re
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from .db import ordered
from .levels import LEVEL_NAMES, TASK_TYPES

TEXT = {"type": "text"}


class Env:
    """Read-only access to a built environment; the explorer runs the step queries on the same database."""

    def __init__(self, build: Path):
        self.build = build
        self.con = duckdb.connect(str(build / "world.duckdb"), read_only=True)
        self.con.execute("LOAD spatial")

    def q(self, sql):
        return self.con.execute(ordered(self.con, sql)).df()

    def v(self, sql):
        return self.con.execute(sql).fetchone()[0]


def tab(df: pd.DataFrame, digits=4, limit=None) -> dict:
    rows = []
    for r in (df if limit is None else df.head(limit)).itertuples(index=False):
        out = []
        for x in r:
            if isinstance(x, pd.Timestamp):
                x = str(x.date()) if x == x.normalize() else str(x)
            elif isinstance(x, (float, np.floating)):
                x = None if np.isnan(x) else float(f"{x:.{digits}g}")
            elif isinstance(x, (np.integer,)):
                x = int(x)
            elif isinstance(x, (np.bool_,)):
                x = bool(x)
            elif x is pd.NA:
                x = None
            elif hasattr(x, "isoformat"):
                x = x.isoformat()
            out.append(x)
        rows.append(out)
    res = {"columns": list(df.columns), "rows": rows}
    if limit is not None and len(df) > limit:
        res["note"] = f"first {limit} of {len(df)} rows"
    return res


def step(title, *, sql=None, doc=None, result=None, note=None, uses=None, source=None):
    src = source or ("sub-task" if uses else "db" if sql else "document" if doc else "calculation")
    return {k: v for k, v in dict(title=title, source=src, sql=sql.strip() if sql else None,
                                  doc=doc, result=result, note=note, uses=uses).items() if v is not None}


def pct(x, k=0):
    return f"{x * 100:.{k}f}%"


class TaskSet:
    def __init__(self, tables, sources):
        self.tasks = []
        self.tables = tables
        self.sources = sources

    def task(self, id, task_type, question, answer_format, depends_on=(), scoring=None, control=None, planted=False):
        def deco(fn):
            self.tasks.append(dict(id=id, task_type=task_type, question=question, answer_format=answer_format,
                                   depends_on=list(depends_on), control=control, planted=planted,
                                   scoring=scoring or {"metric": "key_fact_recall", "judge": "llm"}, fn=fn))
            return fn
        return deco

    def provenance(self, steps):
        used = set()
        for s in steps:
            for t in self.tables:
                if s.get("sql") and re.search(rf"\b{t}\b", s["sql"]):
                    used.add(t)
        out = [dict(table=t, **self.sources[t]) for t in sorted(used) if t in self.sources]
        for s in steps:
            if s.get("doc"):
                out.append(dict(document=s["doc"]["document"], url=s["doc"]["url"]))
            if s.get("source") == "benchmark":
                out.append(dict(benchmark="Spider 2.0", instance=s["result"].get("spider_instance")
                                if isinstance(s["result"], dict) else None))
        return out

    def build(self, env: Env, out_file: Path):
        out = []
        for t in self.tasks:
            steps, gold, resp = t["fn"](env)
            lv = t["task_type"].split(".")[0]
            out.append(dict(id=t["id"], level=lv, level_name=LEVEL_NAMES[lv], task_type=t["task_type"],
                            task_type_name=TASK_TYPES[t["task_type"]], question=t["question"],
                            answer_format=t["answer_format"], depends_on=t["depends_on"], control=t["control"],
                            planted=t["planted"], steps=steps, gold=gold, target_response=resp, scoring=t["scoring"],
                            provenance=self.provenance(steps)))
        byid = {x["id"]: x for x in out}
        for x in out:                   # a task also rests on the sources of the tasks it builds on
            seen, stack = [], list(x["depends_on"])
            while stack:
                d = stack.pop()
                if d not in seen:
                    seen.append(d)
                    stack += byid[d]["depends_on"]
            have = {json.dumps(p, sort_keys=True) for p in x["provenance"]}
            for d in seen:
                for p in byid[d]["provenance"]:
                    k = json.dumps(p, sort_keys=True)
                    if k not in have:
                        have.add(k)
                        x["provenance"].append(p)
        ids = {x["id"] for x in out}
        for x in out:
            assert set(x["depends_on"]) <= ids, x["id"]
            x["used_by"] = [y["id"] for y in out if x["id"] in y["depends_on"]]
            for s in x["steps"]:
                assert s.get("uses") is None or s["uses"] in x["depends_on"], (x["id"], s["uses"])
        out_file.write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str) + "\n")
        return out
