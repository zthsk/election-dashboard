#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../backend"

PYTHON_BIN="${PYTHON_BIN:-python3}"

"$PYTHON_BIN" -m ingest.discover
exec uvicorn app:app --host 0.0.0.0 --port 8000 --reload
