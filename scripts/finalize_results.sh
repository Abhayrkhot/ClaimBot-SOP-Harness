#!/usr/bin/env bash
# After pulling runs from the cluster: rejudge every run with the current judge, gzip transcripts,
# and regenerate the README tables and the results figure. Run inside the dev container.
set -euo pipefail
cd "$(dirname "$0")/.."
for d in results/runs/*/; do
  name=$(basename "$d")
  case "$name" in
    smoke*) continue ;;
    v1-*) world=src/claimbot/claims/fixtures ;;
    *) world=evalsets/world ;;
  esac
  shopt -s nullglob
  files=("$d"*.jsonl "$d"*.jsonl.gz)
  [ ${#files[@]} -gt 0 ] && claimbot rejudge "${files[@]}" --world "$world"
done
scripts/pack_results.sh
for d in results/runs/*/; do  # per-run summaries, recomputed with the same judge as the README
  claimbot report "$d"*.jsonl.gz --json "$d/summary.json" --md "$d/summary.md" > /dev/null
done
python scripts/gen_readme.py
python scripts/plot_results.py
