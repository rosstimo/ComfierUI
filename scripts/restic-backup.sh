#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"
# shellcheck source=scripts/lib.sh
source scripts/lib.sh

restic_env="${RESTIC_ENV_FILE:-${repo_root}/config/restic.env}"
exclude_file="${RESTIC_EXCLUDE_FILE:-${repo_root}/config/restic-excludes.txt}"
if [[ ! -r "${restic_env}" ]]; then
    echo "ERROR: Missing ${restic_env}. Copy config/restic.env.example first." >&2
    exit 1
fi
if [[ ! -r "${exclude_file}" ]]; then
    echo "ERROR: Missing ${exclude_file}. Copy config/restic-excludes.txt.example first." >&2
    exit 1
fi

# shellcheck disable=SC1090
set -a
source "${restic_env}"
set +a

: "${RESTIC_REPOSITORY:?Set RESTIC_REPOSITORY}"
: "${RESTIC_PASSWORD_FILE:?Set RESTIC_PASSWORD_FILE}"
require_command restic
require_command docker
backup_tag="${RESTIC_TAG:-comfierui}"

data_path="$(resolve_host_path "$(env_get COMFYUI_DATA_PATH ./data)" "${repo_root}")"
models_path="$(resolve_host_path "$(env_get COMFYUI_MODELS_PATH ./data/models)" "${repo_root}")"
workflows_path="$(resolve_host_path "$(env_get COMFYUI_WORKFLOWS_PATH ./data/workflows)" "${repo_root}")"
extra_models_setting="$(env_get COMFYUI_EXTRA_MODELS_PATH "" .env)"
extra_models_path=""
if [[ -n "${extra_models_setting}" ]]; then
    extra_models_path="$(resolve_host_path "${extra_models_setting}" "${repo_root}")"
fi

if bool_true "${COMFYUI_BACKUP_EXTRA_MODELS:-false}" && [[ -z "${extra_models_path}" ]]; then
    echo "ERROR: COMFYUI_BACKUP_EXTRA_MODELS=true but COMFYUI_EXTRA_MODELS_PATH is not configured in .env." >&2
    exit 1
fi

# Fail before stopping ComfyUI when the repository cannot be opened with the
# configured credentials. This also catches an uninitialized repository.
echo "Checking restic repository access..."
restic snapshots --latest 1 >/dev/null

staging="$(mktemp -d "${TMPDIR:-/tmp}/comfierui-backup.XXXXXX")"
source_list="$(mktemp)"
was_running=false

cleanup() {
    rm -f "${source_list}"
    rm -rf "${staging}"
    if [[ "${was_running}" == true ]]; then
        docker compose up -d comfyui >/dev/null
    fi
}
trap cleanup EXIT

add_required() {
    [[ -e "$1" ]] || { echo "ERROR: Required backup source is missing: $1" >&2; exit 1; }
    printf '%s\n' "$1" >> "${source_list}"
}

add_optional() {
    if [[ -e "$1" ]]; then
        printf '%s\n' "$1" >> "${source_list}"
    else
        echo "WARN: Optional backup source is missing: $1"
    fi
}

container_id="$(docker compose ps -q comfyui 2>/dev/null || true)"

state_manifest="${staging}/state/current.json"
python3 "${repo_root}/docker/capture-state.py" \
    --output "${state_manifest}" \
    --custom-nodes "${data_path}/custom_nodes" \
    --runtime-state "${data_path}/user/.comfierui/runtime-state.json" \
    --repository "${repo_root}" \
    --compose-files "$(env_get COMPOSE_FILE compose.yaml)" \
    >/dev/null
state_capture_id="$(python3 - "${state_manifest}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as state_file:
    print(json.load(state_file)["capture_id"])
PY
)"
add_required "${state_manifest}"

