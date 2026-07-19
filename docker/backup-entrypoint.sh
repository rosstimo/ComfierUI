#!/bin/sh
set -eu

log() {
    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

is_true() {
    case "${1:-}" in
        1|true|TRUE|yes|YES|on|ON) return 0 ;;
        *) return 1 ;;
    esac
}

require_uint() {
    name="$1"
    value="$2"
    case "$value" in
        ''|*[!0-9]*)
            log "ERROR: ${name} must be a non-negative integer, got: ${value}"
            exit 2
            ;;
    esac
}

backup_tag="${COMFYUI_BACKUP_TAG:-comfierui}"
interval_hours="${COMFYUI_BACKUP_INTERVAL_HOURS:-24}"
start_delay_minutes="${COMFYUI_BACKUP_START_DELAY_MINUTES:-10}"
retry_minutes="${COMFYUI_BACKUP_RETRY_MINUTES:-60}"
state_dir=/backups/state
restore_root=/backups/restore
password_file="${RESTIC_PASSWORD_FILE:-/backups/restic-password}"
last_success_file="${state_dir}/last-success-epoch"
manifest_file="${state_dir}/recovery-manifest.txt"
blueprint_dir="${state_dir}/recovery-blueprint"
include_file="${COMFYUI_BACKUP_INCLUDE_FILE:-/config/backup-includes.txt}"
exclude_file="${COMFYUI_BACKUP_EXCLUDE_FILE:-/config/backup-excludes.txt}"
exclude_larger_than="${COMFYUI_BACKUP_EXCLUDE_LARGER_THAN:-}"

ensure_backup_layout() {
    mkdir -p /backups/restic /backups/cache /backups/home "${state_dir}" "${restore_root}"

    if [ ! -s "${password_file}" ]; then
        old_umask="$(umask)"
        umask 077
        od -An -N32 -tx1 /dev/urandom | tr -d ' \n' > "${password_file}"
        chmod 600 "${password_file}"
        umask "${old_umask}"
        log "Created backup password: ${password_file}"
        log "IMPORTANT: Copy this password somewhere safe outside this backup directory."
    fi

    if [ ! -f /backups/restic/config ]; then
        log "Initializing encrypted restic repository at /backups/restic"
        restic init
    fi

    restic snapshots --json >/dev/null
}

add_source() {
    source_list="$1"
    path="$2"
    required="${3:-false}"

    if [ -e "${path}" ]; then
        printf '%s\n' "${path}" >> "${source_list}"
    elif is_true "${required}"; then
        log "ERROR: Required backup source is missing: ${path}"
        return 1
    else
        log "WARN: Optional backup source is missing: ${path}"
    fi
}

