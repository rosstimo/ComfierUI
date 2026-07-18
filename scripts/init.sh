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

new_env=false
if [[ ! -f .env ]]; then
    new_env=true
    echo "Creating a minimal .env. See .env.example for every available setting."
else
    echo "Keeping existing .env and refreshing detected runtime values."
fi

config_get() {
    local key="$1"
    local fallback="${2-}"
    local value=""

    if [[ -f .env ]]; then
        value="$(env_get "${key}" "" .env)"
    fi
    if [[ -z "${value}" ]]; then
        value="$(env_get "${key}" "${fallback}" .env.example)"
    fi
    printf '%s\n' "${value}"
}

detect_timezone() {
    local timezone=""

    if [[ -n "${TZ:-}" ]]; then
        timezone="${TZ}"
    elif command -v timedatectl >/dev/null 2>&1; then
        timezone="$(timedatectl show --property=Timezone --value 2>/dev/null || true)"
    fi

    if [[ -z "${timezone}" && -r /etc/timezone ]]; then
        timezone="$(head -n1 /etc/timezone | tr -d '[:space:]')"
    fi

    if [[ -z "${timezone}" && -L /etc/localtime ]]; then
        timezone="$(readlink -f /etc/localtime | sed -n 's|.*/zoneinfo/||p')"
    fi

    printf '%s\n' "${timezone:-Etc/UTC}"
}

compose_with_accelerator() {
    local current="$1"
    local accelerator_layer="$2"

    python3 - "${current}" "${accelerator_layer}" <<'PY'
import sys

current = sys.argv[1]
accelerator = sys.argv[2]
layers = [item for item in current.split(":") if item]
layers = [
    item
    for item in layers
    if item not in {"compose.nvidia.yaml", "compose.cpu.yaml"}
]
if "compose.yaml" not in layers:
    layers.insert(0, "compose.yaml")
base_index = layers.index("compose.yaml")
layers.insert(base_index + 1, accelerator)
print(":".join(dict.fromkeys(layers)))
PY
}

compose_with_optional_layer() {
    local current="$1"
    local optional_layer="$2"
    local enabled="$3"

    python3 - "${current}" "${optional_layer}" "${enabled}" <<'PY'
import sys

current, optional_layer, enabled = sys.argv[1:4]
layers = [item for item in current.split(":") if item and item != optional_layer]
if enabled == "true":
    layers.append(optional_layer)
print(":".join(dict.fromkeys(layers)))
PY
}

project_name="$(config_get COMPOSE_PROJECT_NAME comfierui)"
bind_address="$(config_get COMFYUI_BIND_ADDRESS 127.0.0.1)"
port="$(config_get COMFYUI_PORT 8188)"
if [[ "${new_env}" == true ]]; then
    timezone="$(detect_timezone)"
else
    timezone="$(config_get TZ "$(detect_timezone)")"
fi
data_setting="$(config_get COMFYUI_DATA_PATH ./data)"
models_setting="$(config_get COMFYUI_MODELS_PATH ./data/models)"
workflows_setting="$(config_get COMFYUI_WORKFLOWS_PATH ./data/workflows)"
manager_legacy_ui="$(config_get COMFYUI_MANAGER_LEGACY_UI true)"
current_compose_file="$(config_get COMPOSE_FILE compose.yaml)"

shared_gid_setting="${COMFYUI_SHARED_GID:-}"
if [[ -z "${shared_gid_setting}" && -f .env ]]; then
    shared_gid_setting="$(env_get COMFYUI_SHARED_GID "" .env)"
fi
if [[ -z "${shared_gid_setting}" ]]; then
    shared_gid_setting="$(id -g)"
fi

extra_models_setting="${COMFYUI_EXTRA_MODELS_PATH:-}"
if [[ -z "${extra_models_setting}" && -f .env ]]; then
    extra_models_setting="$(env_get COMFYUI_EXTRA_MODELS_PATH "" .env)"
fi

cuda13_base="$(config_get COMFYUI_CUDA13_BASE_IMAGE nvidia/cuda:13.0.0-base-ubuntu24.04)"
cuda13_index="$(config_get COMFYUI_CUDA13_TORCH_INDEX_URL https://download.pytorch.org/whl/cu130)"
cuda12_base="$(config_get COMFYUI_CUDA12_BASE_IMAGE nvidia/cuda:12.6.3-base-ubuntu24.04)"
cuda12_index="$(config_get COMFYUI_CUDA12_TORCH_INDEX_URL https://download.pytorch.org/whl/cu126)"
cpu_base="$(config_get COMFYUI_CPU_BASE_IMAGE ubuntu:24.04)"
cpu_index="$(config_get COMFYUI_CPU_TORCH_INDEX_URL https://download.pytorch.org/whl/cpu)"

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
    selected_compose_file="$(compose_with_accelerator "${current_compose_file}" compose.cpu.yaml)"
    selected_accelerator=cpu
    selected_profile=none
    selected_gpu_device=""
    selected_base_image="${cpu_base}"
    selected_torch_index="${cpu_index}"
    echo "Configured CPU mode (experimental; representative workflow validation pending)."
}

configure_nvidia() {
    local profile="$1"
    selected_compose_file="$(compose_with_accelerator "${current_compose_file}" compose.nvidia.yaml)"
    selected_accelerator=nvidia
    selected_profile="${profile}"
    selected_gpu_device="${gpu_uuid}"

    case "${profile}" in
        cuda13)
            selected_base_image="${cuda13_base}"
            selected_torch_index="${cuda13_index}"
            ;;
        cuda12)
            selected_base_image="${cuda12_base}"
            selected_torch_index="${cuda12_index}"
            ;;
        *) return 2 ;;
    esac

    echo "Selected NVIDIA GPU: ${gpu_name} (${gpu_uuid})"
    echo "NVIDIA profile: ${profile}; driver ${gpu_driver}; compute capability ${gpu_compute}"
    if [[ "${profile}" == cuda12 ]]; then
        echo "Support tier: experimental; representative CUDA 12 hardware validation pending."
    else
        echo "Support tier: verified for the documented CUDA 13 test configuration."
    fi
}

