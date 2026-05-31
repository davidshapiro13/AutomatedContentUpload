#!/bin/bash
set -euo pipefail

cd "/Users/davidshapiro/Desktop - Duck Duck Goose pro/Coding/Automated Reels"
set -a
source .env
set +a

.venv/bin/python main.py post-due --manifest manifests/manifest.csv --state state/post_state.json
