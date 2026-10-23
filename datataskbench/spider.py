"""Comparison of a result with the Spider 2.0 gold results (one or more accepted variants per task)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


def _norm(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "∅"
    try:
        return f"{float(x):.1f}"
    except (TypeError, ValueError):
        return str(x).strip().lower()


def _close(a, b):
    try:
        a, b = float(a), float(b)
    except ValueError:
        return False
    return abs(a - b) <= 0.01 * max(1.0, abs(b))


def spider_match(df: pd.DataFrame, iid: str, cols: list[int], gold_dir: Path, gold_cols: list[int] | None = None) -> dict:
    """True if the result equals one Spider gold variant on the compared columns (row order ignored). gold_cols names
    the gold columns to compare when they sit at other positions than ours."""
    gcols = gold_cols or cols
    ours = sorted(tuple(_norm(v) for v in r) for r in df.iloc[:, cols].itertuples(index=False))
    tried = []
    files = sorted(gold_dir.glob(f"{iid}_*.csv")) or sorted(gold_dir.glob(f"{iid}.csv"))
    for f in files:
        g = pd.read_csv(f)
        if g.shape[1] <= max(gcols):
            continue
        theirs = sorted(tuple(_norm(v) for v in r) for r in g.iloc[:, gcols].itertuples(index=False))
        tried.append(f.name)
        if ours == theirs:
            return dict(match=True, variant=f.name)
        if len(ours) == len(theirs) and all(
                all(a == b or _close(a, b) for a, b in zip(r1, r2_)) for r1, r2_ in zip(ours, theirs)):
            return dict(match=True, variant=f.name)
    return dict(match=False, tried=tried)
