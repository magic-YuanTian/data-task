"""How a task builds on lower-level tasks, and the rules that make the levels a spectrum of growing horizon.

A task at a higher level rests on the answers of lower-level tasks: a description on retrieved values, a discovery on
a description of the normal, a diagnosis on a discovery, a prediction on the drivers found by a diagnosis, and a
prescription on predicted consequences. Each environment's task build checks these rules and records each task's
horizon in tasks.json:

- prerequisites: the tasks it builds on, directly or through them;
- depth: the number of tasks on the longest chain of dependencies that ends at it (1 for a task with none);
- steps: the steps of its own reasoning path that are not the reuse of a sub-task answer;
- total_steps: those steps plus the steps of all its prerequisites, i.e. the length of the whole analysis.
"""
from __future__ import annotations

LEVELS = ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]

# Q1 and Q2 are the foundation (what the data mean, how to get values out of them); from Q3 on, a task uses at least
# one task of the level just below, so the shortest chain of dependencies grows by one with each level
ANCHOR = {"Q3": ("Q1", "Q2"), "Q4": ("Q3",), "Q5": ("Q4",), "Q6": ("Q5",), "Q7": ("Q6",)}
MIN_DIRECT = {"Q1": 0, "Q2": 0, "Q3": 1, "Q4": 2, "Q5": 2, "Q6": 3, "Q7": 3}
MIN_PREREQUISITES = {"Q1": 0, "Q2": 0, "Q3": 1, "Q4": 3, "Q5": 4, "Q6": 6, "Q7": 8}
MIN_OWN_STEPS = {"Q1": 1, "Q2": 1, "Q3": 1, "Q4": 2, "Q5": 2, "Q6": 2, "Q7": 2}


def own_steps(t) -> int:
    return sum(1 for s in t["steps"] if s.get("source") != "sub-task")


def horizon(tasks: list[dict]) -> dict[str, dict]:
    """For every task: its prerequisites (sorted by level, then id), depth, own steps and total steps."""
    by = {t["id"]: t for t in tasks}
    pre, depth = {}, {}

    def walk(i):
        if i not in pre:
            p, d = set(), 1
            for x in by[i]["depends_on"]:
                walk(x)
                p |= {x} | pre[x]
                d = max(d, depth[x] + 1)
            pre[i], depth[i] = p, d
        return pre[i]

    for i in by:
        walk(i)
    return {i: dict(prerequisites=sorted(pre[i], key=lambda x: (by[x]["level"], x)), depth=depth[i],
                    steps=own_steps(by[i]), total_steps=own_steps(by[i]) + sum(own_steps(by[x]) for x in pre[i]))
            for i in by}


def problems(tasks: list[dict]) -> list[str]:
    """Every way the tasks break the rules above (an empty list when they follow them)."""
    by = {t["id"]: t for t in tasks}
    out = []
    for t in tasks:
        if any(d not in by for d in t["depends_on"]):
            out.append(f"{t['id']}: unknown dependency {[d for d in t['depends_on'] if d not in by]}")
    if out:
        return out
    h = horizon(tasks)
    for t in tasks:
        i, lv = t["id"], t["level"]
        deps = [by[d] for d in t["depends_on"]]
        used = {s.get("uses") for s in t["steps"]} - {None}
        if up := [d["id"] for d in deps if LEVELS.index(d["level"]) > LEVELS.index(lv)]:
            out.append(f"{i}: depends on higher-level tasks {up}")
        if unused := [d["id"] for d in deps if d["id"] not in used]:
            out.append(f"{i}: no step uses {unused}")
        if stray := sorted(used - set(t["depends_on"])):
            out.append(f"{i}: steps use tasks it does not depend on {stray}")
        if lv in ANCHOR and not any(d["level"] in ANCHOR[lv] for d in deps):
            out.append(f"{i}: needs a {' or '.join(ANCHOR[lv])} task among its dependencies")
        if len(deps) < MIN_DIRECT[lv]:
            out.append(f"{i}: {len(deps)} dependencies, {lv} needs {MIN_DIRECT[lv]}")
        if len(h[i]["prerequisites"]) < MIN_PREREQUISITES[lv]:
            out.append(f"{i}: {len(h[i]['prerequisites'])} prerequisites, {lv} needs {MIN_PREREQUISITES[lv]}")
        if h[i]["steps"] < MIN_OWN_STEPS[lv]:
            out.append(f"{i}: {h[i]['steps']} own steps, {lv} needs {MIN_OWN_STEPS[lv]}")
    return out


def check(tasks: list[dict]):
    """Record each task's horizon in it, after checking the rules (raises when a task breaks one)."""
    if p := problems(tasks):
        raise ValueError(f"{len(p)} tasks break the level rules (datataskbench/spectrum.py):\n  " + "\n  ".join(p))
    for i, x in horizon(tasks).items():
        next(t for t in tasks if t["id"] == i)["horizon"] = x


def summary(tasks: list[dict]) -> list[dict]:
    """Per level: number of tasks and the mean (min-max) of direct dependencies, prerequisites, depth and total steps."""
    h = horizon(tasks)
    rows = []
    for lv in LEVELS:
        ts = [t for t in tasks if t["level"] == lv]
        if not ts:
            continue
        col = {"dependencies": [len(t["depends_on"]) for t in ts],
               "prerequisites": [len(h[t["id"]]["prerequisites"]) for t in ts],
               "depth": [h[t["id"]]["depth"] for t in ts],
               "total_steps": [h[t["id"]]["total_steps"] for t in ts]}
        rows.append(dict(level=lv, tasks=len(ts), **{k: (sum(v) / len(v), min(v), max(v)) for k, v in col.items()}))
    return rows
