# Sourced by every Explorer job: paths, caches and helpers.
#   in_image <cmd...>   run inside the vLLM image (vLLM uses the image's own Python)
#   cb <args...>        run the claimbot CLI from this project's venv (separate from vLLM's packages)
export CB_ROOT=${CB_ROOT:-/scratch/$USER/claimbot}
export CB_SIF=${CB_SIF:-/scratch/$USER/icp/vllm.sif}
export HF_HOME=$CB_ROOT/hf HF_HUB_CACHE=$CB_ROOT/hf/hub HF_HUB_DISABLE_TELEMETRY=1
[ "${CB_ONLINE:-0}" = 1 ] || export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export XDG_CACHE_HOME=$CB_ROOT/cache TRITON_CACHE_DIR=$CB_ROOT/cache/triton VLLM_CACHE_ROOT=$CB_ROOT/cache/vllm
export TORCHINDUCTOR_CACHE_DIR=$CB_ROOT/cache/inductor PIP_CACHE_DIR=$CB_ROOT/pip-cache
export PYTHONNOUSERSITE=1 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1  # ~/.local has an old torch
export no_proxy=localhost,127.0.0.1${no_proxy:+,$no_proxy} NO_PROXY=localhost,127.0.0.1${NO_PROXY:+,$NO_PROXY}
mkdir -p "$CB_ROOT/logs" "$CB_ROOT/results" "$HF_HUB_CACHE" "$XDG_CACHE_HOME"
# The image's CUDA 13 user-mode driver needs driver >= 580; older nodes use its compat libs.
DRIVER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 | cut -d. -f1 || true)
export CB_CUDA_COMPAT=0
if [ -n "$DRIVER" ] && [ "$DRIVER" -lt 580 ]; then CB_CUDA_COMPAT=1; fi

in_image() {
  apptainer exec --nv --bind "/scratch/$USER" --pwd "$PWD" "$CB_SIF" bash -c '
    if [ "$CB_CUDA_COMPAT" = 1 ]; then export LD_LIBRARY_PATH=/usr/local/cuda/compat${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}; fi
    exec "$@"' _ "$@"
}
cb() { in_image "$CB_ROOT/venv/bin/python" -m claimbot.cli "$@"; }
export -f in_image cb
