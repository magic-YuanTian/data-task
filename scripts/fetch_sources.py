"""Download the public source files that are too large or not ours to keep in the repository.

    python scripts/fetch_sources.py            # everything below, skipping files that are already present
    python scripts/fetch_sources.py --check    # only verify the checksums of files that are present

Every file is checked against the SHA-256 of the copy the published environment was built from. A mismatch means
the publisher changed the file; the build would then differ from the published one, so the script stops (pass
--allow-changed to continue anyway).

| File | Source | Size |
|---|---|---|
| tl_2019_us_zcta510.zip | U.S. Census TIGER/Line 2019 ZIP Code Tabulation Areas | 534 MB |
| {2013..2018}-citibike-tripdata.zip | NYC bike share trip histories (Lyft Bikes and Scooters, LLC) | 3.2 GB |

The trip archives are converted to monthly parquet files by `python -m envs.nyc_bikeshare.ingest`, which deletes them
afterwards. Everything else the environment needs (weather, taxi zones and daily taxi pickups, ZIP neighborhoods,
station dock counts, the Spider 2.0 gold results) is small and kept in the repository.
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "sources" / "nyc" / "raw"
FILES = [
    # (path, url or None if it comes from an archive, sha256)
    (RAW / "tl_2019_us_zcta510.zip", "https://www2.census.gov/geo/tiger/TIGER2019/ZCTA5/tl_2019_us_zcta510.zip",
     "64cb2443430fffcf5cfa2cded86dbb62b02b5f8f0222d34baf8dd85e7ee425b2"),
]
TRIP_ZIPS = {
    2013: "4f9936239e741078da5c64206702240d15c5d57fdba2c16bb49b98f589b137b9",
    2014: "0c8468df81be760a87605cf6f83d89b5c30714632b0f7486899673e7fb227878",
    2015: "b6085d17d86d3f156bec59e76420a9d24a5fac83981a1634819be821968ed052",
    2016: "3f2a47babfe48f1397247861867bd1a6057f62d4564d1285f9739eb4b5b25495",
    2017: "d71ee823b0ef9fadf948a75b52ace6f6a5a363202f34e544f6092b80ab84241d",
    2018: "91f39eda364531181bb2b983afb69e6875106e5032c6d8418cee647115e386d7",
}
TRIP_URL = "https://s3.amazonaws.com/tripdata/{y}-citibike-tripdata.zip"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def download(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    print(f"downloading {url}\n         -> {dest.relative_to(ROOT)}", flush=True)
    subprocess.run(["curl", "-fL", "--retry", "5", "--retry-delay", "10", "-C", "-", "-o", str(tmp), url], check=True)
    tmp.rename(dest)


def verify(p: Path, want: str, allow: bool) -> bool:
    got = sha256(p)
    if got == want:
        print(f"ok       {p.relative_to(ROOT)}", flush=True)
        return True
    msg = f"CHANGED  {p.relative_to(ROOT)}: sha256 {got}, expected {want}"
    if not allow:
        sys.exit(msg + "\nThe publisher changed this file, so the build would differ from the published one. "
                       "Pass --allow-changed to continue anyway.")
    print(msg + " (continuing)", flush=True)
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="only verify files that are present")
    ap.add_argument("--allow-changed", action="store_true", help="continue when a checksum differs")
    a = ap.parse_args()

    for path, url, want in FILES:
        if path.exists():
            verify(path, want, a.allow_changed)
        elif a.check:
            print(f"missing  {path.relative_to(ROOT)}")
        else:
            download(url, path)
            verify(path, want, a.allow_changed)

    trips = ROOT / "sources" / "nyc" / "trips"
    for y, want in TRIP_ZIPS.items():
        z = RAW / f"{y}-citibike-tripdata.zip"
        if z.exists():
            verify(z, want, a.allow_changed)
        elif a.check:
            continue
        elif all((trips / f"{y}{m:02d}.parquet").exists() for m in range(7 if y == 2013 else 1, 13)):
            print(f"ok       trips {y} already converted to parquet", flush=True)
        else:
            download(TRIP_URL.format(y=y), z)
            verify(z, want, a.allow_changed)


if __name__ == "__main__":
    main()
