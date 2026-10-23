"""Download the public source files that are too large or not ours to keep in the repository.

    python scripts/fetch_sources.py                  # every environment, skipping files that are already present
    python scripts/fetch_sources.py br_ecommerce     # one environment
    python scripts/fetch_sources.py --check          # only verify the checksums of files that are present

Every file is checked against the SHA-256 of the copy the published environment was built from. A mismatch means
the publisher changed the file; the build would then differ from the published one, so the script stops (pass
--allow-changed to continue anyway).

| Environment | File | Source | Size |
|---|---|---|---|
| nyc_bikeshare | tl_2019_us_zcta510.zip | U.S. Census TIGER/Line 2019 ZIP Code Tabulation Areas | 534 MB |
| nyc_bikeshare | {2013..2018}-citibike-tripdata.zip | NYC bike share trip histories (Lyft Bikes and Scooters, LLC) | 3.2 GB |
| br_ecommerce | E_commerce.sqlite | Spider 2.0-lite local databases (archive of 457 MB; only this file is kept) | 113 MB |
| br_ecommerce | br_municipios.zip, br_unidades_da_federacao.zip | IBGE 2018 municipal and state meshes | 137 MB |
| br_ecommerce | base_de_dados_2010_2017_txt.zip | IBGE municipal GDP 2010-2017 | 5 MB |

The trip archives are converted to monthly parquet files by `python -m envs.nyc_bikeshare.ingest`, which deletes them
afterwards. Everything else the environments need is small and kept in the repository.
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "sources" / "nyc" / "raw"
BR = ROOT / "sources" / "br_ecommerce" / "raw"
FILES = {
    # (path, url, sha256)
    "nyc_bikeshare": [
        (RAW / "tl_2019_us_zcta510.zip", "https://www2.census.gov/geo/tiger/TIGER2019/ZCTA5/tl_2019_us_zcta510.zip",
         "64cb2443430fffcf5cfa2cded86dbb62b02b5f8f0222d34baf8dd85e7ee425b2"),
    ],
    "br_ecommerce": [
        (BR / "br_municipios.zip", "https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/malhas_municipais/"
         "municipio_2018/Brasil/BR/br_municipios.zip", "0b21b73384b67f2e8f55ab8ab3c810720260d8c3bcbcaa7e5ebb96fac521149f"),
        (BR / "br_unidades_da_federacao.zip", "https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/"
         "malhas_municipais/municipio_2018/Brasil/BR/br_unidades_da_federacao.zip",
         "7040de3116cef615841c1da21bc8f1300e6ba58b2ab3cc66b77089fd422b5da4"),
        (BR / "base_de_dados_2010_2017_txt.zip", "https://ftp.ibge.gov.br/Pib_Municipios/2017/base/base_de_dados_2010_2017_txt.zip",
         "71ac6c22f39c38ecb59d44a532f8a04c2eddad3337cac3ad6f6ff5487fa5bb6d"),
    ],
}
TRIP_ZIPS = {
    2013: "4f9936239e741078da5c64206702240d15c5d57fdba2c16bb49b98f589b137b9",
    2014: "0c8468df81be760a87605cf6f83d89b5c30714632b0f7486899673e7fb227878",
    2015: "b6085d17d86d3f156bec59e76420a9d24a5fac83981a1634819be821968ed052",
    2016: "3f2a47babfe48f1397247861867bd1a6057f62d4564d1285f9739eb4b5b25495",
    2017: "d71ee823b0ef9fadf948a75b52ace6f6a5a363202f34e544f6092b80ab84241d",
    2018: "91f39eda364531181bb2b983afb69e6875106e5032c6d8418cee647115e386d7",
}
TRIP_URL = "https://s3.amazonaws.com/tripdata/{y}-citibike-tripdata.zip"
# Spider 2.0-lite distributes its local SQLite databases as one archive (README of spider2-lite, "local database")
SPIDER_ZIP = (BR / "spider2_local_sqlite.zip",
              "https://drive.usercontent.google.com/download?id=1coEVsCZq-Xvj9p2TnhBFoFTsY-UoYGmG&export=download&confirm=t",
              "d56acf7c9d89be4bdf1f4f1281f4a03d91735f1858f6d6d0cfe0f9d562e3a94f")
SPIDER_DB = (BR / "E_commerce.sqlite", "49446afd935721ee12fc95316fbee9666a3e1bd4872dfa194fe4625d6762a81a")


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


def fetch_files(files, a):
    for path, url, want in files:
        if path.exists():
            verify(path, want, a.allow_changed)
        elif a.check:
            print(f"missing  {path.relative_to(ROOT)}")
        else:
            download(url, path)
            verify(path, want, a.allow_changed)


def fetch_nyc(a):
    fetch_files(FILES["nyc_bikeshare"], a)
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


def fetch_br(a):
    fetch_files(FILES["br_ecommerce"], a)
    db, want_db = SPIDER_DB
    if db.exists():
        verify(db, want_db, a.allow_changed)
        return
    if a.check:
        print(f"missing  {db.relative_to(ROOT)}")
        return
    z, url, want_zip = SPIDER_ZIP
    if not z.exists():
        download(url, z)
    verify(z, want_zip, a.allow_changed)
    with zipfile.ZipFile(z) as f:
        f.extract(db.name, db.parent)
    verify(db, want_db, a.allow_changed)
    z.unlink()                          # only E_commerce.sqlite is needed
    print(f"deleted  {z.relative_to(ROOT)} (kept {db.name})", flush=True)


ENVS = {"nyc_bikeshare": fetch_nyc, "br_ecommerce": fetch_br}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("envs", nargs="*", help=f"environments: {', '.join(ENVS)} (default: all)")
    ap.add_argument("--check", action="store_true", help="only verify files that are present")
    ap.add_argument("--allow-changed", action="store_true", help="continue when a checksum differs")
    a = ap.parse_args()
    unknown = set(a.envs) - set(ENVS)
    if unknown:
        ap.error(f"unknown environment: {', '.join(sorted(unknown))}")
    for name in a.envs or ENVS:
        print(f"== {name}", flush=True)
        ENVS[name](a)


if __name__ == "__main__":
    main()
