"""Repository paths. Relative paths in environment configs are resolved against the repository root."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"


def repo_path(p) -> Path:
    p = Path(p)
    return p if p.is_absolute() else ROOT / p
