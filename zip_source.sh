#!/bin/bash
set -euo pipefail

zip "srt_viewer_src.zip" \
    *.py \
    *.spec \
    .gitignore

echo "Created srt_viewer_src.zip"
