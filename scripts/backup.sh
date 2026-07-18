#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"
# shellcheck source=scripts/lib.sh
source scripts/lib.sh

usage() {
    cat <<'EOF'
Usage: scripts/backup.sh COMMAND [ARGS]

Normal built-in backup commands:
  status                 Show backup services and recent backup logs
  now                    Stop ComfyUI if running, run an immediate backup,
                         then restart ComfyUI
  list                   List available backup snapshots
  inspect SNAPSHOT [PATH]
                         List files in a snapshot, optionally below PATH
  stage [SNAPSHOT]       Extract a snapshot to ./backups/restore for inspection
                         without changing the live deployment (defaults to latest)
  restore [SNAPSHOT]     Functionally restore the live deployment to a snapshot
                         (defaults to latest)
  check                  Verify the restic repository structure
  maintenance            Apply configured retention and prune old data
  logs                   Follow backup service logs

Examples:
  bash scripts/backup.sh now
  bash scripts/backup.sh list
  bash scripts/backup.sh inspect a1b2c3d4
  bash scripts/backup.sh stage a1b2c3d4
  bash scripts/backup.sh restore a1b2c3d4
  bash scripts/backup.sh restore latest
EOF
}

backup_running() {
    docker compose ps --status running --services 2>/dev/null | grep -qx backup
}

comfyui_running() {
    docker compose ps --status running --services 2>/dev/null | grep -qx comfyui
}

has_backup_service() {
    docker compose config --services 2>/dev/null | grep -qx backup
}

require_backup_running() {
    if ! backup_running; then
        echo "ERROR: The backup service is not running." >&2
        echo "Enable COMFYUI_BACKUP_ENABLED=true and start the Compose stack first." >&2
        exit 1
    fi
}

backup_command() {
    docker compose exec -T backup \
        /bin/sh /usr/local/bin/comfierui-backup "$@"
}

backup_host_path() {
    resolve_host_path "$(env_get COMFYUI_BACKUP_PATH ./backups)" "${repo_root}"
}

run_consistent_manual_backup() {
    local was_running=false

    if comfyui_running; then
        was_running=true
        echo "Stopping ComfyUI for a consistent manual backup..."
        docker compose stop comfyui
    fi

    cleanup_manual_backup() {
        if [[ "${was_running}" == true ]]; then
            echo "Starting ComfyUI..."
            docker compose up -d comfyui
        fi
    }
    trap cleanup_manual_backup EXIT INT TERM HUP

    backup_command backup-now

    cleanup_manual_backup
    trap - EXIT INT TERM HUP
}

stage_snapshot() {
    local snapshot="${1:-latest}"
    local backup_path stage_name stage_container stage_host

    backup_path="$(backup_host_path)"
    stage_name="$(date -u +%Y%m%dT%H%M%SZ)-${snapshot//[^A-Za-z0-9._-]/_}"
    stage_container="/backups/restore/${stage_name}"
    stage_host="${backup_path}/restore/${stage_name}"

    backup_command stage "${snapshot}" "${stage_container}"
    printf '%s\n' "${stage_host}"
}

replace_tree() {
    local source_path="$1"
    local destination_path="$2"

    [[ -d "${source_path}" ]] || return 0
    mkdir -p "${destination_path}"
    find "${destination_path}" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
    cp -a "${source_path}/." "${destination_path}/"
}

overlay_tree() {
    local source_path="$1"
    local destination_path="$2"

    [[ -d "${source_path}" ]] || return 0
    mkdir -p "${destination_path}"
    cp -a "${source_path}/." "${destination_path}/"
}

blueprint_value() {
    local stage_host="$1"
    local key="$2"
    local fallback="${3-}"
    local value=""
    local candidate

    for candidate in \
        "${stage_host}/backups/state/recovery-blueprint/state.env" \
        "${stage_host}/backups/state/recovery-manifest.txt"; do
        if [[ -r "${candidate}" ]]; then
            value="$(sed -n "s/^${key}=//p" "${candidate}" | tail -n1)"
            if [[ -n "${value}" ]]; then
                printf '%s\n' "${value}"
                return 0
            fi
        fi
    done

    printf '%s\n' "${fallback}"
}

