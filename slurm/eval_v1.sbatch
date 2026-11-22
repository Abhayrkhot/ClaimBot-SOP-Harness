#!/usr/bin/env bash
#SBATCH --job-name=cb-eval-v1
#SBATCH --partition=gpu
#SBATCH --gres=gpu:h200:1
#SBATCH --cpus-per-task=16
#SBATCH --mem=96G
#SBATCH --time=04:00:00
#SBATCH --output=/scratch/%u/claimbot/logs/%x-%j.out
# Before/after: the v1 service and the v2 harness on the same model, simulator and fixture-world scenarios.
#   sbatch slurm/eval_v1.sbatch <agent-model> <tag>
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
source slurm/env.sh
source slurm/serve.sh
echo "commit $(cat GIT_SHA 2>/dev/null)"; cb_version
model=$1; tag=$2
SIM=openai/gpt-oss-120b
start_vllm "$SIM" "$SIM_PORT" "${CB_SIM_UTIL:-0.54}" --max-model-len 16384 --max-num-seqs 128
start_vllm "$model" "$AGENT_PORT" "${CB_AGENT_UTIL:-0.36}" --max-model-len 16384 --max-num-seqs 128
# v1 reads its provider from the environment, exactly as in its README.
( cd "$CB_ROOT/v1" && OPENAI_API_KEY=eval LLM_BASE_URL=http://127.0.0.1:$AGENT_PORT/v1 LLM_MODEL="$model" \
    setsid bash -c 'in_image "$CB_ROOT/venv-v1/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port $V1_PORT --log-level warning' \
    > "$CB_ROOT/logs/v1-${SLURM_JOB_ID}.log" 2>&1 & echo $! > "$CB_ROOT/logs/v1-${SLURM_JOB_ID}.pid" )
V1_PID=$(cat "$CB_ROOT/logs/v1-${SLURM_JOB_ID}.pid")
trap 'kill -TERM -- "-$V1_PID" 2>/dev/null || true; stop_vllms' EXIT
for _ in $(seq 60); do curl -sf -o /dev/null http://127.0.0.1:$V1_PORT/api/health && break; sleep 2; done
curl -s http://127.0.0.1:$V1_PORT/api/health; echo
out=$CB_ROOT/results/runs/$tag
mkdir -p "$out"
requests() { curl -s "http://127.0.0.1:$AGENT_PORT/metrics" | awk '/^vllm:request_success_total/ {s += $2} END {printf "%d", s}'; }
for agent in v1 harness; do
  before=$(requests)
  cb eval --agent "$agent" --world src/claimbot/claims/fixtures --scenarios evalsets/fixture_scenarios.jsonl \
    --out "$out/$agent.jsonl" --agent-url http://127.0.0.1:$AGENT_PORT/v1 --agent-model "$model" \
    --sim-url http://127.0.0.1:$SIM_PORT/v1 --sim-model "$SIM" --sim-extra '{"reasoning_effort": "low"}' \
    --concurrency "${CB_CONCURRENCY:-32}" --v1-url http://127.0.0.1:$V1_PORT --label "$tag"
  # Model requests the agent server served during this system's run (v1 is a black box otherwise).
  echo "{\"agent\": \"$agent\", \"requests\": $(( $(requests) - before ))}" > "$out/$agent.requests.json"
done
cb report "$out"/*.jsonl --json "$out/summary.json" --md "$out/summary.md"
