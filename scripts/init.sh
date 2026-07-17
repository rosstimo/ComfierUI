#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

command -v docker >/dev/null || {
    echo "ERROR: docker is not installed or is not in PATH." >&2
    exit 1
}

docker compose version >/dev/null || {
    echo "ERROR: The Docker Compose plugin is unavailable." >&2
    exit 1
}

command -v nvidia-smi >/dev/null || {
    echo "ERROR: nvidia-smi is not installed or is not in PATH." >&2
    exit 1
}

mkdir -p \
    data/cache \
    data/custom_nodes \
    data/home \
    data/input \
    data/models \
    data/output \
    data/temp \
    data/user/default \
    workflows

if [[ ! -f .env ]]; then
    cp .env.example .env
fi

puid="$(id -u)"
pgid="$(id -g)"

best_gpu_uuid="$({
    nvidia-smi \
        --query-gpu=uuid,memory.total \
        --format=csv,noheader,nounits
} | awk -F, '
    {
        uuid=$1
        memory=$2
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", uuid)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", memory)
        if (memory + 0 > best_memory + 0) {
            best_memory=memory
            best_uuid=uuid
        }
    }
    END { print best_uuid }
')"

[[ -n "${best_gpu_uuid}" ]] || {
    echo "ERROR: No NVIDIA GPU was detected." >&2
    exit 1
}

sed -i \
    -e "s/^COMFYUI_GPU_DEVICE=auto$/COMFYUI_GPU_DEVICE=${best_gpu_uuid}/" \
    -e "s/^PUID=.*/PUID=${puid}/" \
    -e "s/^PGID=.*/PGID=${pgid}/" \
    .env

printf 'Initialized %s\n' "${repo_root}"
printf 'GPU: %s\n' "$(grep '^COMFYUI_GPU_DEVICE=' .env | cut -d= -f2-)"
printf 'UID:GID: %s:%s\n' "${puid}" "${pgid}"
docker compose config >/dev/null

printf 'Compose configuration: valid\n'
printf 'Test URL: http://127.0.0.1:%s\n' "$(grep '^COMFYUI_PORT=' .env | cut -d= -f2-)"
