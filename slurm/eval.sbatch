#!/usr/bin/env bash
#SBATCH --job-name=cb-eval
#SBATCH --partition=gpu
#SBATCH --gres=gpu:h200:1
#SBATCH --cpus-per-task=16
#SBATCH --mem=96G
#SBATCH --time=08:00:00
#SBATCH --output=/scratch/%u/claimbot/logs/%x-%j.out
# One H200 hosts the agent model and the gpt-oss-120b caller simulator side by side.
#   sbatch slurm/eval.sbatch <agent-model> <tag> <agents...>
#   e.g. sbatch slurm/eval.sbatch Qwen/Qwen2.5-7B-Instruct qwen25-7b harness prompt_only guarded_tools
# Env: CB_SCENARIOS (default evalsets/scenarios.jsonl), CB_LIMIT, CB_CONCURRENCY (default 48),
#      CB_AGENT_UTIL (default 0.36), CB_SIM_UTIL (default 0.54).
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
source slurm/env.sh
source slurm/serve.sh
echo "commit $(cat GIT_SHA 2>/dev/null)"; cb_version
model=$1; tag=$2; shift 2
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
SIM=openai/gpt-oss-120b
case "$model" in
  Qwen/Qwen3-32B*) AGENT_EXTRA='{"chat_template_kwargs": {"enable_thinking": false}}' ;;
  openai/gpt-oss*) AGENT_EXTRA='{"reasoning_effort": "low"}' ;;
  *) AGENT_EXTRA='{}' ;;
esac
TOOL_PARSER=hermes; [[ "$model" == openai/gpt-oss* ]] && TOOL_PARSER=openai
start_vllm "$SIM" "$SIM_PORT" "${CB_SIM_UTIL:-0.54}" --max-model-len 16384 --max-num-seqs 128
if [ "$model" != "$SIM" ]; then
  start_vllm "$model" "$AGENT_PORT" "${CB_AGENT_UTIL:-0.36}" --max-model-len 16384 --max-num-seqs 128 \
    --enable-auto-tool-choice --tool-call-parser "$TOOL_PARSER"
  AGENT_URL=http://127.0.0.1:$AGENT_PORT/v1
else
  AGENT_URL=http://127.0.0.1:$SIM_PORT/v1
fi
out=$CB_ROOT/results/runs/$tag
mkdir -p "$out"
for agent in "$@"; do
  cb eval --agent "$agent" --world evalsets/world --scenarios "${CB_SCENARIOS:-evalsets/scenarios.jsonl}" \
    --out "$out/$agent.jsonl" --agent-url "$AGENT_URL" --agent-model "$model" --agent-extra "$AGENT_EXTRA" \
    --sim-url http://127.0.0.1:$SIM_PORT/v1 --sim-model "$SIM" --sim-extra '{"reasoning_effort": "low"}' \
    --concurrency "${CB_CONCURRENCY:-48}" ${CB_LIMIT:+--limit "$CB_LIMIT"} --label "$tag"
done
cb report "$out"/*.jsonl --json "$out/summary.json" --md "$out/summary.md"
