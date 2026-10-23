#!/usr/bin/env bash
# End-to-end reproduction of the DataTaskBench environments: Python environment, source data, build, tasks, check.
#
#   scripts/reproduce.sh                          # everything
#   scripts/reproduce.sh --env br_ecommerce       # one environment (repeat --env for several)
#   scripts/reproduce.sh --skip-env --skip-fetch  # reuse .venv and the downloaded sources
#   scripts/reproduce.sh --record                 # (maintainers) record the build as the published one
#
# Needs Python 3.11, curl, about 16 GB of free disk and a network connection. The build is compared with the
# published one (reproduce/expected.json): every table, the catalog and every task.
set -euo pipefail
cd "$(dirname "$0")/.."

SKIP_ENV=0
SKIP_FETCH=0
RECORD=0
ENVS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENVS="$ENVS $2"; shift 2 ;;
    --skip-env) SKIP_ENV=1; shift ;;
    --skip-fetch) SKIP_FETCH=1; shift ;;
    --record) RECORD=1; shift ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

step() { printf '\n== %s  (%s)\n' "$1" "$(date '+%H:%M:%S')"; }
PY=.venv/bin/python

if [ "$SKIP_ENV" = 0 ]; then
  step "1/4 Python environment in .venv"
  PYTHON=${PYTHON:-$(command -v python3.11 || command -v python3)}
  "$PYTHON" - <<'PYEOF'
import sys
if sys.version_info[:2] < (3, 11):
    sys.exit(f"Python 3.11 is needed, found {sys.version.split()[0]} (set PYTHON=/path/to/python3.11)")
PYEOF
  [ -x "$PY" ] || "$PYTHON" -m venv .venv
  "$PY" -m pip install -q --upgrade pip
  "$PY" -m pip install -q -r requirements.txt
  "$PY" -c "import duckdb; c = duckdb.connect(); c.execute('INSTALL spatial'); c.execute('INSTALL sqlite')"
fi

ENVS=${ENVS:-"nyc_bikeshare br_ecommerce"}
for e in $ENVS; do
  [ -f "envs/$e/build.py" ] || { echo "unknown environment: $e" >&2; exit 2; }
done

if [ "$SKIP_FETCH" = 0 ]; then
  step "2/4 Source data"
  # shellcheck disable=SC2086
  "$PY" scripts/fetch_sources.py $ENVS
  case " $ENVS " in *" nyc_bikeshare "*)
    "$PY" -m envs.nyc_bikeshare.ingest ;;      # trip archives -> sources/nyc/trips/YYYYMM.parquet (deletes the archives)
  esac
fi

for e in $ENVS; do
  step "3/4 Build: $e"
  "$PY" -m "envs.$e.build"
  "$PY" -m "envs.$e.tasks"
done

step "4/4 Check"
# shellcheck disable=SC2086
if [ "$RECORD" = 1 ]; then "$PY" scripts/verify.py --write $ENVS; else "$PY" scripts/verify.py $ENVS; fi
echo
echo "Browse it: .venv/bin/python ui/server.py  ->  http://127.0.0.1:8765"
