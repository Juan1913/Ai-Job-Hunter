#!/bin/sh
# Entrada del contenedor: migra la BD y arranca el proceso indicado (uvicorn por defecto).
set -eu

if [ "${JH_RUN_MIGRATIONS:-true}" = "true" ]; then
  python /app/migrations/run.py
fi

exec "$@"
