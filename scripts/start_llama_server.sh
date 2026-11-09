#!/usr/bin/env bash
# Optional faster VLM backend via llama.cpp (community-reported on M4).
# Official GGUF: https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6-GGUF
# Official Apple Silicon guide recommends mlx-vlm-server; this is an alternative.
set -euo pipefail
MODEL_DIR="${PADDLEOCR_VL_GGUF_DIR:-$HOME/models/PaddleOCR-VL-1.6-GGUF}"
PORT="${LLAMA_SERVER_PORT:-8080}"
GGUF="${MODEL_DIR}/PaddleOCR-VL-1.6-GGUF.gguf"
MMPROJ="${MODEL_DIR}/PaddleOCR-VL-1.6-GGUF-mmproj.gguf"
# Some HF snapshots use shorter names:
if [[ ! -f "$GGUF" ]]; then GGUF="${MODEL_DIR}/PaddleOCR-VL-1.6.gguf"; fi
if [[ ! -f "$MMPROJ" ]]; then MMPROJ="${MODEL_DIR}/PaddleOCR-VL-1.6-mmproj.gguf"; fi
if [[ ! -f "$GGUF" || ! -f "$MMPROJ" ]]; then
  echo "Missing GGUF files under ${MODEL_DIR}"
  echo "Download from https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6-GGUF"
  exit 1
fi
echo "Starting llama-server on port ${PORT}"
exec llama-server -m "$GGUF" --mmproj "$MMPROJ" --port "$PORT" --host 127.0.0.1 --temp 0