blueprint_true() {
    local stage_host="$1"
    local key="$2"
    local fallback="${3:-false}"
    bool_true "$(blueprint_value "${stage_host}" "${key}" "${fallback}")"
}

snapshot_has_partial_policy() {
    local stage_host="$1"
    local exclude_policy="${stage_host}/backups/state/recovery-blueprint/backup-excludes.txt"
    local exclude_larger_than

    exclude_larger_than="$(blueprint_value "${stage_host}" exclude_larger_than "")"
    if [[ -n "${exclude_larger_than}" ]]; then
        return 0
    fi

    if [[ -r "${exclude_policy}" ]] && \
        grep -Ev '^[[:space:]]*($|#)' "${exclude_policy}" | grep -q .; then
        return 0
    fi

    return 1
}

restore_complete_tree_if_applicable() {
    local stage_host="$1"
    local source_path="$2"
    local destination_path="$3"
    local include_key="$4"
    local label="$5"
    local snapshot_partial="$6"

    [[ -d "${source_path}" ]] || return 0

    if blueprint_true "${stage_host}" "${include_key}" false && \
        [[ "${snapshot_partial}" == false ]]; then
        echo "Replacing ${label} from complete backed-up category..."
        replace_tree "${source_path}" "${destination_path}"
    fi
}

resolve_python_volume() {
    local project_name volume_name

    project_name="$(env_get COMPOSE_PROJECT_NAME comfierui)"
    volume_name="$(
        docker volume ls -q \
            --filter "label=com.docker.compose.project=${project_name}" \
            --filter 'label=com.docker.compose.volume=comfyui-python' \
        | head -n1
    )"

    if [[ -z "${volume_name}" ]]; then
        echo "Creating Compose resources so the Python volume exists..." >&2
        docker compose create comfyui >/dev/null
        volume_name="$(
            docker volume ls -q \
                --filter "label=com.docker.compose.project=${project_name}" \
                --filter 'label=com.docker.compose.volume=comfyui-python' \
            | head -n1
        )"
    fi

    if [[ -z "${volume_name}" ]]; then
        echo "ERROR: Could not resolve the comfyui-python volume for restore." >&2
        return 1
    fi

    printf '%s\n' "${volume_name}"
}

restore_python_volume() {
    local source_path="$1"
    local mode="${2:-replace}"
    local volume_name

    [[ -d "${source_path}" ]] || return 0
    volume_name="$(resolve_python_volume)"

    if [[ "${mode}" == replace ]]; then
        echo "Replacing backed-up Python environment volume..."
        docker run --rm \
            -v "${volume_name}:/target" \
            -v "${source_path}:/source:ro" \
            alpine:3.22 sh -c \
            'rm -rf /target/* /target/.[!.]* /target/..?*; cp -a /source/. /target/'
    else
        echo "Overlaying selectively backed-up Python files without deleting unbacked files..."
        docker run --rm \
            -v "${volume_name}:/target" \
            -v "${source_path}:/source:ro" \
            alpine:3.22 sh -c \
            'cp -a /source/. /target/'
    fi
}

