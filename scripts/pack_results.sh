#!/usr/bin/env bash
# Gzip run transcripts before committing (report, rejudge and gen_readme read .jsonl.gz directly).
set -euo pipefail
cd "$(dirname "$0")/.."
find results/runs -name '*.jsonl' -print0 | xargs -0 -r gzip -9 -f
du -sh results
