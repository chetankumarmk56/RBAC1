#!/bin/sh
# Container entrypoint: bring the schema up to date, seed a fresh database, serve.
set -e

# Every instance runs this, and App Runner may start more than one, so both steps
# are no-ops on a database that is already prepared: `upgrade head` does nothing
# when the revision matches, and `--if-empty` seeds only when there are no users
# yet — redeploying never wipes saved conversations or runtime access grants.
# Set RUN_MIGRATIONS=false to leave the database untouched at boot.
if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "==> alembic upgrade head"
  alembic upgrade head
  echo "==> python seed.py --if-empty"
  python seed.py --if-empty
fi

echo "==> uvicorn on port ${PORT:-8080}"
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8080}"
