#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"
# shellcheck source=scripts/lib.sh
source scripts/lib.sh

skip_gpu_container_test=false
if [[ "${1:-}" == --skip-gpu-container-test ]]; then
    skip_gpu_container_test=true
elif [[ $# -gt 0 ]]; then
    echo "Usage: $0 [--skip-gpu-container-test]" >&2
    exit 2
fi

fail=0
check_command() {
    if command -v "$1" >/dev/null 2>&1; then
        printf 'PASS  %s\n' "$1"
    else
        printf 'FAIL  %s not found\n' "$1"
        fail=1
    fi
}

echo "=== Commands ==="
for command in docker git curl python3; do
    check_command "${command}"
done

if docker compose version >/dev/null 2>&1; then
    echo "PASS  docker compose"
else
    echo "FAIL  docker compose plugin"
    fail=1
fi

echo
echo "=== Host architecture ==="
uname -m
case "$(uname -m)" in
    x86_64|aarch64|arm64) ;;
    *) echo "WARN  this architecture has not been documented or validated" ;;
esac

echo
echo "=== Compose configuration ==="
if docker compose config >/dev/null; then
    echo "PASS  docker compose config"
else
    echo "FAIL  docker compose config"
    fail=1
fi

echo
echo "=== Permissions ==="
if ! bash scripts/check-permissions.sh; then
    fail=1
fi

if [[ "$(env_get COMFYUI_ACCELERATOR nvidia)" == nvidia ]]; then
    echo
    echo "=== NVIDIA host ==="
    if command -v nvidia-smi >/dev/null 2>&1; then
        nvidia-smi -L
        gpu="$(env_get COMFYUI_GPU_DEVICE 0)"
        base_image="$(env_get COMFYUI_BASE_IMAGE nvidia/cuda:13.0.0-base-ubuntu24.04)"
        if [[ "${skip_gpu_container_test}" == false ]]; then
            echo "Testing NVIDIA Container Toolkit with ${base_image} ..."
            if docker run --rm --gpus "device=${gpu}" "${base_image}" nvidia-smi -L; then
                echo "PASS  NVIDIA container access"
            else
                echo "FAIL  NVIDIA container access"
                fail=1
            fi
        else
            echo "SKIP  NVIDIA container test"
        fi
    else
        echo "FAIL  .env selects NVIDIA but nvidia-smi is unavailable"
        fail=1
    fi
fi

echo
echo "=== Disk space ==="
data_path="$(resolve_host_path "$(env_get COMFYUI_DATA_PATH ./data)" "${repo_root}")"
models_path="$(resolve_host_path "$(env_get COMFYUI_MODELS_PATH ./data/models)" "${repo_root}")"
df -h "${repo_root}" "${data_path}" "${models_path}" 2>/dev/null | awk 'NR == 1 || !seen[$1]++'

exit "${fail}"
