#!/usr/bin/env bash
# Starts the MLX VLM server (if not running), keeps the Mac awake, and runs the queue.
# Usage: ./parse.sh --test            (2-page smoke test)
#        ./parse.sh --n 1             (next book)
#        ./parse.sh --cls class-7     (one class)
#        ./parse.sh                   (everything remaining)
#        ./parse.sh --status
set -euo pipefail
cd "$(dirname "$0")"
source .venv/bin/activate
mkdir -p logs
if ! curl -s -o /dev/null http://127.0.0.1:8111/ ; then
  echo "Starting MLX server on :8111 (log: logs/mlx_server.log)"
  nohup bash scripts/start_mlx_server.sh > logs/mlx_server.log 2>&1 &
  for i in $(seq 1 60); do curl -s -o /dev/null http://127.0.0.1:8111/ && break; sleep 2; done
fi
exec caffeinate -i python run_parse.py --backend mlx "$@"
