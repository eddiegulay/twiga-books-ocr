#!/usr/bin/env bash
# Build embedding-ready chunks:  ./ingest.sh [--books-dir DIR] [--only class-3] [--workers N]
set -euo pipefail
cd "$(dirname "$0")"
source .venv/bin/activate
exec python -m ingest "$@"
