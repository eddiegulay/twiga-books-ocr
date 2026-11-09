#!/usr/bin/env bash
# One-time setup: Python 3.12 venv (PaddleOCR doesn't support 3.14) + CPU PaddlePaddle + PaddleOCR-VL + MLX server.
set -euo pipefail
cd "$(dirname "$0")/.."
command -v uv >/dev/null || { echo "uv not found: brew install uv"; exit 1; }
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install paddlepaddle==3.2.1 --index-url https://www.paddlepaddle.org.cn/packages/stable/cpu/ --index-strategy unsafe-best-match --extra-index-url https://pypi.org/simple
uv pip install -r requirements.txt pytest
python -c "import paddle, paddleocr, mlx_vlm; print('paddle', paddle.__version__, '| paddleocr', paddleocr.__version__, '| mlx_vlm ok')"
echo "Setup done. Next: put the books in ./books, then see README.md (python main.py --help)"