restore_live_snapshot() {
    local snapshot="${1:-latest}"
    local stage_host
    local was_running=false
    local apply_started=false
    local restore_complete=false
    local needs_rebuild=false
    local snapshot_partial=false

    echo "Staging snapshot ${snapshot} before applying it..."
    stage_host="$(stage_snapshot "${snapshot}" | tail -n1)"

    if [[ ! -d "${stage_host}" ]]; then
        echo "ERROR: Staged restore directory was not created: ${stage_host}" >&2
        exit 1
    fi

    if snapshot_has_partial_policy "${stage_host}"; then
        snapshot_partial=true
        echo "Snapshot used an exclude pattern or file-size ceiling."
        echo "Restore will preserve unbacked live files and overlay backed-up content."
    fi

    if comfyui_running; then
        was_running=true
        echo "Stopping ComfyUI for live restore..."
        docker compose stop comfyui
    fi

    cleanup_live_restore() {
        if [[ "${restore_complete}" == true ]]; then
            return
        fi

        if [[ "${apply_started}" == false ]]; then
            echo "Restore aborted before live files were changed. Restarting previous services..." >&2
            has_backup_service && docker compose up -d backup >/dev/null 2>&1 || true
            if [[ "${was_running}" == true ]]; then
                docker compose up -d comfyui >/dev/null 2>&1 || true
            fi
        else
            echo "ERROR: Restore stopped after live files began changing." >&2
            echo "Services are being left stopped to avoid starting a partially restored deployment." >&2
            echo "The pre-restore safety snapshot remains available in the backup repository." >&2
        fi
    }
    trap cleanup_live_restore EXIT INT TERM HUP

    echo "Creating a pre-restore safety snapshot of the current state..."
    backup_command backup-now

    echo "Stopping automatic backup scheduler during restore..."
    docker compose stop backup

    apply_started=true

    # Repository/configuration data is always overlaid rather than deleting the
    # current checkout. Portable fresh-clone recovery can later use the recorded
    # repository commit as the authoritative tracked-source state.
    if [[ -d "${stage_host}/source/repo" ]]; then
        echo "Restoring backed-up deployment configuration..."
        overlay_tree "${stage_host}/source/repo" "${repo_root}"
        needs_rebuild=true
    fi

    local data_path models_path workflows_path extra_models_path
    data_path="$(resolve_host_path "$(env_get COMFYUI_DATA_PATH ./data)" "${repo_root}")"
    models_path="$(resolve_host_path "$(env_get COMFYUI_MODELS_PATH ./data/models)" "${repo_root}")"
    workflows_path="$(resolve_host_path "$(env_get COMFYUI_WORKFLOWS_PATH ./data/workflows)" "${repo_root}")"
    extra_models_path="$(env_get COMFYUI_EXTRA_MODELS_PATH "")"
    if [[ -n "${extra_models_path}" ]]; then
        extra_models_path="$(resolve_host_path "${extra_models_path}" "${repo_root}")"
    fi

    # Fully included categories with no exclusion policy can be replaced to match
    # the snapshot. Selectively included or partially excluded categories are
    # overlaid later so files that were never backed up are never deleted.
    restore_complete_tree_if_applicable \
        "${stage_host}" "${stage_host}/source/data/custom_nodes" "${data_path}/custom_nodes" \
        include_custom_nodes "custom nodes" "${snapshot_partial}"
    restore_complete_tree_if_applicable \
        "${stage_host}" "${stage_host}/source/data/user" "${data_path}/user" \
        include_user "ComfyUI and Manager user state" "${snapshot_partial}"
    restore_complete_tree_if_applicable \
        "${stage_host}" "${stage_host}/source/data/input" "${data_path}/input" \
        include_input "input images" "${snapshot_partial}"
    restore_complete_tree_if_applicable \
        "${stage_host}" "${stage_host}/source/data/output" "${data_path}/output" \
        include_output "output images" "${snapshot_partial}"

    if [[ -d "${stage_host}/source/data" ]]; then
        echo "Overlaying backed-up ComfyUI data and selective data includes..."
        overlay_tree "${stage_host}/source/data" "${data_path}"
        mkdir -p "${data_path}/user/default"
    fi

    restore_complete_tree_if_applicable \
        "${stage_host}" "${stage_host}/source/workflows" "${workflows_path}" \
        include_workflows "workflows" "${snapshot_partial}"
    if [[ -d "${stage_host}/source/workflows" ]]; then
        echo "Overlaying backed-up workflows and selective workflow includes..."
        overlay_tree "${stage_host}/source/workflows" "${workflows_path}"
    fi

    restore_complete_tree_if_applicable \
        "${stage_host}" "${stage_host}/source/models" "${models_path}" \
        include_models "writable model library" "${snapshot_partial}"
    if [[ -d "${stage_host}/source/models" ]]; then
        echo "Overlaying backed-up writable models without deleting unbacked models..."
        overlay_tree "${stage_host}/source/models" "${models_path}"
    fi

    if [[ -d "${stage_host}/source/extra-models" ]]; then
        if [[ -z "${extra_models_path}" ]]; then
            echo "ERROR: Snapshot contains extra models but restored COMFYUI_EXTRA_MODELS_PATH is not configured." >&2
            exit 1
        fi
        restore_complete_tree_if_applicable \
            "${stage_host}" "${stage_host}/source/extra-models" "${extra_models_path}" \
            include_extra_models "extra/legacy model library" "${snapshot_partial}"
        echo "Overlaying backed-up extra/legacy models without deleting unbacked models..."
        overlay_tree "${stage_host}/source/extra-models" "${extra_models_path}"
    fi

    if [[ -n "${extra_models_path}" ]]; then
        mkdir -p "${models_path}/external"
    fi

    if [[ "${needs_rebuild}" == true ]]; then
        echo "Rebuilding ComfyUI from restored deployment configuration..."
        docker compose build comfyui
    fi

    if [[ -d "${stage_host}/source/python" ]]; then
        if blueprint_true "${stage_host}" include_python false && \
            [[ "${snapshot_partial}" == false ]]; then
            restore_python_volume "${stage_host}/source/python" replace
        else
            restore_python_volume "${stage_host}/source/python" overlay
        fi
    else
        echo "Python volume was not included in this snapshot; leaving the current Python environment in place."
    fi

    echo "Recreating and starting restored ComfyUI deployment..."
    docker compose up -d --force-recreate comfyui

    if has_backup_service; then
        echo "Starting backup service..."
        docker compose up -d backup
    fi

    restore_complete=true
    trap - EXIT INT TERM HUP

    echo
    echo "Restore complete. Live deployment was restored from snapshot ${snapshot}."
    echo "Pre-restore safety snapshot was created before live files were changed."
    echo "Staged snapshot copy remains at: ${stage_host}"
    echo
    docker compose ps
}

