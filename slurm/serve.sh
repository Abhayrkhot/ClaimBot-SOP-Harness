# Sourced by job scripts after env.sh: start and stop vLLM servers on this job's GPU.
#   start_vllm <model> <port> <gpu-mem-fraction> [extra vllm args...]   waits for /health
#   stop_vllms                                                          stops all of them
VLLM_PIDS=()
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
