#!/bin/sh
# Start command for an App Runner service built from the GitHub repository with the
# managed Python runtime — the path taken when you cannot create the IAM access role
# an ECR-based service needs. The Dockerfile path uses docker-entrypoint.sh instead;
# the two do the same three things.
#
# App Runner's revised build for Python 3.11 keeps only what the build wrote inside
# the source directory, so the build command installs the dependencies into ./deps
# and this script puts that directory on both paths before anything imports.
set -e

ROOT=$(cd "$(dirname "$0")/../.." && pwd)

export PYTHONPATH="$ROOT/deps"
export PATH="$ROOT/deps/bin:$PATH"

cd "$ROOT/backend"

# Both steps are no-ops against a database that is already prepared: `upgrade head`
# does nothing when the revision matches, and `--if-empty` seeds only when there are
# no users yet, so redeploying never wipes saved conversations or access grants.
# Set RUN_MIGRATIONS=false to leave the database untouched at boot.
if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "==> alembic upgrade head"
  alembic upgrade head
  echo "==> python seed.py --if-empty"
  python3 seed.py --if-empty
fi

echo "==> uvicorn on port ${PORT:-8080}"
exec python3 -m uvicorn main:app --host 0.0.0.0 --port "${PORT:-8080}"
