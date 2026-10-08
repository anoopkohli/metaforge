#!/usr/bin/env bash
# Project-local Postgres 17 + pgvector for stage 3 (`make pg-up`, `make pg-down`).
# Data lives in .pgdata/ (gitignored). Unix socket only (no TCP listener), trust auth: dev use only.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
DATA="$ROOT/.pgdata"
PORT="${METAFORGE_PG_PORT:-54329}"
PGBIN="${PGBIN:-/opt/homebrew/opt/postgresql@17/bin}"
# Homebrew Postgres on macOS refuses to start ("postmaster became multithreaded") without a locale.
export LC_ALL="${LC_ALL:-en_US.UTF-8}"

if [ ! -x "$PGBIN/pg_ctl" ]; then
  echo "Postgres 17 not found at $PGBIN. Install it: brew install postgresql@17 pgvector" >&2
  exit 1
fi

case "${1:-}" in
  up)
    if [ ! -f "$DATA/PG_VERSION" ]; then
      "$PGBIN/initdb" -D "$DATA" -U metaforge_admin --auth=trust -E UTF8 --locale=C >/dev/null
    fi
    if ! "$PGBIN/pg_ctl" -D "$DATA" status >/dev/null 2>&1; then
      "$PGBIN/pg_ctl" -D "$DATA" -l "$DATA/server.log" -w \
        -o "-k $DATA -p $PORT -c listen_addresses=''" start >/dev/null
    fi
    if ! "$PGBIN/psql" -h "$DATA" -p "$PORT" -U metaforge_admin -d postgres -tAc \
        "SELECT 1 FROM pg_database WHERE datname = 'metaforge'" | grep -q 1; then
      "$PGBIN/createdb" -h "$DATA" -p "$PORT" -U metaforge_admin metaforge
    fi
    echo "postgres up (socket dir $DATA, port $PORT)"
    ;;
  down)
    if "$PGBIN/pg_ctl" -D "$DATA" status >/dev/null 2>&1; then
      "$PGBIN/pg_ctl" -D "$DATA" -m fast -w stop >/dev/null
    fi
    echo "postgres down"
    ;;
  psql)
    exec "$PGBIN/psql" -h "$DATA" -p "$PORT" -U metaforge_admin -d metaforge
    ;;
  *)
    echo "usage: $0 up|down|psql" >&2
    exit 2
    ;;
esac
