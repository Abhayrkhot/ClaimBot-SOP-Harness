#!/usr/bin/env bash
# Mac-side helper for Explorer (git lives on the box; Explorer gets `git archive` copies of a commit).
#   scripts/explorer.sh push [ref]     copy a commit to /scratch/$USER/claimbot/src/<sha>, link src/current
#   scripts/explorer.sh run '<cmd>'    run a shell command on the login node in src/current
#   scripts/explorer.sh pull           copy /scratch/$USER/claimbot/results/runs/ into the box repo's results/runs/
# Connection: EXPLORER_HOST and EXPLORER_KEY from the environment or an untracked .explorer.env.
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
[ -f "$here/.explorer.env" ] && source "$here/.explorer.env"
: "${EXPLORER_HOST:?set EXPLORER_HOST (see header)}"
EXP=(ssh ${EXPLORER_KEY:+-i "$EXPLORER_KEY"} -o ServerAliveInterval=15 -o ConnectTimeout=90 "$EXPLORER_HOST")
REPO='~/Projects/ClaimBot-SOP-Harness'
cmd=${1:-}; shift || true
case "$cmd" in
  push)
    ref=${1:-HEAD}
    sha=$(ssh box "cd $REPO && git rev-parse --short=12 $ref")
    ssh box "cd $REPO && git archive --format=tar $ref" | "${EXP[@]}" "set -e
      d=/scratch/\$USER/claimbot/src/$sha; mkdir -p \$d /scratch/\$USER/claimbot/{data,results,logs}
      tar xf - -C \$d; echo $sha > \$d/GIT_SHA
      rm -rf \$d/data; ln -sfn /scratch/\$USER/claimbot/data \$d/data
      ln -sfn \$d /scratch/\$USER/claimbot/src/current; echo pushed \$d"
    ;;
  run)
    "${EXP[@]}" "cd \$(readlink -f /scratch/\$USER/claimbot/src/current) && $*"
    ;;
  pull)
    "${EXP[@]}" 'tar czf - -C /scratch/$USER/claimbot/results runs' | ssh box "mkdir -p $REPO/results && tar xzf - -C $REPO/results"
    ;;
  *) echo "usage: $0 push [ref] | run '<cmd>' | pull" >&2; exit 2 ;;
esac
