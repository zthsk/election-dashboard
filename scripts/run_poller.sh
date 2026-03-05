#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../backend"

PYTHON_BIN="${PYTHON_BIN:-python3}"

"$PYTHON_BIN" -m ingest.discover
exec "$PYTHON_BIN" -m ingest.poll
