#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

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
  restore [SNAPSHOT]     Restore a snapshot to a new staging directory
                         (defaults to latest)
  check                  Verify the restic repository structure
  maintenance            Apply configured retention and prune old data
  logs                   Follow backup service logs

Examples:
  bash scripts/backup.sh now
  bash scripts/backup.sh list
  bash scripts/backup.sh inspect a1b2c3d4
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
    restore)
        require_backup_running
        if [[ $# -gt 1 ]]; then
            echo "Usage: scripts/backup.sh restore [SNAPSHOT]" >&2
            exit 2
        fi
        backup_command restore "${1:-latest}"
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