add_configured_sources() {
    source_list="$1"
    [ -r "${include_file}" ] || return 0

    while IFS= read -r path || [ -n "${path}" ]; do
        case "${path}" in
            ''|\#*) continue ;;
            /source/*)
                add_source "${source_list}" "${path}" false
                ;;
            *)
                log "ERROR: Custom backup include paths must stay under /source: ${path}"
                return 1
                ;;
        esac
    done < "${include_file}"
}

write_manifest() {
    cat > "${manifest_file}" <<EOF
created_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
backup_tag=${backup_tag}
compose_files=${COMFYUI_BACKUP_COMPOSE_FILES:-compose.yaml}
include_config=${COMFYUI_BACKUP_INCLUDE_CONFIG:-true}
include_custom_nodes=${COMFYUI_BACKUP_INCLUDE_CUSTOM_NODES:-true}
include_user=${COMFYUI_BACKUP_INCLUDE_USER:-true}
include_workflows=${COMFYUI_BACKUP_INCLUDE_WORKFLOWS:-true}
include_input=${COMFYUI_BACKUP_INCLUDE_INPUT:-false}
include_output=${COMFYUI_BACKUP_INCLUDE_OUTPUT:-false}
include_models=${COMFYUI_BACKUP_INCLUDE_MODELS:-false}
include_extra_models=${COMFYUI_BACKUP_INCLUDE_EXTRA_MODELS:-false}
include_python=${COMFYUI_BACKUP_INCLUDE_PYTHON:-false}
exclude_larger_than=${exclude_larger_than}
EOF
}

build_source_list() {
    source_list="$1"
    : > "${source_list}"
    write_manifest
    add_source "${source_list}" "${manifest_file}" true

    /bin/sh /usr/local/bin/comfierui-recovery-blueprint "${blueprint_dir}"
    add_source "${source_list}" "${blueprint_dir}" true

    if is_true "${COMFYUI_BACKUP_INCLUDE_CONFIG:-true}"; then
        add_source "${source_list}" /source/repo/.env true
        add_source "${source_list}" /source/repo/Dockerfile true
        add_source "${source_list}" /source/repo/docker true

        old_ifs="${IFS}"
        IFS=':'
        for compose_file in ${COMFYUI_BACKUP_COMPOSE_FILES:-compose.yaml}; do
            [ -n "${compose_file}" ] || continue
            case "${compose_file}" in
                /*)
                    log "WARN: Skipping absolute Compose path not mounted inside backup container: ${compose_file}"
                    ;;
                *)
                    add_source "${source_list}" "/source/repo/${compose_file}" true
                    ;;
            esac
        done
        IFS="${old_ifs}"

        add_source "${source_list}" /source/repo/config/extra_model_paths.yaml false
    fi

    is_true "${COMFYUI_BACKUP_INCLUDE_CUSTOM_NODES:-true}" && add_source "${source_list}" /source/data/custom_nodes true
    is_true "${COMFYUI_BACKUP_INCLUDE_USER:-true}" && add_source "${source_list}" /source/data/user true
    is_true "${COMFYUI_BACKUP_INCLUDE_WORKFLOWS:-true}" && add_source "${source_list}" /source/workflows true
    is_true "${COMFYUI_BACKUP_INCLUDE_INPUT:-false}" && add_source "${source_list}" /source/data/input false
    is_true "${COMFYUI_BACKUP_INCLUDE_OUTPUT:-false}" && add_source "${source_list}" /source/data/output false
    is_true "${COMFYUI_BACKUP_INCLUDE_MODELS:-false}" && add_source "${source_list}" /source/models true

    if is_true "${COMFYUI_BACKUP_INCLUDE_EXTRA_MODELS:-false}"; then
        if [ -n "${COMFYUI_EXTRA_MODELS_PATH_CONFIGURED:-}" ]; then
            add_source "${source_list}" /source/extra-models true
        else
            log "WARN: Extra-model backup requested, but COMFYUI_EXTRA_MODELS_PATH is not configured; skipping."
        fi
    fi

    is_true "${COMFYUI_BACKUP_INCLUDE_PYTHON:-false}" && add_source "${source_list}" /source/python true

    add_configured_sources "${source_list}"

    if [ ! -s "${source_list}" ]; then
        log "ERROR: Backup source list is empty. Enable at least one COMFYUI_BACKUP_INCLUDE_* option or add a custom include path."
        return 1
    fi
}

apply_retention() {
    keep_last="${COMFYUI_BACKUP_KEEP_LAST:-3}"
    keep_daily="${COMFYUI_BACKUP_KEEP_DAILY:-7}"
    keep_weekly="${COMFYUI_BACKUP_KEEP_WEEKLY:-4}"
    keep_monthly="${COMFYUI_BACKUP_KEEP_MONTHLY:-12}"
    keep_yearly="${COMFYUI_BACKUP_KEEP_YEARLY:-3}"

    for pair in \
        "COMFYUI_BACKUP_KEEP_LAST:${keep_last}" \
        "COMFYUI_BACKUP_KEEP_DAILY:${keep_daily}" \
        "COMFYUI_BACKUP_KEEP_WEEKLY:${keep_weekly}" \
        "COMFYUI_BACKUP_KEEP_MONTHLY:${keep_monthly}" \
        "COMFYUI_BACKUP_KEEP_YEARLY:${keep_yearly}"; do
        require_uint "${pair%%:*}" "${pair#*:}"
    done

    log "Applying retention: last=${keep_last}, daily=${keep_daily}, weekly=${keep_weekly}, monthly=${keep_monthly}, yearly=${keep_yearly}"
    restic forget \
        --tag "${backup_tag}" \
        --group-by host,tags \
        --keep-last "${keep_last}" \
        --keep-daily "${keep_daily}" \
        --keep-weekly "${keep_weekly}" \
        --keep-monthly "${keep_monthly}" \
        --keep-yearly "${keep_yearly}" \
        --prune
}

run_backup() {
    apply_retention_after="${1:-true}"
    source_list="$(mktemp)"
    trap 'rm -f "${source_list}"' INT TERM HUP EXIT

    build_source_list "${source_list}"

    log "Starting encrypted backup. Selected sources:"
    sed 's/^/  /' "${source_list}"
    if [ -r "${exclude_file}" ]; then
        log "Using backup exclude policy: ${exclude_file}"
    fi
    if [ -n "${exclude_larger_than}" ]; then
        log "Excluding files larger than: ${exclude_larger_than}"
    fi
    log "WARNING: Workflows can contain API keys/tokens. Images can embed workflow metadata. Protect the backup repository and password."

    set -- backup \
        --files-from-verbatim "${source_list}" \
        --tag "${backup_tag}" \
        --host comfierui \
        --group-by host,tags

    if [ -r "${exclude_file}" ]; then
        set -- "$@" --exclude-file "${exclude_file}"
    fi
    if [ -n "${exclude_larger_than}" ]; then
        set -- "$@" --exclude-larger-than "${exclude_larger_than}"
    fi

    restic "$@"

    if is_true "${apply_retention_after}"; then
        apply_retention
    else
        log "Retention deferred for this manual/safety snapshot. Automatic backups or 'maintenance' will apply the configured policy later."
    fi
    date +%s > "${last_success_file}"
    log "Backup complete."

    rm -f "${source_list}"
    trap - INT TERM HUP EXIT
}

resolve_latest_snapshot() {
    restic snapshots --tag "${backup_tag}" --json \
        | jq -r 'if length == 0 then empty else max_by(.time).id end'
}

stage_snapshot() {
    requested="${1:-latest}"
    target="${2:-}"

    if [ "${requested}" = latest ]; then
        requested="$(resolve_latest_snapshot)"
        if [ -z "${requested}" ]; then
            log "ERROR: No snapshots found with tag ${backup_tag}."
            exit 1
        fi
        log "Resolved latest snapshot: ${requested}"
    fi

    if [ -z "${target}" ]; then
        target="${restore_root}/$(date -u +%Y%m%dT%H%M%SZ)"
    fi

    case "${target}" in
        /backups/restore|/backups/restore/*) ;;
        *)
            log "ERROR: Built-in staging targets must stay under /backups/restore."
            exit 2
            ;;
    esac

    if [ -d "${target}" ] && [ -n "$(find "${target}" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
        log "ERROR: Staging target is not empty: ${target}"
        exit 1
    fi

    mkdir -p "${target}"
    restic restore "${requested}" --target "${target}"
    log "Snapshot staged for inspection: ${target}"
    log "The live deployment was not modified."
}

run_daemon() {
    require_uint COMFYUI_BACKUP_INTERVAL_HOURS "${interval_hours}"
    require_uint COMFYUI_BACKUP_START_DELAY_MINUTES "${start_delay_minutes}"
    require_uint COMFYUI_BACKUP_RETRY_MINUTES "${retry_minutes}"

    if [ "${interval_hours}" -eq 0 ]; then
        log "ERROR: COMFYUI_BACKUP_INTERVAL_HOURS must be at least 1."
        exit 2
    fi

    interval_seconds=$((interval_hours * 3600))
    start_delay_seconds=$((start_delay_minutes * 60))
    retry_seconds=$((retry_minutes * 60))

    log "Automatic backups enabled: every ${interval_hours} hour(s)."
    log "Retention defaults: last=${COMFYUI_BACKUP_KEEP_LAST:-3}, daily=${COMFYUI_BACKUP_KEEP_DAILY:-7}, weekly=${COMFYUI_BACKUP_KEEP_WEEKLY:-4}, monthly=${COMFYUI_BACKUP_KEEP_MONTHLY:-12}, yearly=${COMFYUI_BACKUP_KEEP_YEARLY:-3}."
    log "Backup data lives under /backups. Keep the password somewhere independent if these backups matter."

    while :; do
        now="$(date +%s)"
        if [ -s "${last_success_file}" ]; then
            last="$(cat "${last_success_file}" 2>/dev/null || echo 0)"
            case "${last}" in ''|*[!0-9]*) last=0 ;; esac
        else
            last=0
        fi

        if [ "${last}" -eq 0 ]; then
            wait_seconds="${start_delay_seconds}"
            log "No previous successful built-in backup recorded. First backup starts in ${start_delay_minutes} minute(s)."
        else
            next=$((last + interval_seconds))
            if [ "${next}" -gt "${now}" ]; then
                wait_seconds=$((next - now))
            else
                wait_seconds=0
            fi
        fi

        if [ "${wait_seconds}" -gt 0 ]; then
            sleep "${wait_seconds}"
        fi

        if ! run_backup true; then
            log "ERROR: Backup failed. Retrying in ${retry_minutes} minute(s)."
            sleep "${retry_seconds}"
        fi
    done
}

ensure_backup_layout

command="${1:-daemon}"
shift || true
case "${command}" in
    daemon) run_daemon ;;
    backup-now) run_backup false ;;
    snapshots) restic snapshots --tag "${backup_tag}" ;;
    stage) stage_snapshot "${1:-latest}" "${2:-}" ;;
    check) restic check ;;
    maintenance) apply_retention ;;
    *)
        echo "Usage: $0 [daemon|backup-now|snapshots|stage [SNAPSHOT] [TARGET]|check|maintenance]" >&2
        exit 2
        ;;
esac
