#!/usr/bin/env bash
# Start the MLX-VLM recognition server for PaddleOCR-VL on Apple Silicon.
# Official guide: https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PaddleOCR-VL-Apple-Silicon.html
set -euo pipefail
PORT="${MLX_VLM_PORT:-8111}"
echo "Starting mlx_vlm.server on port ${PORT}"
echo "Model is loaded on first client request (vl_rec_api_model_name)."
exec python -m mlx_vlm.server --port "${PORT}"