if [[ "${mode}" == cpu ]]; then
    configure_cpu
elif [[ -z "${gpu_record}" ]]; then
    if [[ "${mode}" == auto ]]; then
        echo "No configured NVIDIA GPU profile detected."
        configure_cpu
        echo "AMD and Intel GPU acceleration is planned but not implemented; using CPU mode."
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
        echo "NVIDIA GPU found, but driver ${gpu_driver} is older than the configured NVIDIA profiles."
        configure_cpu
        echo "Upgrade the NVIDIA driver or add and validate a separate accelerator profile."
    fi
fi

if [[ -n "${extra_models_setting}" ]]; then
    selected_compose_file="$(compose_with_optional_layer "${selected_compose_file}" compose.extra-models.yaml true)"
else
    selected_compose_file="$(compose_with_optional_layer "${selected_compose_file}" compose.extra-models.yaml false)"
fi

if [[ "${new_env}" == true ]]; then
    cat > .env <<EOF
# ComfierUI local settings
# Full reference: .env.example and docs/CONFIGURATION.md

# Name, address, and port
COMPOSE_PROJECT_NAME=${project_name}
COMFYUI_BIND_ADDRESS=${bind_address}
COMFYUI_PORT=${port}
TZ=${timezone}

# Persistent host paths
COMFYUI_DATA_PATH=${data_setting}
COMFYUI_MODELS_PATH=${models_setting}
COMFYUI_WORKFLOWS_PATH=${workflows_setting}
EOF

    if [[ -n "${extra_models_setting}" ]]; then
        printf 'COMFYUI_EXTRA_MODELS_PATH=%s\n' "${extra_models_setting}" >> .env
    fi

    cat >> .env <<EOF

# Manager interface
# true provides server-side Install Models; false selects the newer interface
COMFYUI_MANAGER_LEGACY_UI=${manager_legacy_ui}

# Detected accelerator configuration
COMPOSE_FILE=${selected_compose_file}
COMFYUI_ACCELERATOR=${selected_accelerator}
COMFYUI_NVIDIA_PROFILE=${selected_profile}
EOF

    if [[ -n "${selected_gpu_device}" ]]; then
        printf 'COMFYUI_GPU_DEVICE=%s\n' "${selected_gpu_device}" >> .env
    fi

    cat >> .env <<EOF
COMFYUI_BASE_IMAGE=${selected_base_image}
TORCH_INDEX_URL=${selected_torch_index}

# Host identity and shared-file permissions
PUID=$(id -u)
PGID=$(id -g)
COMFYUI_SHARED_GID=${shared_gid_setting}

# Optional existing model library
# Add COMFYUI_EXTRA_MODELS_PATH above and rerun scripts/init.sh to enable it.

# Optional existing Docker network
# Append :compose.external-network.yaml to COMPOSE_FILE above, then add:
# COMFYUI_EXTERNAL_NETWORK=ai-services
EOF
    echo "Created minimal .env"
else
    env_set PUID "$(id -u)"
    env_set PGID "$(id -g)"
    env_set COMFYUI_SHARED_GID "${shared_gid_setting}"
    env_set COMPOSE_FILE "${selected_compose_file}"
    env_set COMFYUI_ACCELERATOR "${selected_accelerator}"
    env_set COMFYUI_NVIDIA_PROFILE "${selected_profile}"
    if [[ -n "${selected_gpu_device}" ]]; then
        env_set COMFYUI_GPU_DEVICE "${selected_gpu_device}"
    fi
    env_set COMFYUI_BASE_IMAGE "${selected_base_image}"
    env_set TORCH_INDEX_URL "${selected_torch_index}"
    if [[ -z "$(env_get COMFYUI_MANAGER_LEGACY_UI "" .env)" ]]; then
        env_set COMFYUI_MANAGER_LEGACY_UI "${manager_legacy_ui}"
    fi
    if [[ -z "$(env_get TZ "" .env)" ]]; then
        env_set TZ "${timezone}"
    fi
fi

data_path="$(resolve_host_path "$(env_get COMFYUI_DATA_PATH ./data)" "${repo_root}")"
models_path="$(resolve_host_path "$(env_get COMFYUI_MODELS_PATH ./data/models)" "${repo_root}")"
workflows_path="$(resolve_host_path "$(env_get COMFYUI_WORKFLOWS_PATH ./data/workflows)" "${repo_root}")"

extra_models_path=""
if [[ -n "${extra_models_setting}" ]]; then
    extra_models_path="$(resolve_host_path "${extra_models_setting}" "${repo_root}")"
    if [[ ! -d "${extra_models_path}" ]]; then
        echo "ERROR: COMFYUI_EXTRA_MODELS_PATH is not an existing directory: ${extra_models_path}" >&2
        exit 1
    fi
fi

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
# Pre-create parents of nested bind mounts. Otherwise Docker may create them as
# root before the non-root ComfyUI process starts.
ensure_directory "${data_path}/user/default"
ensure_directory "${models_path}"
if [[ -n "${extra_models_path}" ]]; then
    ensure_directory "${models_path}/external"
    echo "Configured read-only extra model library: ${extra_models_path}"
fi
ensure_directory "${workflows_path}"

docker compose config >/dev/null

echo
echo "Initialization complete."
echo "Review the short .env, then run:"
echo "  bash scripts/preflight.sh"
echo "  docker compose build --pull comfyui"
echo "  docker compose up -d"
