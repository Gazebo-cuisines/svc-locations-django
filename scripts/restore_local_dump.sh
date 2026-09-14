#!/usr/bin/env bash
# Restore gazebo_locations_local from a pg_dump .sql.gz
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DUMP="${1:-$ROOT/gazebo_locations_20260913_0900_Europe-London.sql.gz}"
HOST="${PGHOST:-127.0.0.1}"
PORT="${PGPORT:-5432}"
USER="${PGUSER:-utsavgohel}"
DB="${PGDATABASE:-gazebo_locations_local}"

if [[ ! -f "$DUMP" ]]; then
  echo "dump not found: $DUMP" >&2
  exit 1
fi

psql -h "$HOST" -p "$PORT" -U "$USER" -d postgres -v ON_ERROR_STOP=1 \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$DB' AND pid <> pg_backend_pid();" \
  -c "DROP DATABASE IF EXISTS $DB;" \
  -c "CREATE DATABASE $DB OWNER $USER;"

gzip -dc "$DUMP" | sed '/^\\restrict /d' \
  | psql -h "$HOST" -p "$PORT" -U "$USER" -d "$DB" -v ON_ERROR_STOP=1

echo "restored $DUMP -> $DB @$HOST:$PORT"
