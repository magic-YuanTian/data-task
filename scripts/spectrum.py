"""Print how the tasks of each built environment grow in horizon from level to level.

    python scripts/spectrum.py                  # every environment with an out/<env>/tasks.json
    python scripts/spectrum.py br_ecommerce     # one environment

For each level: the number of tasks and the mean (min-max) of direct dependencies, prerequisites (all the tasks a task
builds on, directly or through them), depth (tasks on the longest chain of dependencies) and total steps (the steps
of the task and of all its prerequisites). The rules the builds enforce are in datataskbench/spectrum.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from datataskbench.levels import LEVEL_NAMES  # noqa: E402
from datataskbench.spectrum import problems, summary  # noqa: E402

COLS = ["dependencies", "prerequisites", "depth", "total_steps"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("envs", nargs="*", help="environment names (default: all built)")
    a = ap.parse_args()
    names = a.envs or sorted(p.parent.name for p in (ROOT / "out").glob("*/tasks.json"))
    for n in names:
        tasks = json.load(open(ROOT / "out" / n / "tasks.json"))
        print(f"\n{n}: {len(tasks)} tasks")
        print(f"  {'level':<14}{'tasks':>6}" + "".join(f"{c:>22}" for c in COLS))
        for r in summary(tasks):
            cell = lambda v: f"{v[0]:.1f} ({v[1]}-{v[2]})"
            print(f"  {r['level'] + ' ' + LEVEL_NAMES[r['level']]:<14}{r['tasks']:>6}" + "".join(f"{cell(r[c]):>22}" for c in COLS))
        for p in problems(tasks):
            print(f"  rule broken: {p}")


if __name__ == "__main__":
    main()
