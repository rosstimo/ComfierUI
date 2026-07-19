#!/bin/sh
set -eu

output_dir="${1:-/backups/state/recovery-blueprint}"
runtime_state=/source/data/user/.comfierui/runtime-state.json
rm -rf "${output_dir}"
mkdir -p "${output_dir}"

resolve_git_commit() {
    worktree="$1"
    gitdir="${worktree}/.git"

    if [ -f "${gitdir}" ]; then
        gitdir_value="$(sed -n 's/^gitdir: //p' "${gitdir}" | head -n1)"
        case "${gitdir_value}" in
            /*) gitdir="${gitdir_value}" ;;
            *) gitdir="${worktree}/${gitdir_value}" ;;
        esac
    fi

    [ -d "${gitdir}" ] || return 0
    [ -r "${gitdir}/HEAD" ] || return 0

    head_value="$(cat "${gitdir}/HEAD")"
    case "${head_value}" in
        ref:\ *)
            ref_name="${head_value#ref: }"
            if [ -r "${gitdir}/${ref_name}" ]; then
                cat "${gitdir}/${ref_name}"
            elif [ -r "${gitdir}/packed-refs" ]; then
                awk -v ref="${ref_name}" '$2 == ref {print $1; exit}' "${gitdir}/packed-refs"
            fi
            ;;
        *) printf '%s\n' "${head_value}" ;;
    esac
}

inventory_files() {
    root="$1"
    destination="$2"

    : > "${destination}"
    [ -d "${root}" ] || return 0

    find "${root}" -type f -print | sort | while IFS= read -r file; do
        relative="${file#${root}/}"
        size="$(stat -c '%s' "${file}" 2>/dev/null || printf 'unknown')"
        printf '%s\t%s\n' "${size}" "${relative}"
    done > "${destination}"
}

json_string() {
    expression="$1"
    fallback="${2:-unknown}"
    if [ -r "${runtime_state}" ]; then
        jq -r --arg fallback "${fallback}" "${expression} // \$fallback" "${runtime_state}"
    else
        printf '%s\n' "${fallback}"
    fi
}

json_bool() {
    expression="$1"
    if [ -r "${runtime_state}" ]; then
        jq -r "if (${expression}) == null then \"unknown\" else (${expression} | tostring) end" "${runtime_state}"
    else
        printf '%s\n' unknown
    fi
}

repo_commit="$(resolve_git_commit /source/repo || true)"
container_state_present=false
if [ -r "${runtime_state}" ]; then
    cp "${runtime_state}" "${output_dir}/container-state.json"
    container_state_present=true
else
    printf '{}\n' > "${output_dir}/container-state.json"
fi

cat > "${output_dir}/state.env" <<EOF
blueprint_schema=2
created_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
repository_commit=${repo_commit:-unknown}
compose_files=${COMFYUI_BACKUP_COMPOSE_FILES:-compose.yaml}
container_state_present=${container_state_present}
COMFYUI_COMMIT=$(json_string '.comfyui.commit')
COMFYUI_GIT_DESCRIBE=$(json_string '.comfyui.git_describe')
COMFYUI_REF=$(json_string '.comfyui.configured_ref_at_build')
COMFYUI_BUILD_ID=$(json_string '.image_build.build_id')
BASE_IMAGE=$(json_string '.image_build.base_image')
PYTHON_VERSION=$(json_string '.runtime.python')
PYTORCH_VERSION=$(json_string '.runtime.torch')
TORCHVISION_VERSION=$(json_string '.runtime.torchvision')
TORCHAUDIO_VERSION=$(json_string '.runtime.torchaudio')
TORCH_CUDA_BUILD=$(json_string '.runtime.torch_cuda_build' '')
TORCH_INDEX_URL=$(json_string '.image_build.torch_index_url')
COMFYUI_ACCELERATOR=$(json_string '.runtime.accelerator')
COMFYUI_MANAGER_ENABLED=$(json_bool '.manager.enabled')
COMFYUI_MANAGER_LEGACY_UI=$(json_bool '.manager.legacy_ui')
COMFYUI_MANAGER_SECURITY_LEVEL=$(json_string '.manager.security_level')
COMFYUI_MANAGER_NETWORK_MODE=$(json_string '.manager.network_mode')
include_config=${COMFYUI_BACKUP_INCLUDE_CONFIG:-true}
include_custom_nodes=${COMFYUI_BACKUP_INCLUDE_CUSTOM_NODES:-true}
include_user=${COMFYUI_BACKUP_INCLUDE_USER:-true}
include_workflows=${COMFYUI_BACKUP_INCLUDE_WORKFLOWS:-true}
include_input=${COMFYUI_BACKUP_INCLUDE_INPUT:-false}
include_output=${COMFYUI_BACKUP_INCLUDE_OUTPUT:-false}
include_models=${COMFYUI_BACKUP_INCLUDE_MODELS:-false}
include_extra_models=${COMFYUI_BACKUP_INCLUDE_EXTRA_MODELS:-false}
include_python=${COMFYUI_BACKUP_INCLUDE_PYTHON:-false}
exclude_larger_than=${COMFYUI_BACKUP_EXCLUDE_LARGER_THAN:-}
EOF

cat > "${output_dir}/README.txt" <<'EOF'
ComfierUI recovery blueprint

This directory describes the known state at backup time, including reproducible
or large items that may not be stored in the backup payload itself.

state.env             Compact recovery summary and backup coverage.
container-state.json  Authoritative build/runtime state reported by ComfyUI.
backup-includes.txt   Effective custom additional-source policy for this snapshot.
backup-excludes.txt   Effective restic exclusion policy for this snapshot.
custom-nodes.tsv      Custom-node directory names and Git commits when detectable.
python-packages.tsv   Installed Python package names and versions found in the venv.
models.tsv            Writable model-library file inventory (size and relative path).
extra-models.tsv      Extra/legacy model-library inventory when mounted for inventory.
input-files.tsv       Input file inventory, even when input files are excluded.
output-files.tsv      Output file inventory, even when output files are excluded.

The container state is written by the ComfyUI container itself from its baked
build metadata and actual installed runtime packages. The blueprint does not use
.env as the authority for effective ComfyUI or PyTorch versions.

Inventories are a recovery roadmap, not proof that excluded files are recoverable.
Model files are not hashed by default because hashing very large libraries would
turn a lightweight backup into an expensive full-disk read.

Host-specific settings such as GPU UUIDs, UID/GID values, bind addresses, and
absolute paths are not portable recovery pins. A fresh host should detect or
configure those values locally while reusing the recorded application state.
EOF

include_policy="${COMFYUI_BACKUP_INCLUDE_FILE:-/config/backup-includes.txt}"
exclude_policy="${COMFYUI_BACKUP_EXCLUDE_FILE:-/config/backup-excludes.txt}"
if [ -r "${include_policy}" ]; then
    cp "${include_policy}" "${output_dir}/backup-includes.txt"
else
    : > "${output_dir}/backup-includes.txt"
fi
if [ -r "${exclude_policy}" ]; then
    cp "${exclude_policy}" "${output_dir}/backup-excludes.txt"
else
    : > "${output_dir}/backup-excludes.txt"
fi

: > "${output_dir}/custom-nodes.tsv"
if [ -d /source/data/custom_nodes ]; then
    find /source/data/custom_nodes -mindepth 1 -maxdepth 1 -type d -print | sort | while IFS= read -r node_dir; do
        node_name="${node_dir##*/}"
        node_commit="$(resolve_git_commit "${node_dir}" || true)"
        printf '%s\t%s\n' "${node_name}" "${node_commit:-unknown}"
    done > "${output_dir}/custom-nodes.tsv"
fi

: > "${output_dir}/python-packages.tsv"
if [ -d /source/python/lib ]; then
    find /source/python/lib -type f -path '*/site-packages/*.dist-info/METADATA' -print | sort | while IFS= read -r metadata; do
        package_name="$(sed -n 's/^Name: //p' "${metadata}" | head -n1)"
        package_version="$(sed -n 's/^Version: //p' "${metadata}" | head -n1)"
        [ -n "${package_name}" ] || continue
        printf '%s\t%s\n' "${package_name}" "${package_version:-unknown}"
    done > "${output_dir}/python-packages.tsv"
fi

inventory_files /source/models "${output_dir}/models.tsv"
inventory_files /source/data/input "${output_dir}/input-files.tsv"
inventory_files /source/data/output "${output_dir}/output-files.tsv"

: > "${output_dir}/extra-models.tsv"
if [ -n "${COMFYUI_EXTRA_MODELS_PATH_CONFIGURED:-}" ] && [ -d /source/extra-models ]; then
    inventory_files /source/extra-models "${output_dir}/extra-models.tsv"
fi

printf '%s\n' "Recovery blueprint written to ${output_dir}"
