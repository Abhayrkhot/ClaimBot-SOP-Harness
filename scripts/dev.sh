#!/usr/bin/env bash
# Run a command inside the claimdev container with the repo mounted at /src.
#   scripts/dev.sh 'uv run pytest -q'
# Env: CB_CPUS (docker --cpus, default 4), CB_NET (default host).
set -euo pipefail
cd "$(dirname "$0")/.."
image=claimdev:py3.12
if ! docker image inspect "$image" >/dev/null 2>&1; then
  docker build -q -t "$image" -f docker/dev.Dockerfile docker >/dev/null
fi
mkdir -p "$HOME/.cache/claimdev"
args=(--rm -i -v "$PWD:/src" -v "$HOME/.cache/claimdev:/cache" -w /src
      --user "$(id -u):$(id -g)" -e HOME=/cache --network "${CB_NET:-host}"
      --cpus "${CB_CPUS:-4}")
exec docker run "${args[@]}" "$image" bash -lc "$*"
