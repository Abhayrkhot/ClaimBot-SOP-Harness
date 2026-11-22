#!/usr/bin/env bash
#SBATCH --job-name=cb-setup
#SBATCH --partition=short
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --time=00:45:00
#SBATCH --output=/scratch/%u/claimbot/logs/%x-%j.out
# One-time (CPU node): a venv for claimbot inside the vLLM image, then the unit tests.
#   cd /scratch/$USER/claimbot/src/current && sbatch slurm/setup.sbatch
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
export CB_ONLINE=1
source slurm/env.sh
in_image bash -c '
  set -e
  [ -x "$CB_ROOT/venv/bin/python" ] || python3 -m venv "$CB_ROOT/venv"
  "$CB_ROOT/venv/bin/pip" install -q --upgrade pip
  "$CB_ROOT/venv/bin/pip" install -q -r slurm/requirements.lock pytest pytest-asyncio hypothesis
  "$CB_ROOT/venv/bin/pip" uninstall -y -q claimbot 2>/dev/null || true  # jobs import from their source tree
  PYTHONPATH="$SLURM_SUBMIT_DIR/src" "$CB_ROOT/venv/bin/python" -c "import claimbot, sys; print(claimbot.__file__, sys.version)"
  PYTHONPATH="$SLURM_SUBMIT_DIR/src" "$CB_ROOT/venv/bin/python" -m pytest -q -p no:cacheprovider "$SLURM_SUBMIT_DIR/tests" 2>&1 | tail -3
'
