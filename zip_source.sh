#!/bin/bash
set -euo pipefail

VERSION=$(grep "APP_VERSION" gui.py | head -1 | grep -o "'[^']*'" | tr -d "'")
OUTPUT="srt_viewer_src_v${VERSION}.zip"

zip "$OUTPUT" \
    *.py \
    *.spec \
    .gitignore

echo "Created $OUTPUT"
