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

compose_file="$(env_get COMPOSE_FILE "" .env)"
extra_models_setting="$(env_get COMFYUI_EXTRA_MODELS_PATH "" .env)"
external_network_setting="$(env_get COMFYUI_EXTERNAL_NETWORK "" .env)"
data_path="$(resolve_host_path "$(env_get COMFYUI_DATA_PATH ./data)" "${repo_root}")"
models_path="$(resolve_host_path "$(env_get COMFYUI_MODELS_PATH ./data/models)" "${repo_root}")"
extra_models_path=""

compose_has_layer() {
    local wanted="$1"
    local layer=""
    local trimmed=""
    local -a layers=()

    IFS=':' read -r -a layers <<< "${compose_file}"
    for layer in "${layers[@]}"; do
        trimmed="${layer#"${layer%%[![:space:]]*}"}"
        trimmed="${trimmed%"${trimmed##*[![:space:]]}"}"
        if [[ "${trimmed}" == "${wanted}" ]]; then
            return 0
        fi
    done
    return 1
}

if [[ -n "${extra_models_setting}" ]]; then
    extra_models_path="$(resolve_host_path "${extra_models_setting}" "${repo_root}")"
    echo
    echo "=== Extra model library ==="
    if compose_has_layer compose.extra-models.yaml; then
        echo "PASS  compose.extra-models.yaml enabled"
    else
        echo "FAIL  COMFYUI_EXTRA_MODELS_PATH is set but compose.extra-models.yaml is not enabled"
        echo "      rerun: bash scripts/init.sh"
        fail=1
    fi
    if [[ -d "${extra_models_path}" ]]; then
        echo "PASS  extra model library exists: ${extra_models_path}"
    else
        echo "FAIL  extra model library is missing: ${extra_models_path}"
        fail=1
    fi
    if [[ -d "${models_path}/external" ]]; then
        echo "PASS  nested extra-model mount point exists: ${models_path}/external"
    else
        echo "FAIL  nested extra-model mount point is missing: ${models_path}/external"
        echo "      rerun: bash scripts/init.sh"
        fail=1
    fi
elif compose_has_layer compose.extra-models.yaml; then
    echo
    echo "=== Extra model library ==="
    echo "FAIL  compose.extra-models.yaml is enabled but COMFYUI_EXTRA_MODELS_PATH is empty"
    echo "      set the path or rerun scripts/init.sh after removing the layer"
    fail=1
fi

if [[ -n "${external_network_setting}" ]] || compose_has_layer compose.external-network.yaml; then
    echo
    echo "=== External Docker network ==="
    if [[ -z "${external_network_setting}" ]]; then
        echo "FAIL  compose.external-network.yaml is enabled but COMFYUI_EXTERNAL_NETWORK is empty"
        fail=1
    elif ! compose_has_layer compose.external-network.yaml; then
        echo "FAIL  COMFYUI_EXTERNAL_NETWORK is set but compose.external-network.yaml is not enabled"
        printf '      parsed COMPOSE_FILE=%q\n' "${compose_file}"
        fail=1
    elif docker network inspect "${external_network_setting}" >/dev/null 2>&1; then
        echo "PASS  external network exists: ${external_network_setting}"
    else
        echo "FAIL  external network does not exist: ${external_network_setting}"
        echo "      create it first or remove the external-network configuration"
        fail=1
    fi
fi

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
if [[ -n "${extra_models_path}" ]]; then
    df -h "${repo_root}" "${data_path}" "${models_path}" "${extra_models_path}" 2>/dev/null |
        awk 'NR == 1 || !seen[$1]++'
else
    df -h "${repo_root}" "${data_path}" "${models_path}" 2>/dev/null |
        awk 'NR == 1 || !seen[$1]++'
fi

echo
if [[ "${fail}" -eq 0 ]]; then
    cat <<'EOF'
=== Preflight passed ===
Next steps:
  docker compose build --pull comfyui
  docker compose up -d
  docker compose ps
EOF
else
    cat <<'EOF'
=== Preflight needs attention ===
Fix the FAIL items above, then rerun:
  bash scripts/preflight.sh
EOF
fi

exit "${fail}"
