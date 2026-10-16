"""Compare the environments built in out/ with the published builds (reproduce/expected.json).

    python scripts/verify.py                 # compare every environment listed in expected.json that is built
    python scripts/verify.py --write         # record the builds in out/ as the expected ones (maintainers)

For each environment it compares every table of world.duckdb (row count and a content hash), the catalog files and
every task (question, reasoning path, gold answer and target response). Tasks that differ only beyond six significant
digits are reported separately from real differences. Exit status: 0 when every compared build is identical.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"
EXPECTED = ROOT / "reproduce" / "expected.json"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def rounded(x):
    """The value with every float rounded to six significant digits (to tell float noise from real changes)."""
    if isinstance(x, float):
        return x if x == 0 or not math.isfinite(x) else float(f"{x:.6g}")
    if isinstance(x, dict):
        return {k: rounded(v) for k, v in x.items()}
    if isinstance(x, list):
        return [rounded(v) for v in x]
    return x


def canon(x) -> str:
    return sha(json.dumps(x, sort_keys=True, ensure_ascii=False).encode())


def tables(db: Path) -> dict:
    import duckdb
    con = duckdb.connect(str(db), read_only=True)
    out = {}
    for (t,) in con.execute("SELECT table_name FROM duckdb_tables() ORDER BY 1").fetchall():
        n, h = con.execute(f'SELECT count(*), coalesce(sum(hash(t)::HUGEINT), 0)::VARCHAR FROM "{t}" t').fetchone()
        out[t] = [n, h]
    con.close()
    return out


def snapshot(env: Path) -> dict:
    tasks = {t["id"]: [canon(t), canon(rounded(t))] for t in json.load(open(env / "tasks.json"))}
    files = {str(f.relative_to(env)): sha(f.read_bytes()) for f in sorted((env / "catalog").glob("*")) if f.is_file()}
    return dict(tasks=tasks, files=files, tables=tables(env / "world.duckdb"))


def compare(want: dict, got: dict) -> list[str]:
    problems = []
    for kind in ("tables", "files"):
        for k in sorted(set(want[kind]) | set(got[kind])):
            if want[kind].get(k) != got[kind].get(k):
                problems.append(f"{kind[:-1]} {k}: " + ("missing" if k not in got[kind] else "unexpected"
                                if k not in want[kind] else "differs"))
    missing = sorted(set(want["tasks"]) - set(got["tasks"]))
    extra = sorted(set(got["tasks"]) - set(want["tasks"]))
    common = sorted(set(want["tasks"]) & set(got["tasks"]))
    real = [i for i in common if want["tasks"][i][1] != got["tasks"][i][1]]
    noise = [i for i in common if want["tasks"][i][0] != got["tasks"][i][0] and i not in real]
    if missing:
        problems.append(f"{len(missing)} tasks missing: {', '.join(missing)}")
    if extra:
        problems.append(f"{len(extra)} unexpected tasks: {', '.join(extra)}")
    if real:
        problems.append(f"{len(real)} tasks differ: {', '.join(real)}")
    if noise:
        problems.append(f"{len(noise)} tasks differ only beyond six significant digits: {', '.join(noise)}")
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="record the builds in out/ as expected")
    ap.add_argument("envs", nargs="*", help="environment names such as nyc_bikeshare (default: all)")
    a = ap.parse_args()
    built = lambda n: (OUT / n / "tasks.json").exists() and (OUT / n / "world.duckdb").exists()
    if a.write:
        names = a.envs or sorted(p.name for p in OUT.iterdir() if built(p.name))
        old = json.loads(EXPECTED.read_text()) if EXPECTED.exists() else {}
        old.update({n: snapshot(OUT / n) for n in names})
        EXPECTED.parent.mkdir(exist_ok=True)
        EXPECTED.write_text(json.dumps(old, indent=1, sort_keys=True) + "\n")
        print(f"recorded {', '.join(names)} in {EXPECTED.relative_to(ROOT)}")
        return
    expected = json.loads(EXPECTED.read_text())
    names = a.envs or [n for n in sorted(expected) if built(n)]
    if not names:
        sys.exit("nothing to compare: build an environment first")
    bad = 0
    for n in names:
        if n not in expected:
            print(f"{n}: not a published environment")
            bad += 1
            continue
        got = snapshot(OUT / n)
        problems = compare(expected[n], got)
        if problems:
            bad += 1
            print(f"DIFFERENT  {n}")
            for p in problems:
                print(f"           - {p}")
        else:
            print(f"identical  {n}: {len(got['tasks'])} tasks, {len(got['tables'])} tables, {len(got['files'])} catalog files")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
