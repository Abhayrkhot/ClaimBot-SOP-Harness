# Sourced by job scripts after env.sh: start and stop vLLM servers on this job's GPU.
#   start_vllm <model> <port> <gpu-mem-fraction> [extra vllm args...]   waits for /health
#   stop_vllms                                                          stops all of them
VLLM_PIDS=()
# Several jobs can share one 8-GPU node, so every job gets its own ports. A fixed port once let a job's
# requests reach another job's model (404 for every turn); start_vllm now also checks the served model.
CB_PORT_BASE=$(( 20000 + (${SLURM_JOB_ID:-0} % 2000) * 10 ))
export AGENT_PORT=$CB_PORT_BASE SIM_PORT=$(( CB_PORT_BASE + 1 )) V1_PORT=$(( CB_PORT_BASE + 2 ))
start_vllm() {
  local model=$1 port=$2 util=$3; shift 3
  local log="$CB_ROOT/logs/vllm-${SLURM_JOB_ID:-local}-$(echo "${model##*/}" | tr '[:upper:]' '[:lower:]')-$port.log"
  setsid bash -c 'in_image vllm serve "$@"' _ "$model" --host 127.0.0.1 --port "$port" \
    --gpu-memory-utilization "$util" --disable-log-requests --uvicorn-log-level warning "$@" > "$log" 2>&1 &
  local pid=$!
  VLLM_PIDS+=($pid)
  echo "vllm $model :$port util $util pid $pid log $log"
  local deadline=$(( $(date +%s) + 1800 ))
  until curl -sf -o /dev/null "http://127.0.0.1:$port/health"; do
    if ! kill -0 "$pid" 2>/dev/null; then echo "vllm $model exited during startup" >&2; tail -40 "$log" >&2; return 1; fi
    [ "$(date +%s)" -gt "$deadline" ] && { echo "vllm $model startup timeout" >&2; tail -40 "$log" >&2; return 1; }
    sleep 5
  done
  local served
  served=$(curl -sf "http://127.0.0.1:$port/v1/models" | python3 -c "import sys, json; print(json.load(sys.stdin)['data'][0]['id'])")
  if [ "$served" != "$model" ]; then echo "port $port serves $served, not $model" >&2; return 1; fi
}
stop_vllms() {
  for p in "${VLLM_PIDS[@]}"; do kill -TERM -- "-$p" 2>/dev/null || kill -TERM "$p" 2>/dev/null || true; done
  for p in "${VLLM_PIDS[@]}"; do
    for _ in $(seq 60); do kill -0 -- "-$p" 2>/dev/null || break; sleep 1; done
    kill -KILL -- "-$p" 2>/dev/null || true
  done
  VLLM_PIDS=()
}
trap stop_vllms EXIT