{
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'state_capture_id=%s\n' "${state_capture_id}"
    printf 'deployment_git_head=%s\n' "$(git rev-parse HEAD 2>/dev/null || echo unavailable)"
    printf 'deployment_git_dirty=%s\n' "$(git status --porcelain 2>/dev/null | grep -q . && echo yes || echo no)"
    printf 'compose_project=%s\n' "$(env_get COMPOSE_PROJECT_NAME comfierui)"
    printf 'compose_files=%s\n' "$(env_get COMPOSE_FILE compose.yaml)"
    if [[ -n "${container_id}" ]]; then
        printf 'container_id=%s\n' "${container_id}"
        printf 'image_id=%s\n' "$(docker inspect -f '{{.Image}}' "${container_id}" 2>/dev/null || echo unavailable)"
        printf 'comfyui_commit=%s\n' "$(docker exec "${container_id}" cat /opt/comfyui-commit 2>/dev/null || echo unavailable)"
    else
        printf 'container_id=not-created\n'
    fi
} > "${staging}/recovery-manifest.txt"
add_required "${staging}/recovery-manifest.txt"

if [[ ! -f "${repo_root}/.env" ]]; then
    echo "ERROR: Missing ${repo_root}/.env. Run scripts/init.sh first." >&2
    exit 1
fi

if bool_true "${COMFYUI_BACKUP_LOCAL_CONFIG:-true}"; then
    add_required "${repo_root}/.env"
    add_required "${repo_root}/Dockerfile"
    add_required "${repo_root}/docker"
    compose_file_value="$(env_get COMPOSE_FILE compose.yaml)"
    IFS=':' read -r -a compose_files <<<"${compose_file_value}"
    for compose_file in "${compose_files[@]}"; do
        [[ -n "${compose_file}" ]] && add_required "$(resolve_host_path "${compose_file}" "${repo_root}")"
    done
    add_optional "${repo_root}/config/extra_model_paths.yaml"
fi

bool_true "${COMFYUI_BACKUP_CUSTOM_NODES:-true}" && add_required "${data_path}/custom_nodes"
bool_true "${COMFYUI_BACKUP_USER:-true}" && add_required "${data_path}/user"
bool_true "${COMFYUI_BACKUP_WORKFLOWS:-true}" && add_required "${workflows_path}"
bool_true "${COMFYUI_BACKUP_INPUT:-true}" && add_optional "${data_path}/input"
bool_true "${COMFYUI_BACKUP_OUTPUT:-false}" && add_optional "${data_path}/output"
bool_true "${COMFYUI_BACKUP_MODELS:-false}" && add_required "${models_path}"
if bool_true "${COMFYUI_BACKUP_EXTRA_MODELS:-false}"; then
    add_required "${extra_models_path}"
fi

if bool_true "${COMFYUI_BACKUP_STOP_SERVICE:-true}"; then
    if docker compose ps --status running --services | grep -qx comfyui; then
        was_running=true
        docker compose stop comfyui
    fi
fi

if bool_true "${COMFYUI_BACKUP_PYTHON_VOLUME:-false}"; then
    volume_name="$(
        docker volume ls -q \
            --filter "label=com.docker.compose.project=$(env_get COMPOSE_PROJECT_NAME comfierui)" \
            --filter 'label=com.docker.compose.volume=comfyui-python' \
        | head -n1
    )"
    if [[ -z "${volume_name}" ]]; then
        echo "ERROR: Could not resolve the comfyui-python volume." >&2
        exit 1
    fi
    docker run --rm \
        -v "${volume_name}:/volume:ro" \
        -v "${staging}:/backup" \
        alpine:3.22 \
        tar -C /volume -cf /backup/comfyui-python.tar .
    add_required "${staging}/comfyui-python.tar"
fi

echo
printf 'Backup tag: %s\n' "${backup_tag}"
echo "Backup sources:"
sed 's/^/  /' "${source_list}"
echo
if bool_true "${COMFYUI_BACKUP_WORKFLOWS:-true}" \
    || bool_true "${COMFYUI_BACKUP_INPUT:-true}" \
    || bool_true "${COMFYUI_BACKUP_OUTPUT:-false}"; then
    echo "NOTE: This snapshot may contain credentials or private workflow/image metadata."
fi

restic backup \
    --files-from-verbatim "${source_list}" \
    --exclude-file "${exclude_file}" \
    --exclude-caches \
    --tag "${backup_tag}" \
    --tag "state:${state_capture_id}"

echo "Backup complete. Run scripts/restic-maintenance.sh on a separate schedule."
