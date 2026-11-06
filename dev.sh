#!/usr/bin/env bash
# Levanta backend (8000), trabajador y frontend (5173). Ctrl+C detiene todo.
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
export JH_AI_MODE="${JH_AI_MODE:-none}"
export JH_EMBED_BACKEND="${JH_EMBED_BACKEND:-hash}"
export JH_DATABASE_URL="${JH_DATABASE_URL:-sqlite:///$ROOT/backend/jobhunter.db}"
cd "$ROOT/backend" && .venv/bin/uvicorn app.main:app --port 8000 --reload --reload-dir app &
BACK=$!
# El trabajador 24/7 ejecuta las búsquedas programadas (y, con ellas, la auto-postulación). Sin él solo busca lo que pulsas a mano.
cd "$ROOT/backend" && .venv/bin/python -m app.worker &
WORKER=$!
trap 'kill $BACK $WORKER 2>/dev/null' EXIT
cd "$ROOT/frontend" && npm run dev -- --host
