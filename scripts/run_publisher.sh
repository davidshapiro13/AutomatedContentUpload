#!/bin/bash
set -euo pipefail

cd "/Users/davidshapiro/Desktop - Duck Duck Goose pro/Coding/Automated Reels"
set -a
source .env
set +a

/usr/bin/python3 main.py post-due --manifest manifests/manifest.csv --state state/post_state.json