command="${1:-help}"
shift || true

case "${command}" in
    status)
        docker compose ps -a backup backup-init
        echo
        docker compose logs --tail=50 backup
        ;;
    now)
        require_backup_running
        run_consistent_manual_backup
        ;;
    list)
        require_backup_running
        backup_command snapshots
        ;;
    inspect)
        require_backup_running
        if [[ $# -lt 1 || $# -gt 2 ]]; then
            echo "Usage: scripts/backup.sh inspect SNAPSHOT [PATH]" >&2
            exit 2
        fi
        docker compose exec -T backup restic ls "$@"
        ;;
    stage)
        require_backup_running
        if [[ $# -gt 1 ]]; then
            echo "Usage: scripts/backup.sh stage [SNAPSHOT]" >&2
            exit 2
        fi
        echo "Staged snapshot at: $(stage_snapshot "${1:-latest}" | tail -n1)"
        echo "The live deployment was not changed."
        ;;
    restore)
        require_backup_running
        if [[ $# -gt 1 ]]; then
            echo "Usage: scripts/backup.sh restore [SNAPSHOT]" >&2
            exit 2
        fi
        restore_live_snapshot "${1:-latest}"
        ;;
    check)
        require_backup_running
        backup_command check
        ;;
    maintenance)
        require_backup_running
        backup_command maintenance
        ;;
    logs)
        docker compose logs -f backup
        ;;
    help|-h|--help)
        usage
        ;;
    *)
        echo "ERROR: Unknown backup command: ${command}" >&2
        usage >&2
        exit 2
        ;;
esac
