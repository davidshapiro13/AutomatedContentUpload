#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -x ".venv/bin/streamlit" ]; then
  echo "Streamlit is not installed in .venv."
  echo "Run this once from this folder:"
  echo "  .venv/bin/pip install -r requirements.txt"
  read -r -p "Press Enter to close..."
  exit 1
fi

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

echo "Starting Local Reels Uploader..."
echo "If the browser does not open, go to:"
echo "  http://localhost:8501"
echo

exec ".venv/bin/streamlit" run "uploader_app.py" --server.address localhost --server.port 8501
