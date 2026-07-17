#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"
# shellcheck source=scripts/lib.sh
source scripts/lib.sh

mode="${1:-auto}"
case "${mode}" in
    auto|cpu|nvidia-cuda12|nvidia-cuda13) ;;
    *)
        echo "Usage: $0 [auto|cpu|nvidia-cuda12|nvidia-cuda13]" >&2
        exit 2
        ;;
esac

require_command docker
require_command git
require_command python3
require_command sort
docker compose version >/dev/null

if [[ "$(id -u)" == 0 && "${COMFYUI_ALLOW_ROOT_INIT:-false}" != true ]]; then
    echo "ERROR: Run initialization as the non-root user that should own ComfyUI data." >&2
    echo "Set COMFYUI_ALLOW_ROOT_INIT=true only when a root-run container is intentional." >&2
    exit 1
fi

if [[ ! -f .env ]]; then
    cp .env.example .env
    echo "Created .env from .env.example"
else
    echo "Keeping existing .env"
fi

env_set PUID "$(id -u)"
env_set PGID "$(id -g)"
env_set COMFYUI_SHARED_GID "$(id -g)"

cuda13_base="$(env_get COMFYUI_CUDA13_BASE_IMAGE nvidia/cuda:13.0.0-base-ubuntu24.04)"
cuda13_index="$(env_get COMFYUI_CUDA13_TORCH_INDEX_URL https://download.pytorch.org/whl/cu130)"
cuda12_base="$(env_get COMFYUI_CUDA12_BASE_IMAGE nvidia/cuda:12.6.3-base-ubuntu24.04)"
cuda12_index="$(env_get COMFYUI_CUDA12_TORCH_INDEX_URL https://download.pytorch.org/whl/cu126)"
cpu_base="$(env_get COMFYUI_CPU_BASE_IMAGE ubuntu:24.04)"
cpu_index="$(env_get COMFYUI_CPU_TORCH_INDEX_URL https://download.pytorch.org/whl/cpu)"

gpu_record=""
if command -v nvidia-smi >/dev/null 2>&1; then
    gpu_record="$(
        nvidia-smi \
            --query-gpu=uuid,name,memory.total,driver_version,compute_cap \
            --format=csv,noheader,nounits 2>/dev/null \
        | awk -F', *' 'NF >= 5 {print $3 "\t" $1 "\t" $2 "\t" $4 "\t" $5}' \
        | sort -nr \
        | head -n1
    )"
fi

configure_cpu() {
    env_set COMPOSE_FILE "compose.yaml:compose.cpu.yaml"
    env_set COMFYUI_ACCELERATOR cpu
    env_set COMFYUI_NVIDIA_PROFILE none
    env_set COMFYUI_BASE_IMAGE "${cpu_base}"
    env_set TORCH_INDEX_URL "${cpu_index}"
    echo "Configured CPU mode."
}

configure_nvidia() {
    local profile="$1"
    local base_image torch_index
    case "${profile}" in
        cuda13)
            base_image="${cuda13_base}"
            torch_index="${cuda13_index}"
            ;;
        cuda12)
            base_image="${cuda12_base}"
            torch_index="${cuda12_index}"
            ;;
        *) return 2 ;;
    esac
    env_set COMPOSE_FILE "compose.yaml:compose.nvidia.yaml"
    env_set COMFYUI_ACCELERATOR nvidia
    env_set COMFYUI_NVIDIA_PROFILE "${profile}"
    env_set COMFYUI_GPU_DEVICE "${gpu_uuid}"
    env_set COMFYUI_BASE_IMAGE "${base_image}"
    env_set TORCH_INDEX_URL "${torch_index}"
    echo "Selected NVIDIA GPU: ${gpu_name} (${gpu_uuid})"
    echo "NVIDIA profile: ${profile}; driver ${gpu_driver}; compute capability ${gpu_compute}"
}

if [[ "${mode}" == cpu ]]; then
    configure_cpu
elif [[ -z "${gpu_record}" ]]; then
    if [[ "${mode}" == auto ]]; then
        echo "No usable NVIDIA GPU detected."
        configure_cpu
        echo "AMD and Intel GPU containers are not currently implemented."
    else
        echo "ERROR: ${mode} requested, but nvidia-smi did not return a GPU." >&2
        exit 1
    fi
else
    gpu_uuid="$(cut -f2 <<<"${gpu_record}")"
    gpu_name="$(cut -f3 <<<"${gpu_record}")"
    gpu_driver="$(cut -f4 <<<"${gpu_record}")"
    gpu_compute="$(cut -f5 <<<"${gpu_record}")"

    if [[ "${mode}" == nvidia-cuda13 ]]; then
        if ! version_ge "${gpu_driver}" 580 || ! version_ge "${gpu_compute}" 7.5; then
            echo "ERROR: CUDA 13 requires a 580+ driver and Turing-or-newer GPU (compute capability 7.5+)." >&2
            exit 1
        fi
        configure_nvidia cuda13
    elif [[ "${mode}" == nvidia-cuda12 ]]; then
        if ! version_ge "${gpu_driver}" 525; then
            echo "ERROR: The CUDA 12 profile requires an NVIDIA driver from the 525 series or newer." >&2
            exit 1
        fi
        configure_nvidia cuda12
    elif version_ge "${gpu_driver}" 580 && version_ge "${gpu_compute}" 7.5; then
        configure_nvidia cuda13
    elif version_ge "${gpu_driver}" 525; then
        configure_nvidia cuda12
        echo "CUDA 12 compatibility mode selected because CUDA 13 requirements were not met."
    else
        echo "NVIDIA GPU found, but driver ${gpu_driver} is older than the supported profiles."
        configure_cpu
        echo "Upgrade the NVIDIA driver or define another tested accelerator profile manually."
    fi
fi

data_path="$(resolve_host_path "$(env_get COMFYUI_DATA_PATH ./data)" "${repo_root}")"
models_path="$(resolve_host_path "$(env_get COMFYUI_MODELS_PATH ./data/models)" "${repo_root}")"
workflows_path="$(resolve_host_path "$(env_get COMFYUI_WORKFLOWS_PATH ./data/workflows)" "${repo_root}")"

ensure_directory() {
    local path="$1"
    if [[ -e "${path}" && ! -d "${path}" ]]; then
        echo "ERROR: Expected a directory but found another file type: ${path}" >&2
        exit 1
    fi
    if [[ ! -e "${path}" ]]; then
        mkdir -p "${path}"
        chmod 2775 "${path}"
        echo "Created ${path}"
    fi
}

ensure_directory "${data_path}"
for directory in cache custom_nodes home input output temp user; do
    ensure_directory "${data_path}/${directory}"
done
ensure_directory "${models_path}"
ensure_directory "${workflows_path}"

docker compose config >/dev/null

echo
echo "Initialization complete."
echo "Review .env, then run:"
echo "  bash scripts/preflight.sh"
echo "  docker compose build --pull comfyui"
echo "  docker compose up -d"
