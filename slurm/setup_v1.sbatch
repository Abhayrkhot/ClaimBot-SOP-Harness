#!/usr/bin/env bash
#SBATCH --job-name=cb-setup-v1
#SBATCH --partition=short
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --time=00:30:00
#SBATCH --output=/scratch/%u/claimbot/logs/%x-%j.out
# Venv for the original v1 service (pushed with `scripts/explorer.sh push-v1`) from its own pins.
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
export CB_ONLINE=1
source slurm/env.sh
in_image bash -c '
  set -e
  [ -x "$CB_ROOT/venv-v1/bin/python" ] || python3 -m venv "$CB_ROOT/venv-v1"
  "$CB_ROOT/venv-v1/bin/pip" install -q -r "$CB_ROOT/v1/requirements.txt"
  cd "$CB_ROOT/v1" && "$CB_ROOT/venv-v1/bin/python" -c "import app.main; print(\"v1 imports ok\")"
'
