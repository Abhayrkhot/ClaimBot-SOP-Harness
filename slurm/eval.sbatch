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
# When gpt-oss is also the agent it shares this server, so the server must parse tool calls too.
SIM_TOOLS=(); [ "$model" = "$SIM" ] && SIM_TOOLS=(--enable-auto-tool-choice --tool-call-parser openai)
start_vllm "$SIM" "$SIM_PORT" "${CB_SIM_UTIL:-0.54}" --max-model-len 16384 --max-num-seqs 128 "${SIM_TOOLS[@]}"
if [ "$model" != "$SIM" ]; then
  start_vllm "$model" "$AGENT_PORT" "${CB_AGENT_UTIL:-0.36}" --max-model-len 16384 --max-num-seqs 128 \
    --enable-auto-tool-choice --tool-call-parser "$TOOL_PARSER"
  AGENT_URL=http://127.0.0.1:$AGENT_PORT/v1
else
  AGENT_URL=http://127.0.0.1:$SIM_PORT/v1
fi
out=$CB_ROOT/results/runs/$tag
mkdir -p "$out"
# Preflight for the tool-calling baselines: the server must return a parsed tool call, or the run is void.
if printf '%s\n' "$@" | grep -qE '^(prompt_only|guarded_tools)$'; then
  curl -sf "$AGENT_URL/chat/completions" -H 'content-type: application/json' -d "$(python3 - "$model" <<'PY'
import json, sys
print(json.dumps({"model": sys.argv[1], "max_tokens": 600, "messages": [
    {"role": "system", "content": "Use the tools."},
    {"role": "user", "content": "Please verify me: full name Jane Doe, date of birth 1990-01-01, SSN last four 1234."}],
  "tools": [{"type": "function", "function": {"name": "verify_identity", "description": "Verify a caller",
    "parameters": {"type": "object", "properties": {"full_name": {"type": "string"},
      "date_of_birth": {"type": "string"}, "ssn_last4": {"type": "string"}}, "required": []}}}]}))
PY
)" | python3 -c 'import json, sys; m = json.load(sys.stdin)["choices"][0]["message"]; c = m.get("tool_calls") or []; print("preflight tool call:", c[0]["function"]["name"] if c else None); sys.exit(0 if c else 1)' \
    || { echo "tool-calling preflight failed for $model; not running baselines" >&2; exit 1; }
fi
for agent in "$@"; do
  cb eval --agent "$agent" --world evalsets/world --scenarios "${CB_SCENARIOS:-evalsets/scenarios.jsonl}" \
    --out "$out/$agent.jsonl" --agent-url "$AGENT_URL" --agent-model "$model" --agent-extra "$AGENT_EXTRA" \
    --sim-url http://127.0.0.1:$SIM_PORT/v1 --sim-model "$SIM" --sim-extra '{"reasoning_effort": "low"}' \
    --concurrency "${CB_CONCURRENCY:-48}" ${CB_LIMIT:+--limit "$CB_LIMIT"} --label "$tag"
done
cb report "$out"/*.jsonl --json "$out/summary.json" --md "$out/summary.md"
